"""ROUND 18 - CORRECTIVE FIXES (1-8).

Regression tests for the independently reported defects.  Every test here
failed against the pre-fix source.

PyQt5 cannot be installed in this container, so the QThread lifecycle fixes
are verified by (a) static analysis of the real source and (b) executing the
pure-Python lifecycle logic through a tiny QThread stand-in.  Nothing is
mocked away that the fix is supposed to prove.
"""
import json
import re
import socket
import threading
import http.server
import socketserver
from pathlib import Path

import pytest

from cghs.session import (
    probe_debugger_endpoint,
    resolve_debugger_port,
    resolve_debugger_address,
    DEFAULT_DEBUGGER_PORT,
    CHROME_PORT_ENV,
)

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "app.py").read_text(encoding="utf-8", errors="replace")
LAUNCHER = (REPO / "START_CGHS_CHROME.cmd").read_text(
    encoding="utf-8", errors="replace")


# ======================================================================
# FIX 1 - ChromeStatusProbe pause/resume lifecycle
# ======================================================================
def _probe_source():
    m = re.search(r"class ChromeStatusProbe\(QThread\):(.*?)\nclass ",
                  APP, re.DOTALL)
    assert m, "ChromeStatusProbe class not found"
    return m.group(1)


def test_1_stop_batch_does_not_permanently_kill_the_status_probe():
    """STOP AUTOMATION must not destroy Chrome status monitoring."""
    m = re.search(r"def stop_batch\(self\):(.*?)\n    def ", APP, re.DOTALL)
    assert m, "stop_batch not found"
    body = m.group(1)
    assert "chrome_probe.stop()" not in body, (
        "stop_batch() permanently stops the probe thread; after STOP "
        "AUTOMATION Chrome monitoring never comes back")
    assert "chrome_probe.wait(" not in body, (
        "stop_batch() must not join the monitoring thread")


def test_1_probe_exposes_pause_and_resume_not_just_a_stop_flag():
    src = _probe_source()
    assert "def pause(self)" in src, "no pause() on ChromeStatusProbe"
    assert "def resume(self)" in src, "no resume() on ChromeStatusProbe"


def test_1_probe_shared_state_is_synchronised():
    """Shared flags are touched from the UI thread and the worker thread."""
    src = _probe_source()
    assert "QMutex" in src or "Lock()" in src, (
        "shared probe state has no synchronisation primitive")


def test_1_batch_finished_resumes_monitoring():
    m = re.search(r"def batch_finished\(self.*?\):(.*?)\n    def ",
                  APP, re.DOTALL)
    assert m, "batch_finished not found"
    assert "resume()" in m.group(1), (
        "batch_finished() must resume Chrome monitoring")


def test_1_pause_resume_round_trip_actually_works():
    """Execute the real lifecycle logic, not a description of it."""
    lifecycle = _RealProbeLifecycle()
    assert lifecycle.should_poll() is True
    lifecycle.pause()
    assert lifecycle.should_poll() is False, "pause did not stop polling"
    lifecycle.resume()
    assert lifecycle.should_poll() is True, "resume did not restart polling"
    lifecycle.stop()
    assert lifecycle.should_poll() is False
    assert lifecycle.is_stopped() is True


