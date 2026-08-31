package control

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

type frameRecorder struct {
	mu sync.Mutex
	bytes.Buffer
	frames chan struct{}
}

func (r *frameRecorder) Write(data []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	n, err := r.Buffer.Write(data)
	select {
	case r.frames <- struct{}{}:
	default:
	}
	return n, err
}

func TestProgressAnimatesWhileReadinessProbeIsBlocked(t *testing.T) {
	var output frameRecorder
	output.frames = make(chan struct{}, 50)
	display := newProgressDisplay(&output, true, 80, 5*time.Millisecond)
	defer display.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	engine := &Engine{Progress: display.Update, Detail: display.Detail}
	finished := make(chan error, 1)
	go func() {
		_, err := engine.waitForReadiness(ctx, time.Second, func(ctx context.Context) (Report, error) {
			<-ctx.Done()
			return Report{Overall: "blocked"}, ctx.Err()
		})
		finished <- err
	}()
	for range 4 {
		select {
		case <-output.frames:
		case <-time.After(time.Second):
			t.Fatal("animation froze while probe was blocked")
		}
	}
	cancel()
	select {
	case err := <-finished:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancellation did not stop readiness")
	}
	display.Close()
	text := output.Buffer.String() // Close joined the only remaining writer.
	if !strings.Contains(text, "Readiness") || !strings.Contains(text, "DB:wait") {
		t.Fatal(text)
	}
	if strings.Contains(text, "Services ready") {
		t.Fatal("cancelled operation reported success")
	}
	if strings.Count(text, "\n") != 1 {
		t.Fatalf("interactive progress scrolled: %q", text)
	}
}

func TestPlainProgressCoalescesDockerEventsAndHasBoundedHeartbeat(t *testing.T) {
	var output bytes.Buffer
	display := newProgressDisplay(&output, false, 80, time.Hour)
	display.Update("Starting services")
	for i := range 100 {
		display.Detail(fmt.Sprintf("Container example-%d Created", i))
	}
	// Drive the clock directly rather than sleeping for the log heartbeat.
	display.mu.Lock()
	display.render(display.started.Add(29*time.Second), false)
	display.render(display.started.Add(30*time.Second), false)
	display.mu.Unlock()
	display.Close()
	text := output.String()
	if strings.Count(text, "\n") != 2 || strings.ContainsAny(text, "\r\x1b") {
		t.Fatalf("noisy redirected output: %q", text)
	}
	if !strings.Contains(text, "(30s)") || !strings.Contains(text, "example-99") {
		t.Fatal(text)
	}
}

func TestReadinessRequiresRoutesAndWorkerHeartbeat(t *testing.T) {
	checks := []Check{{ID: "postgres", Status: "pass"}, {ID: "openlab_server", Status: "pass"}, {ID: "openlab_worker", Status: "pass"}, {ID: "openlab_web", Status: "pass"}}
	if got := readinessProgress(checks); got != "DB:ok API:wait Web:wait Worker:wait" {
		t.Fatal(got)
	}
	for _, id := range []string{"api", "setup", "web", "page_guard", "worker_heartbeat"} {
		checks = append(checks, Check{ID: id, Status: "pass"})
	}
	if got := readinessProgress(checks); got != "DB:ok API:ok Web:ok Worker:ok" {
		t.Fatal(got)
	}
	checks[len(checks)-1].Status = "fail"
	if !strings.Contains(readinessProgress(checks), "Worker:wait") {
		t.Fatal("stale heartbeat reported ready")
	}
}

func TestReadinessTimeoutBoundsTheProbeAndDoesNotReportSuccess(t *testing.T) {
	var messages []string
	engine := &Engine{Progress: func(message string) { messages = append(messages, message) }}
	_, err := engine.waitForReadiness(context.Background(), 5*time.Millisecond, func(ctx context.Context) (Report, error) {
		<-ctx.Done()
		return Report{Overall: "ready"}, nil // A late result must not override timeout.
	})
	if SafeError(err).Code != "READINESS_TIMEOUT" {
		t.Fatal(err)
	}
	if strings.Contains(strings.Join(messages, " "), "Services ready") {
		t.Fatal(messages)
	}
}

func TestReadyReportFinishesWithoutDelay(t *testing.T) {
	engine := &Engine{}
	report, err := engine.waitForReadiness(context.Background(), time.Second, func(context.Context) (Report, error) { return Report{Overall: "ready"}, nil })
	if err != nil || report.Overall != "ready" {
		t.Fatalf("%+v %v", report, err)
	}
}

func TestTerminalOutputIsBoundedAndChildControlsCannotClearScreen(t *testing.T) {
	var output bytes.Buffer
	display := newProgressDisplay(&output, true, 60, time.Hour)
	display.Update("Pulling images")
	display.Detail("bad\x1b[2J\r\n" + strings.Repeat("x", 200))
	display.mu.Lock()
	display.render(time.Now(), false)
	display.mu.Unlock()
	display.Close()
	text := output.String()
	if strings.Contains(text, "\x1b[2J") {
		t.Fatal("child output controlled terminal")
	}
	for _, line := range strings.Split(text, "\r\x1b[2K") {
		if len(strings.TrimSuffix(line, "\n")) >= 60 {
			t.Fatalf("line wraps: %q", line)
		}
	}
}

func TestUnusedDisplayIsSilent(t *testing.T) {
	var output bytes.Buffer
	display := newProgressDisplay(&output, false, 80, time.Hour)
	display.Detail("ignored")
	display.Close()
	display.Update("ignored")
	display.Close()
	if output.Len() != 0 {
		t.Fatal(output.String())
	}
}
