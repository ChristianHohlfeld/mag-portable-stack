// MAG browser hot view: UDP from each isolated Chromium VM to WebRTC/UDP.
// One Pion PeerConnection per viewer, one existing browser per VM.
// No transcoding, no browser restarts and no shared Chromium profile.
package main

import (
 "crypto/hmac"
 "crypto/sha256"
 "encoding/binary"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "log"
 "net"
 "net/http"
 "os"
 "strings"
 "sync"
 "sync/atomic"
 "time"

 "github.com/pion/webrtc/v4"
)

const listenPort = 18783
const controlVMPort = 9227
const maxImage = 256000
const maxParts = 256
const chunkSize = 1000
const frameChunk = 14000

type packet struct {
 kind byte
 seq uint32
 index, total int
 body []byte
}
func authenticate(raw []byte, secret []byte) (packet,error) {
 if len(raw)<29 || len(raw)>1400 || string(raw[:4])!="MGB1" {return packet{},errors.New("bad packet")}
 mac:=hmac.New(sha256.New,secret);mac.Write(raw[:len(raw)-16])
 if !hmac.Equal(mac.Sum(nil)[:16],raw[len(raw)-16:]) {return packet{},errors.New("bad authentication")}
 p:=packet{kind:raw[4],seq:binary.BigEndian.Uint32(raw[5:9]),index:int(binary.BigEndian.Uint16(raw[9:11])),total:int(binary.BigEndian.Uint16(raw[11:13])),body:raw[13:len(raw)-16]}
 if p.total<1||p.total>maxParts||p.index>=p.total{return packet{},errors.New("bad fragments")}
 return p,nil
}
func encode(secret []byte, kind byte, seq uint32, body []byte) []byte {
 p:=make([]byte,13,13+len(body)+16)
 copy(p[:4],"MGB1");p[4]=kind;binary.BigEndian.PutUint32(p[5:9],seq)
 binary.BigEndian.PutUint16(p[11:13],1)
 p=append(p,body...)
 mac:=hmac.New(sha256.New,secret);mac.Write(p)
 return append(p,mac.Sum(nil)[:16]...)
}
type fragments struct {
 seq uint32
 data [][]byte
 received int
 deadline time.Time
}
type peer struct {
 conn *webrtc.PeerConnection
 frames *webrtc.DataChannel
 closed atomic.Bool
}
type bot struct {
 vm string
 addr *net.UDPAddr
 seen time.Time
 frame fragments
 peers map[*peer]struct{}
}
type relay struct {
 mu sync.Mutex
 udp *net.UDPConn
 keys sync.Map // VM name -> private HMAC secret
 bots map[string]*bot
 addresses map[string]string
 seq atomic.Uint32
}
func newRelay()(*relay,error) {
 addr:=&net.UDPAddr{IP:net.IPv4zero,Port:listenPort}
 udp,err:=net.ListenUDP("udp4",addr)
 if err!=nil{return nil,err}
 r:=&relay{udp:udp,bots:make(map[string]*bot),addresses:make(map[string]string)}
 // Start with an unpredictable sequence. Signed subscriptions can reset
 // the bot's control sequence after relay restart without resetting tabs.
 r.seq.Store(uint32(time.Now().UnixNano()))
 return r,nil
}
func (r *relay) key(vm string)([]byte,bool){
 if len(vm)<1||len(vm)>32||strings.ContainsAny(vm,"/\\ \n\r\t"){return nil,false}
 if value,ok:=r.keys.Load(vm);ok{return value.([]byte),true}
 raw,err:=os.ReadFile(os.Getenv("MAG_ROOM_STATE")+"/browser-udp-keys/"+vm+".key")
 if err!=nil{return nil,false}
 secret:=[]byte(strings.TrimSpace(string(raw)))
 if len(secret)!=64{return nil,false}
 r.keys.Store(vm,secret)
 return secret,true
}
func (r *relay) send(vm string, message []byte) {
 r.mu.Lock()
 b:=r.bots[vm]
 if b==nil || time.Since(b.seen)>10*time.Second {r.mu.Unlock();return}
 target:=*b.addr
 target.Port=controlVMPort
 r.mu.Unlock()
 secret,ok:=r.key(vm)
 if !ok{return}
 p:=encode(secret,3,r.seq.Add(1),message)
 _,_=r.udp.WriteToUDP(p,&target)
}
func (r *relay) subscribe(vm string){r.send(vm,[]byte(`{"kind":"subscribe"}`))}
func (r *relay) receive() {
 buf:=make([]byte,1400)
 for {
  n,addr,err:=r.udp.ReadFromUDP(buf);if err!=nil{log.Printf("browser UDP recv: %v",err);return}
  // The browser guest network is the only authorized source.
  ip:=addr.IP.To4()
  if ip==nil||ip[0]!=192||ip[1]!=168||ip[2]!=122 {continue}
  if n<29 || string(buf[:4])!="MGB1" {continue}
  var vm string
  if buf[4]==1 {vm=string(buf[13:n-16])}
  if buf[4]==2 {
   r.mu.Lock()
   vm=r.addresses[addr.IP.String()]
   r.mu.Unlock()
  }
  secret,ok:=r.key(vm);if !ok{continue}
  p,err:=authenticate(buf[:n],secret);if err!=nil{continue}
  if p.kind==1 {
   if string(p.body)!=vm {continue}
   r.mu.Lock()
   b:=r.bots[vm]
   if b==nil{b=&bot{vm:vm,peers:make(map[*peer]struct{})};r.bots[vm]=b}
   b.addr=addr;b.seen=time.Now()
   r.addresses[addr.IP.String()]=vm
   r.mu.Unlock()
   continue
  }
  if p.kind!=2 {continue}
  r.mu.Lock()
  b:=r.bots[vm]
  if b==nil||time.Since(b.seen)>10*time.Second{r.mu.Unlock();continue}
  if b.frame.seq!=p.seq {
   b.frame=fragments{seq:p.seq,data:make([][]byte,p.total),deadline:time.Now().Add(time.Second)}
  }
  f:=&b.frame
  if f.seq!=p.seq||len(f.data)!=p.total||time.Now().After(f.deadline) {r.mu.Unlock();continue}
  if f.data[p.index]==nil {
   f.data[p.index]=append([]byte{},p.body...)
   f.received++
  }
  if f.received!=p.total{r.mu.Unlock();continue}
  size:=0
  for _,v:=range f.data {size+=len(v)}
  if size<4||size>maxImage{b.frame=fragments{};r.mu.Unlock();continue}
  jpeg:=make([]byte,0,size)
  for _,v:=range f.data {jpeg=append(jpeg,v...)}
  b.frame=fragments{}
  receivers:=make([]*peer,0,len(b.peers))
  for p:=range b.peers {receivers=append(receivers,p)}
  r.mu.Unlock()
  if len(jpeg)<4||jpeg[0]!=0xff||jpeg[1]!=0xd8{continue}
  for _,viewer:=range receivers {
   if viewer.closed.Load() || viewer.frames.ReadyState()!=webrtc.DataChannelStateOpen ||
      viewer.frames.BufferedAmount()>1000000 {continue}
   parts:=(len(jpeg)+frameChunk-1)/frameChunk
   if parts>32{continue}
   for i:=0;i<parts;i++ {
    end:=(i+1)*frameChunk
    if end>len(jpeg){end=len(jpeg)}
    out:=make([]byte,8,8+end-i*frameChunk)
    binary.BigEndian.PutUint32(out[0:4],p.seq)
    binary.BigEndian.PutUint16(out[4:6],uint16(i))
    binary.BigEndian.PutUint16(out[6:8],uint16(parts))
    out=append(out,jpeg[i*frameChunk:end]...)
    if err:=viewer.frames.Send(out);err!=nil{break}
   }
  }
 }
}
func (r *relay) resolveVM(id string)(string,error) {
 f,err:=os.ReadFile(os.Getenv("MAG_ROOM_STATE")+"/agents.json")
 if err!=nil{return "",fmt.Errorf("agents: %w",err)}
 var agents []struct{ID string `json:"id"`; VM string `json:"vm"`}
 if err=json.Unmarshal(f,&agents);err!=nil{return "",err}
 for _,a:=range agents {
  if id==a.ID||id==a.VM{
   if a.VM=="" {return "",errors.New("bot has no VM")}
   return a.VM,nil
  }
 }
 return "",errors.New("unknown bot")
}
func (r *relay) offer(w http.ResponseWriter,req *http.Request){
 if req.Method!="POST"{http.Error(w,"method",405);return}
 id:=req.URL.Query().Get("bot")
 vm,err:=r.resolveVM(id);if err!=nil {http.Error(w,err.Error(),404);return}
 var offer webrtc.SessionDescription
 if err=json.NewDecoder(io.LimitReader(req.Body,65536)).Decode(&offer);err!=nil||
    offer.Type!=webrtc.SDPTypeOffer{http.Error(w,"invalid offer",400);return}
 setting:=webrtc.SettingEngine{}
 setting.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
 if err=setting.SetEphemeralUDPPortRange(18840,18890);err!=nil{http.Error(w,"ICE ports",500);return}
 api:=webrtc.NewAPI(webrtc.WithSettingEngine(setting))
 pc,err:=api.NewPeerConnection(webrtc.Configuration{ICEServers:[]webrtc.ICEServer{}})
 if err!=nil {http.Error(w,"peer creation",500);return}
 viewer:=&peer{conn:pc}
 channel,err:=pc.CreateDataChannel("frames",&webrtc.DataChannelInit{Ordered:boolptr(false),MaxRetransmits:uint16ptr(0)})
 if err!=nil{_ =pc.Close();http.Error(w,"frames",500);return}
 viewer.frames=channel
 r.mu.Lock()
 b:=r.bots[vm]
 if b==nil {b=&bot{vm:vm,peers:make(map[*peer]struct{})};r.bots[vm]=b}
 b.peers[viewer]=struct{}{}
 r.mu.Unlock()
 pc.OnConnectionStateChange(func(s webrtc.PeerConnectionState){
  if s==webrtc.PeerConnectionStateFailed||s==webrtc.PeerConnectionStateClosed{
   viewer.closed.Store(true)
   r.mu.Lock();delete(b.peers,viewer);r.mu.Unlock()
   _=pc.Close()
  }
 })
 pc.OnDataChannel(func(dc *webrtc.DataChannel){
  if dc.Label()!="control"{return}
  dc.OnMessage(func(m webrtc.DataChannelMessage){
   if len(m.Data)>1024||!json.Valid(m.Data){return}
   var evt struct{Kind string `json:"kind"`}
   if json.Unmarshal(m.Data,&evt)!=nil{return}
   switch evt.Kind {
   case "subscribe":
    r.subscribe(vm)
   case "pointer","wheel","key","text":
    r.send(vm,m.Data)
   }
  })
 })
 if err=pc.SetRemoteDescription(offer);err!=nil{_ =pc.Close();http.Error(w,"remote SDP",400);return}
 answer,err:=pc.CreateAnswer(nil)
 if err!=nil{_ =pc.Close();http.Error(w,"answer SDP",500);return}
 gathered:=webrtc.GatheringCompletePromise(pc)
 if err=pc.SetLocalDescription(answer);err!=nil{_ =pc.Close();http.Error(w,"set SDP",500);return}
 select {case <-gathered: case <-time.After(10*time.Second):
  _=pc.Close();http.Error(w,"ICE timeout",504);return}
 w.Header().Set("Content-Type","application/json")
 _=json.NewEncoder(w).Encode(pc.LocalDescription())
}
func boolptr(v bool)*bool{return &v}
func uint16ptr(v uint16)*uint16{return &v}
func main(){
 state:=os.Getenv("MAG_ROOM_STATE")
 if state==""{state="/var/lib/magbot/room";_=os.Setenv("MAG_ROOM_STATE",state)}
 relay,err:=newRelay();if err!=nil{log.Fatal(err)}
 go relay.receive()
 mux:=http.NewServeMux()
 mux.HandleFunc("POST /offer",relay.offer)
 mux.HandleFunc("GET /status",func(w http.ResponseWriter,_ *http.Request){
  type status struct {VM string `json:"vm"`; SeenSeconds float64 `json:"seen_seconds"`; Viewers int `json:"viewers"`}
  all:=make([]status,0)
  relay.mu.Lock()
  for _,b:=range relay.bots{
   age:=time.Since(b.seen).Seconds()
   if b.seen.IsZero(){age=-1}
   all=append(all,status{VM:b.vm,SeenSeconds:age,Viewers:len(b.peers)})
  }
  relay.mu.Unlock()
  w.Header().Set("Content-Type","application/json")
  _=json.NewEncoder(w).Encode(map[string]any{"transport":"WebRTC/SCTP/UDP + authenticated VM UDP JPEG","bots":all})
 })
 mux.HandleFunc("GET /health",func(w http.ResponseWriter,_ *http.Request){
  w.Header().Set("Content-Type","text/plain");_,_=w.Write([]byte("ok\n"))
 })
 server:=&http.Server{Addr:"127.0.0.1:18784",Handler:mux,ReadHeaderTimeout:5*time.Second}
 log.Printf("MAG browser WebRTC/UDP gateway ready: localhost:18784 and UDP :18783")
 log.Fatal(server.ListenAndServe())
}
