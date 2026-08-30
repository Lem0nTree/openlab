package control

import (
	"context"
	"time"
)

// Bind changes only OpenLab's private listener. It never edits firewalls, routers,
// Tailscale Serve/Funnel, or other applications' listeners.
func (e *Engine) Bind(ctx context.Context, address string, port int) (any, error) {
	config, err := loadConfig()
	if err != nil {
		return nil, err
	}
	previous := config
	config.BindAddress = address
	config.Port = port
	if err = config.Validate(); err != nil {
		return nil, err
	}
	if err = writeNetwork(config); err != nil {
		return nil, err
	}
	if err = saveConfig(config); err != nil {
		_ = writeNetwork(previous)
		return nil, err
	}
	_, err = e.compose(ctx, 2*time.Minute, config, "up", "-d", "--no-build", "openlab-web")
	if err == nil {
		var report Report
		report, err = e.waitReady(ctx)
		if err == nil {
			return report, nil
		}
	}
	rollbackCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Minute)
	defer cancel()
	if writeNetwork(previous) != nil || saveConfig(previous) != nil {
		return nil, Fail("NETWORK_ROLLBACK_FAILED", "The previous binding could not be restored.", "Inspect /etc/openlab locally.")
	}
	if _, restoreErr := e.compose(rollbackCtx, 2*time.Minute, previous, "up", "-d", "--no-build", "openlab-web"); restoreErr != nil {
		return nil, Fail("NETWORK_ROLLBACK_FAILED", "The previous listener could not be restarted.", "openlabctl doctor")
	}
	return nil, Fail("NETWORK_CHANGE_FAILED", "The new listener failed validation; the previous configuration was restored.", "Verify the address belongs to this host and the port is free.")
}
