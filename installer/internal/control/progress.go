package control

import (
	"fmt"
	"io"
	"strings"
	"sync"
	"time"
)

// ProgressDisplay owns terminal rendering. Docker output never writes ANSI or
// new lines directly, and stdout remains available for the JSON protocol.
type ProgressDisplay struct {
	mu                 sync.Mutex
	output             io.Writer
	interactive        bool
	width              int
	phase, detail      string
	started, lastPlain time.Time
	frame              int
	closed             bool
	stop, stopped      chan struct{}
}

func newProgressDisplay(output io.Writer, interactive bool, width int, interval time.Duration) *ProgressDisplay {
	p := &ProgressDisplay{output: output, interactive: interactive, width: width, stop: make(chan struct{}), stopped: make(chan struct{})}
	go func() {
		defer close(p.stopped)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-p.stop:
				return
			case now := <-ticker.C:
				p.mu.Lock()
				p.render(now, false)
				p.mu.Unlock()
			}
		}
	}()
	return p
}

func progressText(value string) string {
	// Only our renderer may emit terminal control sequences, never child output.
	cleaned := strings.Map(func(r rune) rune {
		if r < 32 || r > 126 {
			return -1
		}
		return r
	}, value)
	if len(cleaned) > 160 {
		cleaned = cleaned[:157] + "..."
	}
	return cleaned
}

func (p *ProgressDisplay) Update(phase string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return
	}
	phase = progressText(phase)
	if phase == p.phase {
		return
	}
	p.phase, p.detail = phase, ""
	p.started = time.Now()
	p.frame = 0
	p.render(p.started, true)
}

func (p *ProgressDisplay) Detail(detail string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.closed {
		p.detail = progressText(detail)
	}
}

func (p *ProgressDisplay) render(now time.Time, changed bool) {
	if p.closed || p.phase == "" {
		return
	}
	elapsed := max(0, int(now.Sub(p.started).Seconds()))
	detail := ""
	if p.detail != "" {
		detail = " | " + p.detail
	}
	if !p.interactive {
		// No animation/escape codes in redirected logs or MCP. A heartbeat still
		// reports liveness if a subprocess or an entire diagnostic pass is slow.
		if changed || now.Sub(p.lastPlain) >= 30*time.Second {
			fmt.Fprintf(p.output, "OpenLab: %s (%ds)%s\n", p.phase, elapsed, detail)
			p.lastPlain = now
		}
		return
	}
	bar := []byte("........")
	bar[p.frame%len(bar)] = '='
	line := fmt.Sprintf("OpenLab %c [%s] %s (%ds)%s", "|/-\\"[p.frame%4], bar, p.phase, elapsed, detail)
	// Keep the last column free so an 80-column SSH terminal never wraps.
	if len(line) >= p.width {
		line = line[:max(0, p.width-1)]
	}
	fmt.Fprintf(p.output, "\r\x1b[2K%s", line)
	p.frame++
}

// Close joins the animation before callers print a result, error, or setup URL.
// It never marks a failed or cancelled operation as successful.
func (p *ProgressDisplay) Close() {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		<-p.stopped
		return
	}
	if p.phase != "" && p.interactive {
		line := fmt.Sprintf("OpenLab: %s (%ds)", p.phase, max(0, int(time.Since(p.started).Seconds())))
		if len(line) >= p.width {
			line = line[:max(0, p.width-1)]
		}
		fmt.Fprintf(p.output, "\r\x1b[2K%s\n", line)
	}
	p.closed = true
	close(p.stop)
	p.mu.Unlock()
	<-p.stopped
}

func readinessProgress(checks []Check) string {
	states := map[string]bool{}
	for _, check := range checks {
		states[check.ID] = check.Status == "pass"
	}
	groups := []struct {
		name   string
		checks []string
	}{
		{"DB", []string{"postgres"}},
		{"API", []string{"openlab_server", "api", "setup"}},
		{"Web", []string{"openlab_web", "web", "page_guard"}},
		{"Worker", []string{"openlab_worker", "worker_heartbeat"}},
	}
	parts := make([]string, 0, len(groups))
	for _, group := range groups {
		ready := true
		for _, id := range group.checks {
			ready = ready && states[id]
		}
		status := "wait"
		if ready {
			status = "ok"
		}
		parts = append(parts, group.name+":"+status)
	}
	return strings.Join(parts, " ")
}
