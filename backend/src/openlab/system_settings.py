"""Safe, owner-managed settings and worker capability checks."""

import shutil
import subprocess
from pathlib import Path

from .config import Settings, get_settings
from .models import Lab


def normalize_kicad_cli(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    executable = cleaned.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable not in {"kicad-cli", "kicad-cli.exe"}:
        raise ValueError("KiCad command must end with kicad-cli or kicad-cli.exe")
    return cleaned


def effective_kicad_cli(lab: Lab | None, config: Settings | None = None) -> tuple[str | None, str]:
    if lab and lab.kicad_cli:
        return lab.kicad_cli, "settings"
    configured = (config or get_settings()).kicad_cli
    return (configured, "environment") if configured else (None, "unset")


def check_kicad_cli(cli: str | None) -> dict[str, object]:
    if not cli:
        return {"status": "unavailable", "error": "KiCad CLI is not configured"}
    resolved = shutil.which(cli)
    if resolved is None and Path(cli).is_file():
        resolved = cli
    if resolved is None:
        return {
            "status": "unavailable",
            "error": "The configured kicad-cli executable is not available in the worker container",
        }
    try:
        completed = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "error": str(exc)[:1000]}
    output = (completed.stdout or completed.stderr).strip()[:1000]
    if completed.returncode != 0:
        return {
            "status": "unavailable",
            "error": output or f"kicad-cli exited with status {completed.returncode}",
        }
    return {"status": "available", "version": output.splitlines()[0] if output else "Detected"}
