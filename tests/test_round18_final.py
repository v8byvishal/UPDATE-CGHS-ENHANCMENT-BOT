"""ROUND 18 - FINAL CORRECTIVE PATCH (defects 1-3).

FIX 1  closeEvent() accepted the close even when the probe thread refused
       to stop, destroying a running QThread.
FIX 2  the launcher's existing-port branch returned 0 for ANY service.
FIX 3  a custom port was not reliably shared with the Python application.

The shutdown tests execute the REAL decision logic (``app_shutdown``) with
stand-in threads, including the wait-failure branch - they do not merely
assert that stop() and wait() appear in the source.
"""
import json
import re
from pathlib import Path

import pytest

from app_shutdown import plan_shutdown, ShutdownDecision
from cghs.session import (
    CHROME_PORT_ENV,
    CHROME_CONFIG_ENV,
    DEFAULT_DEBUGGER_PORT,
    chrome_config_path,
    load_chrome_config,
    resolve_debugger_address,
    resolve_debugger_port,
)

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "app.py").read_text(encoding="utf-8", errors="replace")
LAUNCHER = (REPO / "START_CGHS_CHROME.cmd").read_text(
    encoding="utf-8", errors="replace")


# ======================================================================
# Stand-in threads.  wait() returns False to model "did not finish".
# ======================================================================
class FakeThread:
    def __init__(self, running=True, stops=True):
        self._running = running
        self._stops = stops
        self.stop_calls = 0
        self.wait_calls = 0

    def isRunning(self):
        return self._running

    def stop(self):
        self.stop_calls += 1
        if self._stops:
            self._running = False

    def wait(self, ms=0):
        self.wait_calls += 1
        return not self._running


# ----------------------------------------------------------------------
# FIX 1 - the six required shutdown scenarios
# ----------------------------------------------------------------------
def test_1_both_threads_stop_cleanly_the_window_closes():
    worker, probe = FakeThread(stops=True), FakeThread(stops=True)
    d = plan_shutdown(worker, probe, wait_ms=10)
    assert d.accept is True
    assert d.clear_probe is True
    assert d.stage == "ALL_STOPPED"
    assert probe.stop_calls == 1


def test_1_probe_that_does_not_stop_blocks_the_close():
    """The reported defect: the window closed anyway."""
    worker, probe = FakeThread(stops=True), FakeThread(stops=False)
    d = plan_shutdown(worker, probe, wait_ms=10)
    assert d.accept is False, (
        "a probe that refused to stop must NOT allow the window to close - "
        "its QThread would be destroyed while running")
    assert d.clear_probe is False, (
        "the probe reference must be preserved, not discarded, while the "
        "thread is still alive")
    assert d.stage == "PROBE_BUSY"
    assert probe.stop_calls == 1 and probe.wait_calls == 1


def test_1_worker_still_running_blocks_the_close_before_touching_the_probe():
    worker, probe = FakeThread(stops=False), FakeThread(stops=True)
    d = plan_shutdown(worker, probe, wait_ms=10)
    assert d.accept is False
    assert d.stage == "WORKER_BUSY"
    assert probe.stop_calls == 0, (
        "the probe must not be stopped until the worker has finished")


def test_1_worker_finished_but_probe_running():
    worker, probe = FakeThread(running=False), FakeThread(stops=False)
    d = plan_shutdown(worker, probe, wait_ms=10)
    assert d.accept is False
    assert d.stage == "PROBE_BUSY"
    assert d.clear_probe is False


def test_1_second_close_attempt_succeeds_once_the_thread_finishes():
    worker, probe = FakeThread(stops=True), FakeThread(stops=False)
    first = plan_shutdown(worker, probe, wait_ms=10)
    assert first.accept is False and first.clear_probe is False

    probe._stops = True                      # the thread finally exits
    second = plan_shutdown(worker, probe, wait_ms=10)
    assert second.accept is True, "a retried close must succeed"
    assert second.clear_probe is True
    assert probe.stop_calls == 2


def test_1_idle_application_closes_immediately():
    d = plan_shutdown(None, None, wait_ms=10)
    assert d.accept is True and d.stage == "ALL_STOPPED"


def test_1_a_blocked_shutdown_keeps_the_app_in_shutdown_state():
    """No new batch may start while a close is pending."""
    d = plan_shutdown(FakeThread(stops=False), None, wait_ms=10)
    assert d.accept is False
    assert d.keep_shutdown_latch is True


