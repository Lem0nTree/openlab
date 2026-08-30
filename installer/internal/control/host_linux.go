package control

import (
	"context"
	"net"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type Host struct {
	OS           string `json:"os"`
	Architecture string `json:"architecture"`
	Distribution string `json:"distribution"`
	Codename     string `json:"codename"`
	MemoryBytes  uint64 `json:"memory_bytes"`
	FreeBytes    uint64 `json:"free_bytes"`
	BindAddress  string `json:"bind_address"`
	DockerReady  bool   `json:"docker_ready"`
	ComposeReady bool   `json:"compose_ready"`
	Systemd      bool   `json:"systemd"`
}

func ParseOSRelease(data string) map[string]string {
	result := map[string]string{}
	for _, line := range strings.Split(data, "\n") {
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		if parts[0] == "ID" || parts[0] == "VERSION_CODENAME" || parts[0] == "UBUNTU_CODENAME" {
			result[parts[0]] = strings.Trim(parts[1], "\"'")
		}
	}
	return result
}

func PrivateBindAddress() string {
	interfaces, err := net.Interfaces()
	if err != nil {
		return "127.0.0.1"
	}
	candidates := []string{}
	for _, device := range interfaces {
		if device.Flags&net.FlagUp == 0 || device.Flags&net.FlagLoopback != 0 {
			continue
		}
		skip := false
		for _, prefix := range []string{"docker", "br-", "veth", "virbr", "cni", "tailscale"} {
			if strings.HasPrefix(device.Name, prefix) {
				skip = true
			}
		}
		if skip {
			continue
		}
		addresses, _ := device.Addrs()
		for _, address := range addresses {
			ip, _, err := net.ParseCIDR(address.String())
			if err == nil && ip.To4() != nil && ip.IsPrivate() && !ip.IsLoopback() {
				candidates = append(candidates, ip.String())
			}
		}
	}
	sort.Strings(candidates)
	if len(candidates) > 0 {
		return candidates[0]
	}
	return "127.0.0.1"
}

func Inspect(ctx context.Context, runner Runner) (Host, Report) {
	host := Host{OS: runtime.GOOS, Architecture: runtime.GOARCH, BindAddress: PrivateBindAddress()}
	release, _ := os.ReadFile("/etc/os-release")
	distro := ParseOSRelease(string(release))
	host.Distribution = distro["ID"]
	host.Codename = distro["VERSION_CODENAME"]
	if host.Distribution == "ubuntu" && distro["UBUNTU_CODENAME"] != "" {
		host.Codename = distro["UBUNTU_CODENAME"]
	}
	var disk syscall.Statfs_t
	if syscall.Statfs("/", &disk) == nil {
		host.FreeBytes = disk.Bavail * uint64(disk.Bsize)
	}
	memory, _ := os.ReadFile("/proc/meminfo")
	for _, line := range strings.Split(string(memory), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "MemTotal:" {
			value, _ := strconv.ParseUint(fields[1], 10, 64)
			host.MemoryBytes = value * 1024
		}
	}
	_, hostSystemdErr := os.Stat("/run/systemd/system")
	host.Systemd = hostSystemdErr == nil
	_, dockerErr := runner.Run(ctx, 10*time.Second, nil, "docker", "info", "--format", "{{.ServerVersion}}")
	composeVersion, composeErr := runner.Run(ctx, 10*time.Second, nil, "docker", "compose", "version", "--short")
	host.DockerReady = dockerErr == nil
	host.ComposeReady = composeErr == nil && ComposeSupported(string(composeVersion))
	checks := []Check{
		NewCheck("platform", "Linux architecture", true, host.OS == "linux" && (host.Architecture == "amd64" || host.Architecture == "arm64"), "PLATFORM_UNSUPPORTED", "Supported targets are Linux AMD64 and ARM64.", "Install a 64-bit operating system."),
		NewCheck("disk", "Free disk space", true, host.FreeBytes >= 2*1024*1024*1024, "DISK_LOW", "At least 2 GiB of free space is required before pulling images.", "Free disk space without deleting OpenLab data."),
		NewCheck("memory", "Memory", false, host.MemoryBytes >= 1024*1024*1024, "MEMORY_LOW", "1 GiB or more is recommended; small Pis may require swap and slower operation.", "Use a larger host or configure swap deliberately."),
		NewCheck("docker", "Docker engine", true, host.DockerReady, "DOCKER_UNAVAILABLE", "Docker must be installed and accessible.", "openlabctl install --install-deps"),
		NewCheck("compose", "Docker Compose plugin", true, host.ComposeReady, "COMPOSE_UNAVAILABLE", "Docker Compose 2.24.4 or newer is required for safe port overrides.", "openlabctl install --install-deps"),
		NewCheck("scheduler", "Systemd scheduler", false, host.Systemd, "SCHEDULER_UNAVAILABLE", "Automatic security updates require systemd; manual updates remain available.", "Use openlabctl update on non-systemd hosts."),
	}
	return host, Summarize(checks, "host")
}

func ComposeSupported(raw string) bool {
	value := "v" + strings.TrimPrefix(strings.TrimSpace(raw), "v")
	return ValidRelease(value) && !Newer("v2.24.4", value)
}
