"""ROUND 18 - WINDOWS COMPATIBILITY PATCH (fixes 1-4).

FIX 1  Windows PowerShell 5.1 writes a UTF-8 BOM for -Encoding UTF8; the
       Python reader used plain utf-8 and would reject it.
FIX 2  the launcher hid PowerShell write errors and only checked that a
       file existed, so a stale file produced a false [OK].
FIX 3  app.py logged a hardcoded 127.0.0.1:9222 regardless of the port.
FIX 4  reported duplicate byte counter - see the NOT REPRODUCIBLE test.

Launcher assertions are STATIC: there is no Windows host or cmd.exe here,
so the .cmd is verified by structure, never by execution.
"""
import json
import re
import socket
import threading
from pathlib import Path

import pytest

from cghs.session import (
    CHROME_CONFIG_ENV,
    CHROME_PORT_ENV,
    DEFAULT_DEBUGGER_PORT,
    load_chrome_config,
    probe_debugger_endpoint,
    resolve_debugger_address,
    resolve_debugger_port,
)

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "app.py").read_text(encoding="utf-8", errors="replace")
LAUNCHER = (REPO / "START_CGHS_CHROME.cmd").read_text(
    encoding="utf-8", errors="replace")
SESSION = (REPO / "cghs" / "session.py").read_text(encoding="utf-8")


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    """An isolated shared-configuration file."""
    monkeypatch.delenv(CHROME_PORT_ENV, raising=False)
    path = tmp_path / "chrome_config.json"
    monkeypatch.setenv(CHROME_CONFIG_ENV, str(path))
    return path


# ======================================================================
# FIX 1 - BOM tolerance, both layers
# ======================================================================
def test_1_plain_utf8_json_still_works(cfg):
    cfg.write_text(json.dumps({"host": "127.0.0.1", "port": 9333}),
                   encoding="utf-8")
    assert resolve_debugger_port() == 9333


def test_1_utf8_with_bom_is_accepted(cfg):
    """What Windows PowerShell 5.1 actually produces for -Encoding UTF8."""
    cfg.write_text(json.dumps({"host": "127.0.0.1", "port": 9333}),
                   encoding="utf-8-sig")
    raw = cfg.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "fixture must really carry a BOM"
    assert resolve_debugger_port() == 9333, (
        "a BOM-prefixed config written by PowerShell 5.1 must be readable")
    assert resolve_debugger_address() == "127.0.0.1:9333"


@pytest.mark.parametrize("port", [9222, 9333, 9444])
def test_1_bom_config_round_trips_for_each_port(cfg, port):
    cfg.write_text(json.dumps({"host": "127.0.0.1", "port": port}),
                   encoding="utf-8-sig")
    assert resolve_debugger_port() == port


