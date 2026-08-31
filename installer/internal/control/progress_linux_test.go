package control

import (
	"os"
	"strconv"
	"syscall"
	"testing"
	"unsafe"
)

func TestProgressDetectsRealTerminalAndDumbMode(t *testing.T) {
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer master.Close()
	var unlocked int32
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, master.Fd(), syscall.TIOCSPTLCK, uintptr(unsafe.Pointer(&unlocked)))
	if errno != 0 {
		t.Fatal(errno)
	}
	var number uint32
	_, _, errno = syscall.Syscall(syscall.SYS_IOCTL, master.Fd(), syscall.TIOCGPTN, uintptr(unsafe.Pointer(&number)))
	if errno != 0 {
		t.Fatal(errno)
	}
	slave, err := os.OpenFile("/dev/pts/"+strconv.Itoa(int(number)), os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer slave.Close()
	size := struct{ rows, columns, x, y uint16 }{rows: 24, columns: 80}
	_, _, errno = syscall.Syscall(syscall.SYS_IOCTL, slave.Fd(), syscall.TIOCSWINSZ, uintptr(unsafe.Pointer(&size)))
	if errno != 0 {
		t.Fatal(errno)
	}
	t.Setenv("TERM", "xterm-256color")
	display := NewTerminalProgress(slave)
	if !display.interactive || display.width != 80 {
		t.Fatalf("TTY not recognized: %+v", display)
	}
	display.Update("Starting services")
	display.Close()
	data := make([]byte, 512)
	n, err := master.Read(data)
	if err != nil || n == 0 {
		t.Fatalf("no terminal progress: %v", err)
	}
	t.Setenv("TERM", "dumb")
	display = NewTerminalProgress(slave)
	display.Close()
	if display.interactive {
		t.Fatal("TERM=dumb must disable animation")
	}
}

func TestProgressDoesNotAnimatePipesOrDevNull(t *testing.T) {
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	if InteractiveTerminal(writer) {
		t.Fatal("pipe is not an interactive terminal")
	}
	null, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer null.Close()
	if InteractiveTerminal(null) {
		t.Fatal("a character device is not necessarily a terminal")
	}
}
