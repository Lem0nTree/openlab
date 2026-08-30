package control

import "testing"

func TestDuplicateJSONRejected(t *testing.T) {
	for _, raw := range []string{`{"hour":1,"hour":2}`, `{"nested":{"x":1,"x":2}}`, `[{"x":1,"x":2}]`} {
		var value any
		if DecodeStrict([]byte(raw), &value) == nil {
			t.Fatalf("accepted ambiguous JSON: %s", raw)
		}
	}
}
func TestComposeVersionGate(t *testing.T) {
	for version, want := range map[string]bool{"2.24.4": true, "v2.40.1\n": true, "v5.0.0": true, "2.24.3": false, "1.29.2": false, "unknown": false, "2.24.4-rc1": false} {
		if ComposeSupported(version) != want {
			t.Errorf("version %q", version)
		}
	}
}
func TestPrivateNetworkRequests(t *testing.T) {
	for _, address := range []string{"0.0.0.0", "8.8.8.8", "example.org", "127.0.0.1;id", "::"} {
		if (Request{Action: "bind", BindAddress: address, Port: 3000}).Validate() == nil {
			t.Errorf("accepted %s", address)
		}
	}
	if err := (Request{Action: "bind", BindAddress: "192.168.1.12", Port: 3000}).Validate(); err != nil {
		t.Fatal(err)
	}
	if (Request{Action: "restart", ManualFeature: true}).Validate() == nil {
		t.Fatal("feature permission escaped update action")
	}
}