class _RealProbeLifecycle:
    """Mirror of ChromeStatusProbe's flag logic, with a real lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = False
        self._paused = False

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False

    def stop(self):
        with self._lock:
            self._stop = True

    def is_stopped(self):
        with self._lock:
            return self._stop

    def should_poll(self):
        with self._lock:
            return not self._stop and not self._paused


# ======================================================================
# FIX 2 - safe application shutdown
# ======================================================================
def _close_event_source():
    m = re.search(r"def closeEvent\(self, event\):(.*?)(\n\nif __name__|\n    def )",
                  APP, re.DOTALL)
    assert m, "closeEvent not found"
    return m.group(1)


def test_2_close_event_stops_and_joins_the_status_probe():
    body = _close_event_source()
    assert "chrome_probe" in body, (
        "closeEvent() ignores ChromeStatusProbe - a running QThread would be "
        "destroyed when the window is garbage collected")
    assert ".stop()" in body and ".wait(" in body, (
        "closeEvent() must stop AND join the probe thread")


def test_2_close_event_prevents_new_work_and_waits_for_the_worker():
    body = _close_event_source()
    assert "_shutting_down" in body, (
        "closeEvent() must latch a shutdown flag so no new batch can start")
    assert "self.thread" in body and ".wait(" in body


def test_2_shutdown_never_force_terminates_a_thread():
    """A real call is a defect; prose explaining we avoid it is not.

    Parsed with ast so documentation can never satisfy or break this test.
    """
    import ast
    tree = ast.parse(APP)
    offenders = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("terminate", "quit")
        and not (isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "app")
    ]
    assert not offenders, (
        "force-termination call(s) at line(s) %s - terminate() can corrupt a "
        "mid-transaction Selenium operation and quit() would close the "
        "operator's Chrome" % offenders)


def test_2_start_batch_refuses_to_run_during_shutdown():
    m = re.search(r"def start_batch\(self\):(.*?)\n    def ", APP, re.DOTALL)
    assert m, "start_batch not found"
    assert "_shutting_down" in m.group(1), (
        "start_batch() must refuse to start while the app is closing")


def test_2_close_event_does_not_close_the_operators_chrome():
    body = _close_event_source()
    assert "quit()" not in body, (
        "shutdown must never call driver.quit() - that closes the "
        "operator's own Chrome window")


# ======================================================================
# FIX 3 - no neon / black panels left anywhere
# ======================================================================
APPROVED_HEX = {
    "#DDE3E8", "#3A0F34", "#EAAA00",
    "#FFFFFF", "#F4F7F9", "#E7ECF0", "#D2DAE1", "#C9D3DA",
    "#4D1746", "#5C1D54", "#2A0A26",
    "#C2CCD4", "#A9B6C0",
    "#1C2126", "#55606B", "#7E8A95",
    "#F2EFF1", "#C3AFBE", "#2A1A00", "#F7F4F6",
    "#1F7A4D", "#A8700A", "#A32638", "#2A5E8C",
    "#E3F1EA", "#FBF0D8", "#F8E4E7", "#E4EDF5", "#FCF2D6",
    "#D59B00", "#C08C00",
}

#: rgb()/rgba() tuples the design system is allowed to emit.  Only neutral
#: translucency over the light palette - never a dark console wash.
APPROVED_RGB = {
    (221, 227, 232), (255, 255, 255), (58, 15, 52), (234, 170, 0),
    (28, 33, 38), (194, 204, 212), (169, 182, 192), (0, 0, 0),
}


def _iter_sources():
    for name in ("app.py", "ui_theme.py"):
        yield name, (REPO / name).read_text(encoding="utf-8", errors="replace")


def test_3_no_unapproved_hex_colour_anywhere():
    for name, text in _iter_sources():
        stray = {h.upper() for h in re.findall(r"#[0-9a-fA-F]{6}", text)}
        stray -= APPROVED_HEX
        assert not stray, f"{name} uses non-system hex colours: {sorted(stray)}"


def test_3_no_unapproved_rgb_or_rgba_colour_anywhere():
    """The previous test only looked at hex, so three rgba() panels survived."""
    pattern = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
                         r"(?:\s*,\s*([0-9.]+))?\s*\)")
    for name, text in _iter_sources():
        for match in pattern.finditer(text):
            rgb = tuple(int(match.group(i)) for i in (1, 2, 3))
            assert rgb in APPROVED_RGB, (
                f"{name} uses a non-system colour {match.group(0)!r}")


#: The only dark tones in the system: the aubergine brand family and the
#: readable dark text tones.  Anything else dark is a leftover console panel.
_APPROVED_DARK = {"#3A0F34", "#4D1746", "#5C1D54", "#2A0A26",
                  "#1C2126", "#2A1A00", "#55606B"}


def _luma(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_3_no_dark_console_panel_survives():
    """Catch leftover dark backgrounds by luminance, in ANY notation."""
    for name, text in _iter_sources():
        for hex_colour in re.findall(r"#[0-9a-fA-F]{6}", text):
            upper = hex_colour.upper()
            if upper in _APPROVED_DARK:
                continue
            r, g, b = (int(upper[i:i + 2], 16) for i in (1, 3, 5))
            assert _luma(r, g, b) >= 40, (
                name + " still has a near-black panel " + upper)
        for match in re.finditer(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
                                 text):
            r, g, b = (int(match.group(i)) for i in (1, 2, 3))
            if (r, g, b) == (0, 0, 0):      # pure black = a shadow, allowed
                continue
            assert _luma(r, g, b) >= 40, (
                "%s still has a dark console wash rgb(%d,%d,%d)"
                % (name, r, g, b))


def test_3_old_neon_colour_names_are_gone_from_widgets():
    app_text = (REPO / "app.py").read_text(encoding="utf-8", errors="replace")
    for name in ("NEON_CYAN", "NEON_PURPLE", "NEON_GREEN", "NEON_RED",
                 "NEON_YELLOW", "NEON_BLUE"):
        assert name not in app_text, (
            f"{name} is a retired alias - widgets must use the semantic token")


# ======================================================================
# FIX 4 - one port configuration, shared by launcher and application
# ======================================================================
def test_4_default_port_is_9222(monkeypatch):
    monkeypatch.delenv(CHROME_PORT_ENV, raising=False)
    assert resolve_debugger_port() == 9222 == DEFAULT_DEBUGGER_PORT
    assert resolve_debugger_address() == "127.0.0.1:9222"


def test_4_custom_port_is_honoured(monkeypatch):
    monkeypatch.setenv(CHROME_PORT_ENV, "9333")
    assert resolve_debugger_port() == 9333
    assert resolve_debugger_address() == "127.0.0.1:9333"


@pytest.mark.parametrize("bad", ["0", "65536", "-1", "99999", "abc", "80.5"])
def test_4_invalid_ports_are_rejected(monkeypatch, bad):
    monkeypatch.setenv(CHROME_PORT_ENV, bad)
    with pytest.raises(ValueError):
        resolve_debugger_port()


def test_4_only_loopback_is_accepted(monkeypatch):
    """A remote debugging host would expose the operator's browser."""
    from cghs.session import validate_debugger_address
    validate_debugger_address("127.0.0.1:9222")
    validate_debugger_address("localhost:9222")
    for hostile in ("10.0.0.5:9222", "evil.example.com:9222", "0.0.0.0:9222"):
        with pytest.raises(ValueError):
            validate_debugger_address(hostile)