def test_1_shutdown_never_force_terminates():
    worker = FakeThread(stops=False)
    plan_shutdown(worker, FakeThread(stops=False), wait_ms=10)
    assert not hasattr(worker, "terminated")
    src = (REPO / "app_shutdown.py").read_text(encoding="utf-8")
    assert "terminate" not in src.replace("force-terminate", "") \
        .replace("terminated", ""), "shutdown must never force-terminate"


def test_1_close_event_delegates_to_the_verified_logic():
    """The GUI must use the logic these tests exercise, not a copy of it."""
    m = re.search(r"def closeEvent\(self, event\):(.*?)(\n\nif __name__|\n    def )",
                  APP, re.DOTALL)
    assert m, "closeEvent not found"
    body = m.group(1)
    assert "plan_shutdown" in body, (
        "closeEvent must call the shared, tested shutdown logic")
    assert "event.ignore()" in body and "event.accept()" in body
    assert "self.chrome_probe = None" not in body or "clear_probe" in body, (
        "the probe reference must only be cleared when the thread is gone")


# ----------------------------------------------------------------------
# FIX 2 - launcher exit codes and a single verification routine
# ----------------------------------------------------------------------
def _existing_port_branch():
    """The block that runs when something already holds the port."""
    start = LAUNCHER.find("ALREADY listening")
    assert start > 0, "existing-port branch not found"
    nxt = re.search(r"\nREM ---- \d+\.", LAUNCHER[start:])
    assert nxt, "could not find the end of the existing-port branch"
    return LAUNCHER[start:start + nxt.start()]


def test_2_existing_port_branch_never_blindly_returns_success():
    branch = _existing_port_branch()
    codes = re.findall(r"exit /b (\d+)", branch)
    assert codes, "the existing-port branch returns no explicit exit code"
    assert any(c != "0" for c in codes), (
        "the existing-port branch can only exit 0 - a foreign service on the "
        "port is reported as success")


def test_2_a_foreign_service_is_reported_as_a_port_conflict():
    branch = _existing_port_branch()
    assert "PORT CONFLICT" in branch, (
        "a non-Chrome service must be named as a port conflict")
    conflict_at = branch.find("PORT CONFLICT")
    after = branch[conflict_at:]
    assert re.search(r"exit /b [1-9]", after), (
        "the PORT CONFLICT path must return a non-zero exit code")


def test_2_existing_chrome_is_accepted_with_a_ready_banner():
    """A verified existing Chrome shows the shared READY banner, exit 0."""
    branch = _existing_port_branch()
    assert "call :SHOW_READY" in branch, (
        "an already-running Chrome must be recognised as ready using the "
        "shared banner routine")
    ready_at = branch.find("call :SHOW_READY")
    assert re.search(r"exit /b 0", branch[ready_at:]), (
        "the verified-Chrome path must exit 0")
    assert "CHROME DEVTOOLS READY" in LAUNCHER


def test_2_one_shared_verification_routine_is_used_everywhere():
    assert ":VERIFY_DEVTOOLS" in LAUNCHER, (
        "expected a single reusable DevTools verification routine")
    calls = len(re.findall(r"call :VERIFY_DEVTOOLS", LAUNCHER))
    assert calls >= 2, (
        f"the verification routine is called {calls} time(s); both the "
        f"already-running and newly-launched paths must use it")
    # the full check must live in exactly one place
    assert LAUNCHER.count("webSocketDebuggerUrl -notmatch") <= 1, (
        "DevTools validation is duplicated - there must be one implementation")


def test_2_launcher_distinguishes_its_failure_modes():
    for banner in ("CHROME DEVTOOLS READY", "PORT CONFLICT", "CONNECTION FAILED"):
        assert banner in LAUNCHER, f"missing operator-facing state: {banner}"


# ----------------------------------------------------------------------
# FIX 3 - one persistent configuration, shared by launcher and Python
# ----------------------------------------------------------------------
@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv(CHROME_PORT_ENV, raising=False)
    cfg = tmp_path / "chrome_config.json"
    monkeypatch.setenv(CHROME_CONFIG_ENV, str(cfg))
    return cfg


def test_3_default_is_9222_when_nothing_is_configured(clean_env):
    assert resolve_debugger_port() == DEFAULT_DEBUGGER_PORT == 9222
    assert resolve_debugger_address() == "127.0.0.1:9222"


@pytest.mark.parametrize("port", [9333, 9444])
def test_3_a_custom_port_in_the_shared_file_is_used(clean_env, port):
    clean_env.write_text(json.dumps({"host": "127.0.0.1", "port": port}))
    assert resolve_debugger_port() == port
    assert resolve_debugger_address() == f"127.0.0.1:{port}"


