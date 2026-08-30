"""Disposable Compose integration gate; never reuses an existing project/volume.

Use --keep only for local browser QA. The printed project name identifies exactly
which test resources to stop later; cleanup never targets another project.
"""

import argparse
import base64
import concurrent.futures
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--web", required=True)
    parser.add_argument("--postgres", default="pgvector/pgvector:pg17")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--credentials-file", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    project = "openlab-installer-smoke-" + secrets.token_hex(6)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    temp = tempfile.TemporaryDirectory(prefix=project)
    envfile = Path(temp.name) / "smoke.env"
    token = secrets.token_urlsafe(32)
    values = {
        "POSTGRES_DB": "openlab", "POSTGRES_USER": "openlab",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "OPENLAB_SECRET_KEY": secrets.token_urlsafe(48),
        "OPENLAB_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "OPENLAB_SETUP_TOKEN": token, "OPENLAB_VERSION": "v0.2.0",
        "OPENLAB_SERVER_IMAGE": args.server, "OPENLAB_WORKER_IMAGE": args.server,
        "OPENLAB_WEB_IMAGE": args.web, "OPENLAB_POSTGRES_IMAGE": args.postgres,
        "OPENLAB_BIND_ADDRESS": "127.0.0.1", "OPENLAB_PORT": str(port),
    }
    values["DATABASE_URL"] = f"postgresql+psycopg://openlab:{values['POSTGRES_PASSWORD']}@postgres:5432/openlab"
    envfile.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    envfile.chmod(0o600)
    command = ["docker", "compose", "--project-name", project, "--env-file", str(envfile),
               "-f", str(root / "deploy/compose.yml")]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("OPENLAB_", "POSTGRES_", "COMPOSE_")) and key != "DATABASE_URL"}

    def compose(*arguments, timeout=180):
        result = subprocess.run(command + list(arguments), env=environment, capture_output=True,
                                text=True, timeout=timeout)
        if result.returncode:
            # Avoid reflecting env or service log secrets into the test report.
            raise RuntimeError(f"Compose operation failed: {arguments[0]} (exit {result.returncode})")
        return result.stdout

    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies), NoRedirect())
    anonymous = urllib.request.build_opener(NoRedirect())

    def request(path, method="GET", payload=None, *, auth=True, csrf=True):
        headers = {"Origin": origin}
        if csrf:
            for cookie in cookies:
                if cookie.name == "openlab_csrf":
                    headers["X-CSRF-Token"] = cookie.value
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(origin + path, data=data, method=method, headers=headers)
        try:
            response = (client if auth else anonymous).open(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            content = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw.decode()
            return response.code, content, response.headers

    success = False
    try:
        print(f"Disposable project: {project}; browser URL: {origin}", flush=True)
        compose("up", "-d", "--no-build", timeout=360)
        deadline = time.monotonic() + 180
        while True:
            try:
                if request("/api/v1/health", auth=False)[0] == 200:
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("HTTP readiness timeout")
            time.sleep(2)
        for path in ("/settings", "/onboarding"):
            code, _, headers = request(path, auth=False)
            assert code == 307 and "/login" in headers["Location"], f"Anonymous page exposed: {path}"
        assert request("/api/v1/readiness", auth=False)[0] == 401
        assert request("/api/v1/setup", auth=False)[1]["setup_required"] is True
        owner = {"token": token, "lab_name": "Installer smoke lab", "email": "smoke@example.test",
                 "display_name": "Smoke owner", "password": secrets.token_urlsafe(24)}
        if args.credentials_file:
            args.credentials_file.write_text(json.dumps({"url": origin, "email": owner["email"], "password": owner["password"]}))
            args.credentials_file.chmod(0o600)
        bad = dict(owner, token="wrong")
        assert request("/api/v1/setup", "POST", bad, auth=False)[0] == 403
        assert request("/api/v1/setup", "POST", dict(owner, token="wrong-\u2603"), auth=False)[0] == 403
        # Real PostgreSQL transaction race, not a mocked setup lock.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(request, "/api/v1/setup", "POST", owner, auth=False) for _ in range(2)]
            assert sorted(f.result()[0] for f in futures) == [201, 409]
        assert request("/api/v1/session", "POST", {"email": owner["email"], "password": owner["password"]})[0] == 200
        assert request("/api/v1/settings/network", "PUT", {"public_url": origin}, csrf=False)[0] == 403
        assert request("/api/v1/settings/network", "PUT", {"public_url": origin})[1]["verified"] is True
        deadline = time.monotonic() + 65
        while True:
            report = request("/api/v1/readiness")[1]
            if report["overall"] != "blocked":
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"Core readiness failed: {[c['code'] for c in report['checks'] if c['required'] and c['status'] != 'pass']}")
            time.sleep(2)
        assert report["overall"] == "ready_with_warnings", "Optional integrations must not be invented"
        serialized = json.dumps(report)
        assert all(secret not in serialized for key, secret in values.items() if key in {"POSTGRES_PASSWORD", "OPENLAB_SECRET_KEY", "OPENLAB_ENCRYPTION_KEY", "OPENLAB_SETUP_TOKEN"})
        assert request("/api/v1/onboarding/complete", "POST", {})[1]["completed_at"]
        assert request("/onboarding")[0] == 200
        # Prove the supported pre-wizard schema can move forward without losing
        # owner/lab/session data, and that image rollback can still read it.
        compose("stop", "openlab-worker", "openlab-server", "openlab-web")
        compose("run", "--rm", "--no-deps", "openlab-server", "alembic", "downgrade", "0009_lab_settings")
        counts = compose("exec", "-T", "postgres", "psql", "-U", "openlab", "-d", "openlab", "-Atc",
                         "SELECT (SELECT count(*) FROM users)||','||(SELECT count(*) FROM labs)||','||(SELECT count(*) FROM session_tokens)").strip()
        users, labs, sessions = (int(value) for value in counts.split(","))
        assert (users, labs) == (1, 1) and sessions >= 1, f"Migration rollback lost owner state: {counts}"
        compose("run", "--rm", "--no-deps", "openlab-server", "alembic", "upgrade", "head")
        compose("up", "-d", "--no-build")
        deadline = time.monotonic() + 120
        while True:
            try:
                if request("/api/v1/session")[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise AssertionError("Session/owner data did not survive 0009-to-head migration")
            time.sleep(2)
        # Wizard-only fields do not exist in 0009; configure them again after
        # the deliberately destructive test downgrade. Production never runs DB downgrades.
        assert request("/api/v1/settings/network", "PUT", {"public_url": origin})[1]["verified"] is True
        deadline = time.monotonic() + 65
        while request("/api/v1/readiness")[1]["overall"] == "blocked":
            if time.monotonic() > deadline:
                raise AssertionError("Readiness did not recover after migration compatibility test")
            time.sleep(2)
        assert request("/api/v1/onboarding/complete", "POST", {})[0] == 200
        compose("stop", "openlab-worker")
        compose("exec", "-T", "postgres", "psql", "-U", "openlab", "-d", "openlab", "-c",
                "UPDATE service_heartbeats SET last_seen_at=NOW()-INTERVAL '2 minutes'")
        report = request("/api/v1/readiness")[1]
        assert report["overall"] == "blocked"
        assert any(c["code"] == "WORKER_UNAVAILABLE" for c in report["checks"])
        assert request("/api/v1/onboarding/complete", "POST", {})[0] == 409
        compose("start", "openlab-worker")
        # Re-run upgrade and restart against the same named volumes: owner/settings persist.
        compose("restart", "openlab-server", "openlab-worker")
        deadline = time.monotonic() + 120
        while True:
            try:
                code, result, _ = request("/api/v1/onboarding")
                if code == 200 and result["readiness"]["overall"] != "blocked":
                    assert result["completed_at"] and result["network"]["public_url"] == origin
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise AssertionError("Restart did not preserve a ready owner session/configuration")
            time.sleep(2)
        assert request("/api/v1/setup", auth=False)[1]["setup_required"] is False
        success = True
        print("PASS: real Compose setup race, sessions, CSRF, URL, 0009 migration compatibility, readiness, stale worker, restart persistence", flush=True)
    finally:
        if not args.keep:
            # project is generated above, never supplied by a caller or matched by wildcard.
            compose("down", "--volumes", "--remove-orphans")
            temp.cleanup()
            print(f"Removed only disposable project {project} and its test data.", flush=True)
        else:
            temp._finalizer.detach()
            print(f"Kept disposable project for QA. Env file: {envfile}; success={success}", flush=True)


if __name__ == "__main__":
    main()
