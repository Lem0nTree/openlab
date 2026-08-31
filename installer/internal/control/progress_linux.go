package control

import (
	"os"
	"syscall"
	"time"
	"unsafe"
)

func InteractiveTerminal(output *os.File) bool {
	var termios syscall.Termios
	_, _, err := syscall.Syscall(syscall.SYS_IOCTL, output.Fd(), syscall.TCGETS, uintptr(unsafe.Pointer(&termios)))
	return err == 0 && os.Getenv("TERM") != "dumb"
}

func NewTerminalProgress(output *os.File) *ProgressDisplay {
	interactive := InteractiveTerminal(output)
	width := 80
	if interactive {
		var size struct{ rows, columns, x, y uint16 }
		_, _, err := syscall.Syscall(syscall.SYS_IOCTL, output.Fd(), syscall.TIOCGWINSZ, uintptr(unsafe.Pointer(&size)))
		if err == 0 && size.columns > 0 {
			width = int(size.columns)
		}
	}
	return newProgressDisplay(output, interactive, width, 100*time.Millisecond)
}