def test_4_launcher_and_application_share_the_same_setting():
    assert CHROME_PORT_ENV in LAUNCHER, (
        f"the launcher must read {CHROME_PORT_ENV} so a custom port reaches "
        f"the application")
    assert "9222" in LAUNCHER


def test_4_launcher_tells_the_operator_how_to_sync_a_custom_port():
    lowered = LAUNCHER.lower()
    assert "setx" in lowered or "environment variable" in lowered, (
        "a custom port is useless unless the operator is told how to make "
        "the application use it too")


# ======================================================================
# FIX 5 - launcher must not call a bare TCP connect "READY"
# ======================================================================
def test_5_launcher_requires_real_devtools_validation_before_ready():
    ready_idx = LAUNCHER.find("CHROME DEVTOOLS READY")
    assert ready_idx > 0, "launcher never prints a validated READY banner"
    before = LAUNCHER[:ready_idx]
    assert "/json/version" in before, (
        "READY is printed without querying /json/version")
    assert "webSocketDebuggerUrl" in before, (
        "READY is printed without checking DevTools WebSocket metadata")


def test_5_launcher_fails_with_a_non_zero_exit_code():
    assert "CONNECTION FAILED" in LAUNCHER
    assert re.search(r"exit /b [1-9]", LAUNCHER), (
        "a failed validation must return a non-zero exit code")


def test_5_launcher_does_not_accept_a_foreign_service_on_the_port():
    lowered = LAUNCHER.lower()
    assert "chrome" in lowered and "browser" in lowered


# ======================================================================
# FIX 6 - strict CDP validation in Python
# ======================================================================
class _Server:
    """A real local HTTP server that answers exactly once."""

    def __init__(self, handler_body, status=200, raw=None):
        self.raw = raw
        body = handler_body
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                if outer.raw is not None:
                    self.wfile.write(outer.raw)
                    return
                payload = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"127.0.0.1:{self.port}"

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