def test_1_corrupt_json_fails_loudly_not_silently(cfg):
    cfg.write_text("{ not json at all", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        resolve_debugger_port()
    assert str(cfg) in str(err.value), "the diagnostic must name the file"
    assert "json" in str(err.value).lower()


def test_1_an_empty_file_is_an_actionable_error_not_a_crash(cfg):
    cfg.write_text("", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        resolve_debugger_port()
    message = str(err.value).lower()
    assert "empty" in message or "json" in message
    assert str(cfg) in str(err.value)


def test_1_a_bom_only_file_is_treated_as_empty(cfg):
    cfg.write_bytes(b"\xef\xbb\xbf")
    with pytest.raises(ValueError) as err:
        resolve_debugger_port()
    assert str(cfg) in str(err.value)


def test_1_a_missing_port_key_keeps_the_default(cfg):
    cfg.write_text(json.dumps({"host": "127.0.0.1"}), encoding="utf-8")
    assert resolve_debugger_port() == DEFAULT_DEBUGGER_PORT


@pytest.mark.parametrize("bad", [0, 65536, -1, "abc", None, 80.5])
def test_1_invalid_port_values_are_rejected(cfg, bad):
    cfg.write_text(json.dumps({"host": "127.0.0.1", "port": bad}),
                   encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_debugger_port()


def test_1_never_falls_back_to_9222_on_a_parse_failure(cfg):
    """A silent fallback is how the two sides end up on different ports."""
    cfg.write_text("{ broken", encoding="utf-8")
    try:
        result = resolve_debugger_port()
    except ValueError:
        return                                   # correct behaviour
    pytest.fail(f"parse failure silently returned port {result}")


def test_1_python_reader_is_bom_tolerant_by_construction():
    assert 'encoding="utf-8-sig"' in SESSION, (
        "load_chrome_config must read with utf-8-sig so a PowerShell 5.1 "
        "BOM does not break JSON parsing")


def test_1_launcher_writes_utf8_without_a_bom():
    """-Encoding UTF8 emits a BOM on Windows PowerShell 5.1."""
    assert "UTF8Encoding" in LAUNCHER, (
        "the launcher must use an explicit BOM-free UTF-8 encoder")
    assert re.search(r"UTF8Encoding\(\s*\$false\s*\)", LAUNCHER), (
        "UTF8Encoding must be constructed with $false (no BOM)")
    assert "WriteAllText" in LAUNCHER, (
        "use [System.IO.File]::WriteAllText, which honours the encoder in "
        "both Windows PowerShell 5.1 and PowerShell 7")
    assert not re.search(r"Set-Content\s+-Encoding\s+UTF8\b",
                         _code_only(LAUNCHER)), (
        "Set-Content -Encoding UTF8 writes a BOM on PowerShell 5.1")


# ======================================================================
# FIX 2 - the configuration write must be verified, not assumed
# ======================================================================
def _code_only(text):
    """Drop REM comment lines - prose must not satisfy or break a test."""
    return "\n".join(line for line in text.splitlines()
                      if not line.strip().upper().startswith("REM"))


def _config_block():
    """The whole configuration-write path: call site + :WRITE_CONFIG.

    The write itself lives in a subroutine so both it and the caller are
    inspected; checking only the call site would miss the implementation.
    """
    start = LAUNCHER.find("PERSIST the port")
    assert start > 0, "configuration write block not found"
    nxt = re.search(r"\nREM ---- \d+\.", LAUNCHER[start:])
    assert nxt
    call_site = LAUNCHER[start:start + nxt.start()]

    sub_start = LAUNCHER.find(":WRITE_CONFIG")
    assert sub_start > 0, "the :WRITE_CONFIG subroutine is missing"
    sub_end = LAUNCHER.find("goto :eof", sub_start)
    assert sub_end > sub_start
    return call_site + "\n" + LAUNCHER[sub_start:sub_end]


def test_2_configuration_is_written_to_a_temporary_file_first():
    block = _config_block()
    assert ".tmp" in block or "TMP_CONFIG" in block, (
        "write to a temporary file so a failure cannot corrupt the live one")


def test_2_the_written_configuration_is_read_back_and_validated():
    block = _config_block()
    assert "ConvertFrom-Json" in block, (
        "the launcher must parse what it wrote, not just check it exists")


def test_2_the_saved_port_is_compared_with_the_requested_port():
    block = _config_block()
    assert "DEBUG_PORT" in block
    assert re.search(r"(-ne|NEQ|-eq)", block), (
        "the launcher must compare the saved port against DEBUG_PORT")


def test_2_the_replacement_is_atomic():
    block = _config_block()
    assert "Move-Item" in block or "::Replace" in block or "::Move" in block, (
        "the temporary file must atomically replace the live configuration")


def test_2_a_write_failure_stops_the_launcher():
    block = _config_block()
    assert "CONFIGURATION WRITE FAILED" in block, (
        "a failed write must be reported with that exact banner")
    failure_at = block.find("CONFIGURATION WRITE FAILED")
    assert re.search(r"exit /b [1-9]", block[failure_at:]), (
        "a failed configuration write must return a non-zero exit code")


def test_2_chrome_is_not_launched_after_a_failed_write():
    """The failure must exit before the Chrome launch section."""
    fail_at = LAUNCHER.find("CONFIGURATION WRITE FAILED")
    launch_at = LAUNCHER.find("--user-data-dir")
    assert 0 < fail_at < launch_at, (
        "the configuration failure path must come before Chrome is started")
    between = LAUNCHER[fail_at:launch_at]
    assert re.search(r"exit /b [1-9]", between), (
        "there must be a non-zero exit between the failure and the launch")


def test_2_powershell_errors_are_no_longer_blindly_discarded():
    block = _config_block()
    assert "ERRORLEVEL" in block or "errorlevel" in block, (
        "the launcher must check the exit status of the write")


def test_2_no_false_ok_from_a_stale_file():
    """An [OK] must depend on verification, not on mere existence."""
    block = _code_only(_config_block())
    ok_at = block.find("[OK]")
    assert ok_at > 0, "no success message found"
    before = block[:ok_at]
    assert "CONFIG_WRITE_OK" in before, (
        "the success message must be gated on the verified write result, "
        "not on the file merely existing")
    # Reading an existing config to discover the current port is fine; what
    # must not exist is a SUCCESS message gated only on the file existing.
    assert 'if exist "%CONFIG_FILE%" (' not in block, (
        "the write block still reports success from mere file existence, "
        "so a stale file would pass verification")


# ======================================================================
# FIX 3 - the log must show the port actually in use
# ======================================================================
def test_3_no_hardcoded_endpoint_in_the_connection_log():
    assert "127.0.0.1:9222" not in APP, (
        "the attach log hardcodes an endpoint and becomes wrong on a "
        "custom port")


def test_3_the_log_uses_the_shared_resolver():
    m = re.search(r"def run\(self\):(.*?)\n    def ", APP, re.DOTALL)
    assert m, "BatchAutomationThread.run not found"
    body = m.group(1)
    assert "resolve_debugger_address" in body, (
        "the log must report the resolved endpoint")
    assert "attaching to Chrome debug" in body


def test_3_the_logged_endpoint_is_the_one_actually_attached(cfg):
    """Resolve once, log it AND pass it to attach - they cannot diverge."""
    m = re.search(r"def run\(self\):(.*?)\n    def ", APP, re.DOTALL)
    body = m.group(1)
    assert "debugger_address=" in body, (
        "the resolved address must be handed to attach(), so the logged "
        "endpoint is provably the one used")


def test_3_the_resolver_reflects_a_changed_configuration(cfg):
    cfg.write_text(json.dumps({"port": 9333}), encoding="utf-8")
    assert resolve_debugger_address() == "127.0.0.1:9333"
    cfg.write_text(json.dumps({"port": 9444}), encoding="utf-8-sig")
    assert resolve_debugger_address() == "127.0.0.1:9444"


def test_3_no_second_source_of_port_configuration_was_added():
    assert APP.count("resolve_debugger_address") <= 2, (
        "the endpoint must be resolved once per batch, not re-derived")
    for banned in ("9222", "9333"):
        assert banned not in APP, f"app.py hardcodes port {banned}"


def test_3_no_selenium_call_was_added_for_the_log():
    m = re.search(r"def run\(self\):(.*?)\n    def ", APP, re.DOTALL)
    body = m.group(1)
    log_line = [l for l in body.splitlines()
                if "attaching to Chrome debug" in l][0]
    for banned in ("driver", "execute_script", "find_element", "probe_"):
        assert banned not in log_line, (
            f"the log line must not perform a {banned} call")


# ======================================================================
# FIX 4 - reported duplicate counter: NOT REPRODUCIBLE, plus a real guard
# ======================================================================
def test_4_the_response_counter_is_incremented_exactly_once():
    """Reported as duplicated; the source has a single increment.

    This test pins the correct behaviour so the reported defect cannot be
    introduced later.
    """
    probe = SESSION[SESSION.index("def probe_debugger_endpoint("):]
    probe = probe[:probe.index("\ndef ")]
    increments = re.findall(r"total\s*\+=\s*len\(chunk\)", probe)
    assert len(increments) == 1, (
        f"expected exactly one byte-counter increment, found "
        f"{len(increments)}")


def test_4_the_byte_limit_is_enforced_against_the_real_count(monkeypatch):
    """A flood of data must stop at the cap instead of being read forever."""
    import cghs.session as session

    monkeypatch.setattr(session, "_MAX_PROBE_RESPONSE_BYTES", 4096,
                        raising=True)

    served = {"sent": 0}
    ready = threading.Event()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    def serve():
        ready.set()
        conn, _ = srv.accept()
        try:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json"
                         b"\r\n\r\n")
            blob = b"x" * 8192
            for _ in range(200):               # ~1.6 MB if never bounded
                conn.sendall(blob)
                served["sent"] += len(blob)
        except OSError:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    ready.wait(2)
    try:
        ok, detail = probe_debugger_endpoint(f"127.0.0.1:{port}", timeout=5.0)
    finally:
        srv.close()

    assert ok is False, "a junk flood must not be accepted as Chrome"
    assert served["sent"] < 200 * 8192, (
        "the probe kept reading - the size limit was not enforced")


def test_4_the_size_limit_is_a_named_constant():
    assert "_MAX_PROBE_RESPONSE_BYTES" in SESSION, (
        "the response cap should be a named constant, not a magic number")


def test_4_a_normal_chrome_response_is_unaffected():
    """The cap must not change ordinary endpoint behaviour."""
    import http.server
    import socketserver

    body = json.dumps({
        "Browser": "Chrome/141.0.7390.65",
        "Protocol-Version": "1.3",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x",
    }).encode()

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    addr = f"127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ok, detail = probe_debugger_endpoint(addr, timeout=3.0)
    finally:
        srv.shutdown()
        srv.server_close()
    assert ok is True, detail
    assert "Chrome" in detail
