"""Contract tests for the native (C++) MAG model-routing gateway.
All HTTP servers are process-local fixtures. No model binaries or privileges needed."""
import contextlib
import grp, http.client, http.server, json, os, pathlib, socket, subprocess
import tempfile, threading, time, unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

class FakeModel(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ready = True
    calls = []
    def log_message(self, *_): pass
    def reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health": self.reply(200 if type(self).ready else 503, {"status":"ok" if type(self).ready else "loading"})
        else: self.reply(200, {"ok":True})
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0"))
        body=self.rfile.read(n)
        try: data=json.loads(body)
        except ValueError: data={}
        type(self).calls.append((self.path,data))
        if self.path == "/v1/chat/completions":
            if any(x.get("content") == "stream" for x in data.get("messages",[]) if isinstance(x,dict)):
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream")
                self.send_header("Transfer-Encoding","chunked")
                self.end_headers()
                for delta in ({"choices":[{"index":0,"delta":{"content":"Hallo ","reasoning_content":"denken "}}]},
                              {"choices":[{"index":0,"delta":{"content":"Welt","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"run_","arguments":"{\"a\":"}}]}}]},
                              {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"command","arguments":"1}"}}]},"finish_reason":"tool_calls"}]}):
                    payload=("data: "+json.dumps(delta)+"\n\n").encode()
                    self.wfile.write(("%X\r\n"%len(payload)).encode()+payload+b"\r\n")
                self.wfile.write(b"0\r\n\r\n");self.wfile.flush()
                return
            self.reply(200, {"choices":[{"message":{"role":"assistant","content":"antwort"},"finish_reason":"stop"}],"model":data.get("model")})
        else: self.reply(200,{"ok":True})

class Q4(FakeModel): ready=True;calls=[]
class IQ3(FakeModel): ready=True;calls=[]

def request(portno,method="GET",path="/health",body=None):
    c=http.client.HTTPConnection("127.0.0.1",portno,timeout=5)
    data=json.dumps(body) if body is not None else None
    c.request(method,path,body=data,headers={"Content-Type":"application/json"})
    r=c.getresponse(); raw=r.read();status=r.status;c.close()
    try: return status,json.loads(raw)
    except ValueError:return status,raw

def control(path,method="GET",body=None):
    payload=json.dumps(body).encode() if body else b""
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(3);s.connect(str(path))
    s.sendall((method+" /models HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: "+str(len(payload))+"\r\n\r\n").encode()+payload)
    raw=b""
    while True:
        x=s.recv(65536)
        if not x:break
        raw+=x
    s.close()
    status=int(raw.split(b"\r\n",1)[0].split()[1])
    return status,json.loads(raw.split(b"\r\n\r\n",1)[1])

class NativeGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="mag-audit-fixed-q4-")
        cls.dir=pathlib.Path(cls.temp.name)
        cls.q4p,cls.iq3p,cls.gwp=port(),port(),port()
        cls.q4=http.server.ThreadingHTTPServer(("127.0.0.1",cls.q4p),Q4)
        cls.iq3=http.server.ThreadingHTTPServer(("127.0.0.1",cls.iq3p),IQ3)
        for server in (cls.q4,cls.iq3):threading.Thread(target=server.serve_forever,daemon=True).start()
        cls.env=dict(os.environ,MAG_AUDIT_LISTEN_PORT=str(cls.gwp),MAG_MODEL_Q4_PORT=str(cls.q4p),
            MAG_MODEL_IQ3_PORT=str(cls.iq3p),MAG_AUDIT_LOG_DIR=str(cls.dir/"logs"),
            MAG_AUDIT_SECRETS_FILE=str(cls.dir/"missing-secrets"),
            MAG_MODEL_CONTROL_SOCKET=str(cls.dir/"control.sock"),
            MAG_MODEL_SUPERVISOR_SOCKET=str(cls.dir/"supervisor.sock"))
        cls.proc=subprocess.Popen([str(ROOT/"build/mag-audit-gateway")],env=cls.env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if cls.proc.poll() is not None:raise RuntimeError(cls.proc.stderr.read().decode())
            try:
                if request(cls.gwp)[0]==200:break
            except (ConnectionError,OSError):pass
            time.sleep(.05)
        else:raise RuntimeError("Gateway not ready")
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=3);cls.proc.stderr.close()
        for server in (cls.q4,cls.iq3):server.shutdown();server.server_close()
        cls.temp.cleanup()
    def test_01_iq3_routes_without_touching_q4(self):
        Q4.calls.clear();IQ3.calls.clear()
        code,j=request(self.gwp,"POST","/v1/chat/completions",{"model":"deepseek-v4-flash-0731-iq3","messages":[]})
        self.assertEqual(code,200)
        self.assertEqual(j["model"],"deepseek-v4-flash-0731-iq3")
        self.assertEqual(len(Q4.calls),0)
        self.assertEqual(len(IQ3.calls),1)
        self.assertEqual(IQ3.calls[0][1]["model"],"deepseek-v4-flash-0731-iq3")
        self.assertFalse((self.dir/"control.sock").exists())
        self.assertFalse((self.dir/"supervisor.sock").exists())
        code,listed=request(self.gwp,"GET","/v1/models")
        self.assertEqual(code,200)
        ids=sorted(row["id"] for row in listed["data"])
        self.assertEqual(ids,["deepseek-v4-flash-0731","deepseek-v4-flash-0731-iq3"])
        self.assertTrue(all(row["ready"] for row in listed["data"]))
    def test_02_no_switch_endpoint_on_gateway(self):
        Q4.calls.clear();IQ3.calls.clear()
        code,_=request(self.gwp,"PUT","/models",{"id":"iq3"})
        self.assertNotEqual(code,202)
        code,_=request(self.gwp,"POST","/v1/chat/completions",{"model":"x"})
        self.assertEqual(code,400,"unknown model must never fall back to Q4")
        self.assertEqual(Q4.calls,[])
        self.assertEqual(IQ3.calls,[])
    def test_03_sse_audit_hash_and_tool_calls(self):
        import sys
        code,_=request(self.gwp,"POST","/v1/chat/completions",
            {"model":"deepseek-v4-flash-0731","stream":True,"messages":[{"role":"user","content":"stream"}]})
        self.assertEqual(code,200)
        # The gateway appends the streamed response record after the client
        # already has the last chunk; wait for it instead of racing it.
        for _ in range(100):
            events=[]
            for f in (self.dir/"logs").rglob("events.jsonl"):
                for l in f.read_text().splitlines():
                    try:events.append(json.loads(l))
                    except ValueError:pass  # line still being written
            responses=[e for e in events if e.get("type")=="llm_response" and e.get("streamed")]
            if responses:break
            time.sleep(.05)
        self.assertTrue(responses)
        msg=responses[-1]["body"]["choices"][0]["message"]
        self.assertEqual(msg["content"],"Hallo Welt")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"],"run_command")
        self.assertEqual(responses[-1]["model_variant"],"q4")
        verify=subprocess.run([sys.executable,str(ROOT/"ops/audit/mag-audit-tail"),"--verify"],
          env=dict(os.environ,MAG_AUDIT_LOG_DIR=str(self.dir/"logs")),capture_output=True,text=True)
        self.assertEqual(verify.returncode,0,verify.stdout+verify.stderr)


    def test_04_qwen_routes_on_numa1_and_old_iq3_is_rejected(self):
        # Real compiled C++ gateway in qwen mode; same fake :8091 listener
        # represents the newly installed Qwen server. No Q4/IQ3 mutation.
        gwp=port()
        env=dict(self.env,MAG_AUDIT_LISTEN_PORT=str(gwp),MAG_SECONDARY_MODEL="qwen",
                 MAG_AUDIT_LOG_DIR=str(self.dir/"qwen-logs"))
        proc=subprocess.Popen([str(ROOT/"build/mag-audit-gateway")],env=env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError(proc.stderr.read().decode())
                try:
                    if request(gwp)[0]==200:break
                except (ConnectionError,OSError):pass
                time.sleep(.05)
            else:raise RuntimeError("Qwen gateway not ready")
            Q4.calls.clear();IQ3.calls.clear()
            code,listed=request(gwp,"GET","/v1/models")
            self.assertEqual(code,200)
            self.assertEqual(sorted(x["id"] for x in listed["data"]),
                             ["qwen3.8-flash-next-uncensored"])
            code,reply=request(gwp,"POST","/v1/chat/completions",
                               {"model":"qwen3.8-flash-next-uncensored","messages":[]})
            self.assertEqual(code,200)
            self.assertEqual(reply["model"],"qwen3.8-flash-next-uncensored")
            self.assertEqual(len(IQ3.calls),1,"the NUMA1 listener receives Qwen traffic")
            self.assertEqual(Q4.calls,[])
            code,_=request(gwp,"POST","/v1/chat/completions",
                           {"model":"deepseek-v4-flash-0731-iq3","messages":[]})
            self.assertEqual(code,400,"IQ3 must not silently fall back after cutover")
            self.assertEqual(len(IQ3.calls),1)
            code,reply=request(gwp,"POST","/v1/chat/completions",
                               {"model":"deepseek-v4-flash-0731","messages":[]})
            self.assertEqual(code,400)
            self.assertIn("DeepSeek ist stillgelegt",str(reply))
            self.assertEqual(Q4.calls,[])
            code,reply=request(gwp,"POST","/v1/chat/completions",
                               {"messages":[]})
            self.assertEqual(code,200,"model-less request routes Qwen on rollback")
            self.assertEqual(reply["model"],"qwen3.8-flash-next-uncensored")
            self.assertEqual(IQ3.calls[-1][1]["model"],"qwen3.8-flash-next-uncensored",
                             "gateway must inject the Qwen alias into model-less chat requests")
            self.assertEqual(Q4.calls,[],"rollback must never reach the retired Q4 upstream")
        finally:
            proc.terminate();proc.wait(timeout=3);proc.stderr.close()

    def test_06_numa_state_routes_swapped_models_to_their_node(self):
        # mag-numa-swap put IQ3 on NUMA0 and Qwen on NUMA1: traffic follows the state file.
        gwp=port();state=self.dir/"numa.json"
        state.write_text(json.dumps({"nodes":{"0":{"model":"deepseek-v4-flash-0731-iq3"},"1":{"model":"qwen3.8-flash-next-uncensored"}}}))
        env=dict(self.env,MAG_AUDIT_LISTEN_PORT=str(gwp),MAG_NUMA_STATE=str(state),MAG_AUDIT_LOG_DIR=str(self.dir/"numa-logs"))
        proc=subprocess.Popen([str(ROOT/"build/mag-audit-gateway")],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                try:
                    if request(gwp)[0]==200:break
                except (ConnectionError,OSError):pass
                time.sleep(.05)
            Q4.calls.clear();IQ3.calls.clear()
            code,_=request(gwp,"POST","/v1/chat/completions",{"model":"qwen3.8-flash-next-uncensored","messages":[]})
            self.assertEqual(code,200);self.assertEqual(len(IQ3.calls),1);self.assertEqual(Q4.calls,[])
            code,_=request(gwp,"POST","/v1/chat/completions",{"model":"deepseek-v4-flash-0731-iq3","messages":[]})
            self.assertEqual(code,200);self.assertEqual(len(Q4.calls),1)
            self.assertEqual(Q4.calls[0][1]["model"],"deepseek-v4-flash-0731-iq3")
        finally:
            proc.terminate();proc.wait(timeout=3);proc.stderr.close()
    def test_05_unknown_secondary_configuration_fails_closed(self):
        env=dict(self.env,MAG_AUDIT_LISTEN_PORT=str(port()),MAG_SECONDARY_MODEL="typo")
        process=subprocess.run([str(ROOT/"build/mag-audit-gateway")],env=env,
                               capture_output=True,timeout=5)
        self.assertNotEqual(process.returncode,0)

    @contextlib.contextmanager
    def configured_gateway(self):
        kinds=[type("DeepSeekNode%d"%n,(FakeModel,),{"ready":True,"calls":[]}) for n in (0,1)]
        servers=[];ports=[]
        for k in kinds:
            srv=http.server.ThreadingHTTPServer(("127.0.0.1",0),k)
            threading.Thread(target=srv.serve_forever,daemon=True).start();servers.append(srv);ports.append(srv.server_port)
        gwp=port()
        env=dict(self.env,MAG_AUDIT_LISTEN_PORT=str(gwp),MAG_ROOM_MODEL_ID="deepseek-v4-flash-0731",
                 MAG_ROOM_MODEL_LABEL="DeepSeek V4 Flash",MAG_ROOM_MODEL_PORTS="%d:0,%d:1"%tuple(ports),MAG_ROOM_MAX_BOTS="2",
                 MAG_AUDIT_LOG_DIR=str(self.dir/"numa-logs"))
        proc=subprocess.Popen([str(ROOT/"build/mag-audit-gateway")],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        def post(slot,path="/v1/chat/completions",body=None,model="deepseek-v4-flash-0731"):
            c=http.client.HTTPConnection("127.0.0.1",gwp,timeout=5)
            h={"Content-Type":"application/json"}
            if slot is not None:h["X-Mag-Slot"]=str(slot)
            c.request("POST",path,body=json.dumps(body if body is not None else {"model":model,"messages":[],"cache_prompt":True,"id_slot":99}),headers=h)
            r=c.getresponse();r.read();c.close();return r.status
        try:
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError(proc.stderr.read().decode())
                try:
                    if request(gwp)[0]==200:break
                except (ConnectionError,OSError):pass
                time.sleep(.05)
            Q4.calls.clear();IQ3.calls.clear()
            yield kinds,gwp,post
            self.assertEqual(Q4.calls+IQ3.calls,[],"configured mode never touches legacy backends")
        finally:
            proc.terminate();proc.wait(timeout=3);proc.stderr.close()
            for srv in servers:srv.shutdown();srv.server_close()

    def test_07_configured_sticky_backend_per_bot_slot(self):
        with self.configured_gateway() as (kinds,gwp,post):
            self.assertEqual([x["id"] for x in request(gwp,path="/v1/models")[1]["data"]],["deepseek-v4-flash-0731"])
            for _ in range(3):
                for slot in (1,0):self.assertEqual(post(slot),200)
            self.assertEqual([len(k.calls) for k in kinds],[3,3])
            for k in kinds:
                for _,body in k.calls:
                    self.assertEqual(body["model"],"deepseek-v4-flash-0731")
                    self.assertEqual(body["id_slot"],0)
                    self.assertTrue(body["cache_prompt"])
            for bad in ("2","4","x","-1",""):self.assertEqual(post(bad),400)
            for old in ("ornith-1.5-35b-a3b","qwen3.8-flash-next-uncensored","deepseek-v4-flash-0731-iq3"):
                self.assertEqual(post(0,model=old),400)
            self.assertEqual(post(None,model=""),200)

    def test_08_configured_single_slot_rpc_per_bot(self):
        with self.configured_gateway() as (kinds,gwp,post):
            for slot in (0,1):
                self.assertEqual(post(slot,"/slots/7?action=save",{"filename":"mag-prefix-deepseek-slot%d.bin"%(slot+1)}),200)
                self.assertEqual(post(slot,"/slots/3?action=restore",{"filename":"own.bin"}),200)
                self.assertEqual([p for p,_ in kinds[slot].calls],["/slots/0?action=save","/slots/0?action=restore"])
            self.assertEqual(post(None,"/slots/0?action=save",{"filename":"a.bin"}),400)
            self.assertEqual(post(1,"/slots/0?action=bogus",{}),400)

    def test_09_configured_bad_backend_spec_fails_closed(self):
        for spec in ("8191:4","8191:x","0:0,8192:1","8191:0,8192:0","8191:0,8191:1","8191:0,8192:1,8193:0"):
            env=dict(self.env,MAG_AUDIT_LISTEN_PORT=str(port()),MAG_ROOM_MODEL_ID="deepseek-v4-flash-0731",MAG_ROOM_MODEL_PORTS=spec)
            self.assertNotEqual(subprocess.run([str(ROOT/"build/mag-audit-gateway")],env=env,capture_output=True,timeout=5).returncode,0,spec)

    def test_10_configured_two_nodes_keep_files_separate(self):
        with self.configured_gateway() as (kinds,gwp,post):
            self.assertEqual(post(1,"/slots/0?action=restore",{"filename":"node1.bin"}),200)
            self.assertEqual(post(0,"/slots/0?action=save",{"filename":"node0.bin"}),200)
            self.assertEqual(kinds[1].calls[0][1]["filename"],"node1.bin")
            self.assertEqual(kinds[0].calls[0][1]["filename"],"node0.bin")

    def test_11_configured_defaults_agree(self):
        unit=(ROOT/"ops/audit/systemd/mag-audit-gateway.service").read_text()
        env=(ROOT/"ops/room/model.env").read_text()
        header=(ROOT/"mag-room/native/room_model.hpp").read_text()
        self.assertIn('EnvironmentFile=-/etc/mag-stack/room-model.env',unit)
        for text in (env,header):self.assertIn("8191:0,8192:1",text)
        self.assertIn('MAG_ROOM_MAX_BOTS=2',env)

if __name__=="__main__":unittest.main()
