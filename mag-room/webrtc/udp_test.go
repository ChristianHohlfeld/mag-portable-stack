package main

import (
	"bytes"
	"flag"
	"fmt"
	"net"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/pion/webrtc/v4"
)

func TestAuthenticatedDatagram(t *testing.T) {
	key := []byte("a-test-authentication-secret-123456")
	in := []byte(`{"kind":"pointer","x":240,"y":72}`)
	data := encode(key, 3, 19, in)
	p, err := authenticate(data, key)
	if err != nil || p.kind != 3 || p.seq != 19 || p.index != 0 || p.total != 1 || !bytes.Equal(p.body, in) {
		t.Fatalf("round trip: %+v %v", p, err)
	}
	data[9] ^= 0xff
	if _, err := authenticate(data, key); err == nil {
		t.Fatal("tampered packet accepted")
	}
	data = encode(key, 3, 19, in)
	if _, err := authenticate(data, []byte("wrong-secret-xxxxxxxxxxxxxxxxxx")); err == nil {
		t.Fatal("wrong HMAC key accepted")
	}
}
func TestTailnetIPv4(t *testing.T) {
	if !tailnetIPv4(net.ParseIP("100.64.0.1")) || !tailnetIPv4(net.ParseIP("100.127.255.255")) || !tailnetIPv4(net.ParseIP("100.123.114.16")) {
		t.Fatal("tailnet range")
	}
	for _, s := range []string{"100.63.255.255", "100.128.0.1", "192.168.0.65", "10.0.0.1", ""} {
		if tailnetIPv4(net.ParseIP(s)) {
			t.Fatal(s)
		}
	}
}

func TestTailnetAddressIsAdvertisedWithoutReplacingHost(t *testing.T) {
	setting := webrtc.SettingEngine{}
	setting.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	if err := setting.SetEphemeralUDPPortRange(18840, 18890); err != nil {
		t.Fatal(err)
	}
	setting.SetNAT1To1IPs([]string{"100.64.0.8"}, webrtc.ICECandidateTypeSrflx)
	pc, err := webrtc.NewAPI(webrtc.WithSettingEngine(setting)).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	if _, err := pc.CreateDataChannel("control", nil); err != nil {
		t.Fatal(err)
	}
	offer, err := pc.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := pc.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	<-webrtc.GatheringCompletePromise(pc)
	sdp := pc.LocalDescription().SDP
	if !strings.Contains(sdp, "100.64.0.8") || !strings.Contains(sdp, "typ srflx") {
		t.Fatalf("tailnet candidate missing:\n%s", sdp)
	}
	if !strings.Contains(sdp, "typ host") {
		t.Fatalf("LAN host candidate was replaced:\n%s", sdp)
	}
}

func TestMalformedDatagram(t *testing.T) {
	key := []byte("test-hmac-xxxxxxxxxxxxxxxxxxxxxxx")
	for _, s := range [][]byte{{}, []byte("MGB1"), make([]byte, 1500)} {
		if _, err := authenticate(s, key); err == nil {
			t.Fatal("malformed packet accepted")
		}
	}
}

// mag-test-timeout: 60
func TestMain(m *testing.M) {
	flag.Parse()
	raw := flag.Lookup("test.timeout").Value.String()
	d, err := time.ParseDuration(raw)
	if err != nil || d <= 0 || d > 2*time.Minute {
		fmt.Fprintf(os.Stderr, "go test -timeout must be set and at most 2m, got %q\n", raw)
		os.Exit(1)
	}
	os.Exit(m.Run())
}
