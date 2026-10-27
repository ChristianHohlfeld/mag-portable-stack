package main

import (
	"bytes"
	"flag"
	"fmt"
	"os"
	"testing"
	"time"
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