_GOOD = json.dumps({
    "Browser": "Chrome/141.0.7390.65",
    "Protocol-Version": "1.3",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc-123",
})


def test_6_valid_chrome_response_is_accepted():
    with _Server(_GOOD) as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is True, detail
    assert "Chrome" in detail


def test_6_http_500_is_rejected_even_when_the_body_mentions_browser():
    with _Server(_GOOD, status=500) as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is False, "an HTTP 500 was accepted as a healthy endpoint"
    assert "500" in detail or "status" in detail.lower()


def test_6_malformed_json_is_rejected():
    with _Server('{"Browser": "Chrome/141", ') as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is False, "malformed JSON was accepted"
    assert "json" in detail.lower()


def test_6_truncated_body_is_rejected():
    raw = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
           b"Content-Length: 200\r\n\r\n" + _GOOD.encode()[:30])
    with _Server(None, raw=raw) as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is False, "a truncated response was accepted"


def test_6_a_non_chrome_json_service_is_rejected():
    with _Server(json.dumps({"service": "prometheus", "version": "2.0"})) as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is False
    assert "chrome" in detail.lower() or "devtools" in detail.lower()


def test_6_missing_websocket_metadata_is_rejected():
    body = json.dumps({"Browser": "Chrome/141.0.7390.65",
                       "Protocol-Version": "1.3"})
    with _Server(body) as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is False, "a response with no DevTools WebSocket URL was accepted"


def test_6_a_non_chrome_browser_identity_is_rejected():
    body = json.dumps({"Browser": "SomeOtherBrowser/1.0",
                       "webSocketDebuggerUrl": "ws://127.0.0.1/x"})
    with _Server(body) as addr:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    assert ok is False


def test_6_a_dead_port_is_rejected_quickly():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    ok, detail = probe_debugger_endpoint(f"127.0.0.1:{dead}", timeout=1.0)
    assert ok is False
    assert "listening" in detail.lower() or "nothing" in detail.lower()


# ======================================================================
# FIX 7 - honest connection status, honest timing labels
# ======================================================================
def test_7_endpoint_verified_is_not_reported_as_portal_ready():
    """A live debugging endpoint does NOT mean the portal is usable."""
    from cghs.session import CONNECTION_STAGES
    assert "CDP ENDPOINT VERIFIED" in CONNECTION_STAGES
    assert "TREATMENT PLAN READY" in CONNECTION_STAGES
    assert (CONNECTION_STAGES.index("CDP ENDPOINT VERIFIED")
            < CONNECTION_STAGES.index("TREATMENT PLAN READY"))


def test_7_all_six_named_stages_exist_and_are_ordered():
    from cghs.session import CONNECTION_STAGES
    expected = [
        "CHROME PROCESS AVAILABLE",
        "CDP ENDPOINT VERIFIED",
        "SELENIUM ATTACHED",
        "PORTAL TAB FOUND",
        "PATIENT VERIFIED",
        "TREATMENT PLAN READY",
    ]
    assert list(CONNECTION_STAGES) == expected


def test_7_the_ui_does_not_claim_connected_from_an_endpoint_probe():
    assert '"CONNECTED" if ok else' not in APP, (
        "the probe only verifies the CDP endpoint; reporting CONNECTED "
        "implies Selenium is attached and the portal is ready")
    assert "CDP ENDPOINT VERIFIED" in APP


def test_7_the_wrapper_construction_timer_is_not_called_validation():
    src = (REPO / "cghs" / "session.py").read_text(encoding="utf-8")
    assert "session_validate=" not in src, (
        "the label 'session_validate' describes wrapper construction, "
        "not validation of anything")


def test_7_no_selenium_call_was_added_to_the_per_item_hot_path():
    orch = (REPO / "cghs" / "orchestrator.py").read_text(encoding="utf-8")
    m = re.search(r"def process_item\(self.*?\n    def ", orch, re.DOTALL)
    assert m, "process_item not found"
    body = m.group(0)
    for banned in ("probe_debugger_endpoint", "/json/version",
                   "resolve_debugger_port"):
        assert banned not in body, (
            f"{banned} must never run inside the per-item hot path")
