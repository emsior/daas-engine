"""Run the deployment smoke script against isolated API and proxy HTTP servers."""
from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT
from tests.support import minimal_subprocess_env

OAUTH_PATH = "/.well-known/oauth-authorization-server"
FEATURE_PATHS = {"mcp": "/mcp", "oauth": OAUTH_PATH}


@contextmanager
def _http_server(overrides):
    routes = {
        "/health": (200, b'{"status":"ok"}'),
        "/ready": (200, b'{"status":"ready"}'),
        "/api/v1/health": (200, b'{"status":"ok","mode":"cloud"}'),
        "/api/v1/me": (401, b"{}"),
        "/login": (200, b"TEST-login"),
        "/api/v1/metrics": (404, b"{}"),
        OAUTH_PATH: (200, b'{"issuer":"https://example.test"}'),
        "/mcp": (401, b"{}"),
    }
    routes.update(overrides)

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            code, body = routes.get(self.path, (404, b"{}"))
            if self.path == "/mcp":
                request_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if (
                    self.command != "POST"
                    or request_body != b"{}"
                    or self.headers.get("Content-Type") != "application/json"
                    or self.headers.get("Authorization")
                    or self.headers.get("X-Api-Key")
                ):
                    code, body = 400, b"{}"
            if code is None:
                # No HTTP response, while the ordinary API health probes still work.
                self.close_connection = True
                return
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_GET = do_HEAD = do_POST = _respond

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def run_smoke(tmp_path: Path):
    bash = shutil.which("bash")
    if bash is None or shutil.which("curl") is None:
        pytest.skip("smoke.sh requires bash and curl in PATH")

    def run(*, api=None, public=None, options=(), api_unreachable=False):
        with ExitStack() as stack:
            api_url = stack.enter_context(_http_server(api or {}))
            public_url = stack.enter_context(_http_server(public or {}))
            if api_unreachable:
                # Reserve a port without listening, avoiding a race with another process.
                closed_api = stack.enter_context(
                    ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler, bind_and_activate=False)
                )
                closed_api.server_bind()
                api_url = f"http://127.0.0.1:{closed_api.server_port}"
            result = subprocess.run(  # noqa: S603 -- fixed local script, synthetic loopback servers
                [
                    bash, "deploy/smoke.sh", "--api", api_url,
                    "--web", public_url, "--public", public_url, *options,
                ],
                cwd=PROJECT_ROOT,
                env=minimal_subprocess_env(tmp_path, TMPDIR=tmp_path.as_posix()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
        return result

    return run


def _feature_response(feature, code):
    body = b'{"issuer":"https://example.test"}' if feature == "oauth" else b"{}"
    return {FEATURE_PATHS[feature]: (code, body)}


@pytest.mark.parametrize(
    "feature,api_code,proxy_code,want",
    [
        pytest.param("mcp", 401, 404, 1, id="missing-mcp-proxy-route"),
        pytest.param("oauth", 200, 404, 1, id="missing-oauth-proxy-route"),
        pytest.param("mcp", 404, 401, 1, id="mcp-unexpectedly-enabled"),
        pytest.param("oauth", 404, 200, 1, id="oauth-unexpectedly-enabled"),
        pytest.param("mcp", 404, 404, 0, id="mcp-disabled-with-proof"),
        pytest.param("oauth", 404, 404, 0, id="oauth-disabled-with-proof"),
        pytest.param("mcp", 401, 401, 0, id="mcp-enabled"),
        pytest.param("oauth", 200, 200, 0, id="oauth-enabled"),
    ],
)
def test_smoke_auto_compares_api_and_proxy(run_smoke, feature, api_code, proxy_code, want):
    result = run_smoke(
        api=_feature_response(feature, api_code),
        public=_feature_response(feature, proxy_code),
    )
    output = result.stdout + result.stderr
    assert result.returncode == want, output
    assert ("SMOKE OK" if want == 0 else "SMOKE FAIL") in output, output
    assert f"proxy={proxy_code}, API={api_code}" in output, output


@pytest.mark.parametrize(
    "feature,mode,proxy_code,want",
    [
        ("mcp", "on", 404, 1),
        ("oauth", "on", 404, 1),
        ("mcp", "off", 401, 1),
        ("oauth", "off", 200, 1),
        ("mcp", "on", 401, 0),
        ("oauth", "on", 200, 0),
        ("mcp", "off", 404, 0),
        ("oauth", "off", 404, 0),
    ],
)
def test_smoke_explicit_expectations(run_smoke, feature, mode, proxy_code, want):
    result = run_smoke(
        api=_feature_response(feature, 503),
        public=_feature_response(feature, proxy_code),
        options=(f"--expect-{feature}", mode),
    )
    output = result.stdout + result.stderr
    assert result.returncode == want, output
    assert ("SMOKE OK" if want == 0 else "SMOKE FAIL") in output, output
    if mode == "off" and want == 1:
        assert "funkcja wlaczona, choc miala byc wylaczona" in output, output


@pytest.mark.parametrize("feature", ["mcp", "oauth"])
@pytest.mark.parametrize("api_code", [None, 307, 429, 503])
def test_smoke_auto_requires_direct_evidence(run_smoke, feature, api_code):
    result = run_smoke(
        api=_feature_response(feature, api_code),
        public=_feature_response(feature, 404),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "SMOKE FAIL" in output, output
    assert "brak dowodu oczekiwanego stanu" in output, output
    assert f"podaj --expect-{feature}" in output, output


def test_smoke_unreachable_api_does_not_prove_disabled_features(run_smoke):
    result = run_smoke(
        api_unreachable=True,
        public={"/mcp": (404, b"{}"), OAUTH_PATH: (404, b"{}")},
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "SMOKE FAIL" in output, output
    assert output.count("brak dowodu oczekiwanego stanu") == 2, output


@pytest.mark.parametrize("mode", ["auto", "on"])
def test_smoke_oauth_requires_issuer(run_smoke, mode):
    result = run_smoke(
        public={OAUTH_PATH: (200, b"<html>TEST-login</html>")},
        options=("--expect-oauth", mode),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "SMOKE FAIL" in output, output


def test_smoke_auto_requires_issuer_in_direct_metadata(run_smoke):
    result = run_smoke(api={OAUTH_PATH: (200, b"<html>TEST-login</html>")})
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "brak dowodu oczekiwanego stanu" in output, output


@pytest.mark.parametrize("feature,code", [("mcp", 200), ("oauth", 401)])
def test_smoke_auto_rejects_invalid_feature_status(run_smoke, feature, code):
    result = run_smoke(
        api=_feature_response(feature, code), public=_feature_response(feature, code)
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "SMOKE FAIL" in output, output


@pytest.mark.parametrize("feature", ["mcp", "oauth"])
def test_smoke_rejects_unknown_expectation(run_smoke, feature):
    result = run_smoke(options=(f"--expect-{feature}", "invalid"))
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "on|off|auto" in output, output
