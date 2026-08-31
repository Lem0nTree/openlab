package control

import (
	"bytes"
	"fmt"
	"strings"
	"testing"
)

func TestProgressWriterRedactsSplitSecretsBeforeNotifying(t *testing.T) {
	var terminal bytes.Buffer
	writer := &progressWriter{notify: func(line string) { fmt.Fprintln(&terminal, line) }, secrets: []string{"secret-value"}}
	if _, err := writer.Write([]byte("layer one secret-")); err != nil {
		t.Fatal(err)
	}
	if _, err := writer.Write([]byte("value\nlayer two\n")); err != nil {
		t.Fatal(err)
	}
	writer.flush()
	if got := terminal.String(); got != "layer one [redacted]\nlayer two\n" {
		t.Fatalf("unexpected terminal output: %q", got)
	}
	if bytes.Contains(terminal.Bytes(), []byte("secret-value")) {
		t.Fatal("secret reached terminal output")
	}
}

func TestProgressWriterFlushesPartialLine(t *testing.T) {
	var terminal bytes.Buffer
	writer := &progressWriter{notify: func(line string) { fmt.Fprintln(&terminal, line) }}
	_, _ = writer.Write([]byte("pulling web"))
	writer.flush()
	if got := terminal.String(); got != "pulling web\n" {
		t.Fatalf("unexpected flushed output: %q", got)
	}
}

func TestProgressWriterDropsOversizedLinesWithoutLeakingPartialSecrets(t *testing.T) {
	var terminal bytes.Buffer
	writer := &progressWriter{capture: cappedBuffer{limit: 65536}, notify: func(line string) { fmt.Fprintln(&terminal, line) }, secrets: []string{"secret-value"}}
	_, _ = writer.Write([]byte(strings.Repeat("x", 16380) + "secret-"))
	_, _ = writer.Write([]byte("value\nnext event\n"))
	writer.flush()
	if terminal.String() != "next event\n" {
		t.Fatalf("oversized line escaped: %q", terminal.String())
	}
	if writer.line.Len() != 0 {
		t.Fatal("line buffer was not reset")
	}
}
