package control

import (
	"bytes"
	"testing"
)

func TestProgressWriterRedactsSplitSecretsAndPrefixesLines(t *testing.T) {
	var terminal bytes.Buffer
	writer := &progressWriter{stderr: &terminal, secrets: []string{"secret-value"}}
	if _, err := writer.Write([]byte("layer one secret-")); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Write([]byte("value\nlayer two\n")); err != nil {
		t.Fatal(err)
	}
	writer.flush()
	if got := terminal.String(); got != "OpenLab:   layer one [redacted]\nOpenLab:   layer two\n" {
		t.Fatalf("unexpected terminal output: %q", got)
	}
	if bytes.Contains(terminal.Bytes(), []byte("secret-value")) {
		t.Fatal("secret reached terminal output")
	}
}

func TestProgressWriterFlushesPartialLine(t *testing.T) {
	var terminal bytes.Buffer
	writer := &progressWriter{stderr: &terminal}
	_, _ = writer.Write([]byte("pulling web"))
	writer.flush()
	if got := terminal.String(); got != "OpenLab:   pulling web\n" {
		t.Fatalf("unexpected flushed output: %q", got)
	}
}