def test_3_the_config_file_wins_over_a_stale_environment_variable(
        clean_env, monkeypatch):
    """setx does not update the running shell - the file is authoritative."""
    monkeypatch.setenv(CHROME_PORT_ENV, "9222")
    clean_env.write_text(json.dumps({"host": "127.0.0.1", "port": 9333}))
    assert resolve_debugger_port() == 9333, (
        "a stale environment variable must not override the shared file")


def test_3_configuration_changed_between_runs_is_picked_up(clean_env):
    clean_env.write_text(json.dumps({"port": 9333}))
    assert resolve_debugger_port() == 9333
    clean_env.write_text(json.dumps({"port": 9444}))
    assert resolve_debugger_port() == 9444, "the file is re-read each time"


@pytest.mark.parametrize("bad", [0, 65536, -1, 99999, "abc", None, 80.5])
def test_3_invalid_ports_in_the_file_raise_clearly(clean_env, bad):
    clean_env.write_text(json.dumps({"host": "127.0.0.1", "port": bad}))
    with pytest.raises(ValueError) as err:
        resolve_debugger_port()
    assert "port" in str(err.value).lower()


def test_3_a_corrupt_config_file_raises_instead_of_falling_back(clean_env):
    clean_env.write_text("{ this is not json")
    with pytest.raises(ValueError) as err:
        resolve_debugger_port()
    message = str(err.value).lower()
    assert "json" in message or "read" in message
    assert str(clean_env) in str(err.value), "the error must name the file"


def test_3_a_non_object_config_file_is_rejected(clean_env):
    clean_env.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        resolve_debugger_port()


def test_3_a_non_loopback_host_in_the_file_is_rejected(clean_env):
    clean_env.write_text(json.dumps({"host": "10.0.0.9", "port": 9222}))
    with pytest.raises(ValueError):
        resolve_debugger_address()


def test_3_load_chrome_config_reports_its_source(clean_env):
    assert load_chrome_config()["source"] == "default"
    clean_env.write_text(json.dumps({"port": 9333}))
    loaded = load_chrome_config()
    assert loaded["port"] == 9333
    assert loaded["source"] == "file"


def test_3_the_config_path_lives_beside_the_chrome_profile(monkeypatch):
    monkeypatch.delenv(CHROME_CONFIG_ENV, raising=False)
    path = chrome_config_path()
    assert path.name == "chrome_config.json"
    assert "CGHS-Enhancement-Bot" in str(path)


def test_3_attach_resolves_the_shared_configuration(clean_env):
    """PortalSession.attach() must not pin a hardcoded port."""
    import inspect
    from cghs.session import PortalSession
    src = inspect.getsource(PortalSession.attach)
    assert "resolve_debugger_address()" in src
    assert "9222" not in src, "attach() must not hardcode a port"


def test_3_probe_uses_the_shared_configuration_by_default(clean_env):
    import inspect
    from cghs.session import probe_debugger_endpoint
    src = inspect.getsource(probe_debugger_endpoint)
    assert "resolve_debugger_address()" in src


def test_3_no_module_hardcodes_the_port_outside_the_config_block():
    """One source of truth: 9222 may appear only where it is defined."""
    session = (REPO / "cghs" / "session.py").read_text(encoding="utf-8")
    body = session[session.index("def resolve_debugger_port"):]
    assert "9222" not in body, (
        "9222 appears after the configuration block - it must be defined "
        "once, as DEFAULT_DEBUGGER_PORT")
    for name in ("orchestrator.py", "dom.py", "tabs.py"):
        text = (REPO / "cghs" / name).read_text(encoding="utf-8")
        assert "9222" not in text, f"{name} hardcodes a debugging port"


# ---- launcher/Python agreement ---------------------------------------
def test_3_launcher_writes_the_same_configuration_file():
    assert "chrome_config.json" in LAUNCHER, (
        "the launcher must persist the port where Python reads it")
    assert "CGHS-Enhancement-Bot" in LAUNCHER


def test_3_launcher_writes_the_config_before_probing_the_port():
    """Requirement 4: persist the port BEFORE checking whether Chrome runs."""
    write_at = LAUNCHER.find("chrome_config.json")
    check_at = LAUNCHER.find("ALREADY listening")
    assert 0 < write_at < check_at, (
        "the shared configuration must be written before the existing-port "
        "check, otherwise that branch exits before the port is synchronised")


def test_3_launcher_validates_the_port_range():
    assert "65535" in LAUNCHER
    assert re.search(r"exit /b [1-9]", LAUNCHER)


def test_3_launcher_does_not_rely_on_setx_alone():
    if "setx" in LAUNCHER.lower():
        assert "chrome_config.json" in LAUNCHER, (
            "setx does not affect the running shell; a persistent file is "
            "required for the application to agree on the port")
