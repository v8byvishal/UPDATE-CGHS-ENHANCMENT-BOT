"""Load the BASELINE ``app.py`` automation straight out of git.

The before/after performance numbers in ``docs/AUTOMATION_PERFORMANCE_HARDENING.md``
compare the new ``cghs`` package against the *real* baseline code, not a hand
written approximation of it: the source is read with
``git show <baseline>:app.py`` and executed with PyQt5/PyMuPDF stubbed out.

If the baseline object is ever unavailable the benchmark must report
ENVIRONMENT_BLOCKED - it must never fabricate a "before" number.
"""

from __future__ import annotations

import pathlib
import hashlib
import subprocess
import sys
import types
from pathlib import Path
from typing import Optional

BASELINE_COMMIT = "c3ccdf32e2170891fad9b150c850053461c85a25"
REPO_ROOT = Path(__file__).resolve().parents[2]

_cache: dict = {}


class _StubBase:
    """Base for every stubbed Qt class - accepts anything, does nothing."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, item):
        return _StubBase()

    def __call__(self, *args, **kwargs):
        return _StubBase()


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        stub = type(name, (_StubBase,), {})
        setattr(self, name, stub)
        return stub


def _install_stubs():
    for name in ("fitz", "PyQt5", "PyQt5.QtWidgets", "PyQt5.QtCore", "PyQt5.QtGui"):
        if name not in sys.modules:
            sys.modules[name] = _StubModule(name)


#: SHA-256 of the baseline ``app.py`` blob at BASELINE_COMMIT.  Both the git
#: blob and the shipped snapshot are checked against this, so the differential
#: tests can never silently compare against the wrong reference.
BASELINE_APP_SHA256 = "84325c0cf1b463d1cd98b2a295ce66cc678ae73eb212a2feabda3c7fe7c8a285"

#: Byte-identical copy of that blob, shipped so the differential parser tests
#: still run from an extracted release archive (which has no .git directory).
BASELINE_SNAPSHOT = pathlib.Path(__file__).resolve().parent / "baseline_app_c3ccdf3.py.txt"


class BaselineUnavailable(RuntimeError):
    """Neither the git blob nor the shipped snapshot could be read."""


def baseline_source(commit: str = BASELINE_COMMIT) -> str:
    """Return the ORIGINAL ``app.py``, verified byte-for-byte.

    Prefers the git blob; falls back to the shipped snapshot so the tests work
    from an extracted ZIP.  Either way the bytes must hash to
    ``BASELINE_APP_SHA256`` - a differential test that compared against the
    wrong source would prove nothing.
    """
    data = None
    errors = []

    try:
        out = subprocess.run(["git", "show", f"{commit}:app.py"], cwd=str(REPO_ROOT),
                             capture_output=True, check=True)
        data = out.stdout
    except Exception as exc:                                # noqa: BLE001
        errors.append(f"git: {type(exc).__name__}: {exc}")

    if data is None and BASELINE_SNAPSHOT.exists():
        try:
            data = BASELINE_SNAPSHOT.read_bytes()
        except Exception as exc:                            # noqa: BLE001
            errors.append(f"snapshot: {type(exc).__name__}: {exc}")

    if data is None:
        raise BaselineUnavailable(
            f"baseline app.py unavailable at {commit}: " + "; ".join(errors))

    if commit == BASELINE_COMMIT:
        digest = hashlib.sha256(data).hexdigest()
        if digest != BASELINE_APP_SHA256:
            raise BaselineUnavailable(
                f"baseline app.py integrity check FAILED: expected "
                f"{BASELINE_APP_SHA256}, got {digest}")

    return data.decode("utf-8", errors="replace")


def load_baseline(commit: str = BASELINE_COMMIT) -> types.ModuleType:
    """Execute the baseline ``app.py`` in an isolated module namespace."""
    if commit in _cache:
        return _cache[commit]
    _install_stubs()
    source = baseline_source(commit)
    module = types.ModuleType(f"legacy_app_{commit[:7]}")
    module.__file__ = f"<git:{commit}:app.py>"
    compiled = compile(source, module.__file__, "exec")
    exec(compiled, module.__dict__)                      # noqa: S102 - deliberate
    _cache[commit] = module
    return module


class SilentSignal:
    """``pyqtSignal``-compatible sink used for the baseline logger."""

    def __init__(self, collect: Optional[list] = None):
        self.records = collect if collect is not None else []

    def emit(self, message):
        self.records.append(message)
