"""E. PACKAGING + canonical-ownership gates.

These tests enforce acceptance gate A (one canonical implementation, no
duplicated engines) and the structural half of gate E.  The ZIP itself is
built and reopened by ``tools/build_package.py``; the test here runs that same
verification so a broken artifact fails the suite rather than the reader.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
ZIP_NAME = "CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip"

PACKAGE_MODULES = sorted(p.name for p in (REPO / "cghs").glob("*.py"))

#: a class name may be DEFINED in exactly one module
ENGINE_CLASSES = [
    "TreatmentPlanOrchestrator", "BatchRunner", "PortalSession", "SmartDOMResolver",
    "PortalSynchronizer", "FrameContextCache", "PatientTabResolver", "TableReader",
    "CommitVerifier", "PlusTransaction", "DispatchLedger", "CGHSParsingEngine",
    "EnterpriseLogger", "PerfCounters", "PlusButtonController",
]


def python_sources():
    for path in sorted((REPO / "cghs").rglob("*.py")):
        yield path
    yield REPO / "app.py"


def test_every_module_parses():
    for path in python_sources():
        ast.parse(path.read_text(encoding="utf-8"), str(path))


def test_no_class_is_defined_twice_across_the_codebase():
    """Gate A: exactly one canonical owner per engine class."""
    locations = {}
    for path in python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                locations.setdefault(node.name, []).append(
                    f"{path.relative_to(REPO)}:{node.lineno}")
    duplicates = {name: where for name, where in locations.items() if len(where) > 1}
    assert duplicates == {}, duplicates


def test_engine_classes_live_in_the_package_not_in_app():
    app_tree = ast.parse((REPO / "app.py").read_text(encoding="utf-8"), "app.py")
    defined_in_app = {node.name for node in ast.walk(app_tree)
                      if isinstance(node, ast.ClassDef)}
    leaked = defined_in_app & set(ENGINE_CLASSES)
    assert leaked == set(), leaked


def test_no_function_is_defined_twice_in_the_package():
    locations = {}
    for path in sorted((REPO / "cghs").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in tree.body:                       # module level only
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                locations.setdefault(node.name, []).append(
                    f"{path.relative_to(REPO)}:{node.lineno}")
    duplicates = {name: where for name, where in locations.items()
                  if len(where) > 1 and not name.startswith("_")}
    assert duplicates == {}, duplicates


def test_the_package_imports_without_pyqt5_fitz_or_a_browser():
    """The automation core must be testable headless."""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys;"
         "blocked = ('PyQt5', 'PyQt5.QtWidgets', 'PyQt5.QtCore', 'PyQt5.QtGui', 'fitz');"
         "[sys.modules.setdefault(name, None) for name in blocked];"
         "import cghs, cghs.orchestrator, cghs.session, cghs.controllers,"
         " cghs.rules, cghs.parsing, cghs.locators, cghs.telemetry, cghs.dom,"
         " cghs.txstate, cghs.tabs;"
         "print(cghs.__version__)"],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip()


def test_app_py_keeps_crlf_line_endings():
    """The file shipped to the Windows build must not change line endings."""
    data = (REPO / "app.py").read_bytes()
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    assert crlf > 0
    assert bare_lf == 0, f"{bare_lf} bare LF line(s) introduced"


def test_pyinstaller_spec_collects_the_package():
    spec = (REPO / "CGHS_Enhancement_Bot.spec").read_text(encoding="utf-8")
    assert "collect_submodules" in spec
    assert "cghs" in spec


def test_app_py_has_no_forbidden_automation_primitives():
    forbidden = ("pyautogui", "pynput", "keyboard.press", "mouse.click",
                 "ImageGrab", "cv2.matchTemplate")
    for path in python_sources():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in forbidden:
            assert token.lower() not in text, f"{path.relative_to(REPO)} uses {token}"


def _executable_text(path: pathlib.Path) -> str:
    """Source with docstrings removed - i.e. what actually RUNS.

    Documentation is allowed to say "no login/MFA automation is performed";
    only executable code is subject to the ban.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    pieces = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                pieces.append(node.value)
        elif isinstance(node, ast.Name):
            pieces.append(node.id)
        elif isinstance(node, ast.Attribute):
            pieces.append(node.attr)
    return "\n".join(pieces).lower()


def test_no_credential_or_login_automation():
    patterns = ("password", "otp", "mfa", "cookie_jar", "add_cookie",
                "user-data-dir", "remote-debugging-port")
    offenders = []
    for path in python_sources():
        text = _executable_text(path)
        for token in patterns:
            if token in text:
                offenders.append(f"{path.relative_to(REPO)}: {token}")
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# the artifact itself
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not (REPO / ZIP_NAME).exists(),
                    reason="ZIP not built yet - run tools/build_package.py")
def test_the_delivery_zip_passes_its_own_reopen_verification():
    probe = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_package.py"), "--verify-only"],
        cwd=REPO, capture_output=True, text=True, timeout=600)
    assert probe.returncode == 0, probe.stdout[-4000:] + probe.stderr[-2000:]
    assert "PACKAGING: PASS" in probe.stdout
