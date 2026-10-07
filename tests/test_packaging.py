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


# ---------------------------------------------------------------------------
# ROUND 15 - BUG #5 / BUG #6: the artifact must contain what the tests need,
# and the build must be reproducible.
#
# Reproduced before the fix, by running tools/build_package.py in a clean
# worktree:
#
#   * INCLUDE_GLOBS had no tests/fixtures/* entry, so the real-bill specimen
#     tests/fixtures/39538.pdf was NOT in the builder's archive, while the
#     shipped git-archive ZIP DID contain it - two divergent artifacts;
#   * the builder still printed "PACKAGING: PASS";
#   * the extracted archive ran the suite with 31 skips instead of 8: all 23
#     real-39538 regressions reported "ENVIRONMENT_BLOCKED: 39538.pdf is not
#     in the repository" and the artifact still looked green.  A green run
#     that silently stopped exercising the real document is a false pass;
#   * requirements.txt was listed in INCLUDE_GLOBS but did not exist, and
#     BUILD_WINDOWS.cmd installed `--upgrade pyinstaller selenium PyQt5
#     PyMuPDF`, i.e. whatever was latest on build day, never the tested set.
# ---------------------------------------------------------------------------

import hashlib                                                  # noqa: E402
import zipfile                                                  # noqa: E402

REQUIREMENTS = REPO / "requirements.txt"
REAL_BILL_FIXTURE = "tests/fixtures/39538.pdf"
REAL_BILL_SHA256 = "1825f727981314ec5b6241f967ee9e26ee1bd76e4cbac96b5ceb4404da04993a"


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_the_builder_declares_the_real_bill_fixture_as_required():
    builder = (REPO / "tools" / "build_package.py").read_text(encoding="utf-8")
    assert "tests/fixtures" in builder, (
        "the regression suite reads tests/fixtures/39538.pdf; the builder must "
        "ship it or the artifact silently loses the real-PDF regression")


def test_the_builder_verifies_required_fixture_hashes():
    builder = (REPO / "tools" / "build_package.py").read_text(encoding="utf-8")
    assert REAL_BILL_SHA256 in builder, (
        "a required fixture must be hash-verified, not merely present")


def test_the_workspace_fixture_matches_the_git_specimen_hash():
    fixture = REPO / REAL_BILL_FIXTURE
    assert fixture.exists(), REAL_BILL_FIXTURE
    assert _sha256(fixture) == REAL_BILL_SHA256


@pytest.mark.skipif(not (REPO / ZIP_NAME).exists(),
                    reason="ZIP not built yet - run tools/build_package.py")
def test_the_delivery_zip_contains_the_real_bill_fixture():
    with zipfile.ZipFile(REPO / ZIP_NAME) as archive:
        names = set(archive.namelist())
        assert REAL_BILL_FIXTURE in names, sorted(n for n in names if "fixture" in n)
        assert hashlib.sha256(
            archive.read(REAL_BILL_FIXTURE)).hexdigest() == REAL_BILL_SHA256


def test_a_single_authoritative_dependency_manifest_exists():
    assert REQUIREMENTS.exists(), (
        "tools/build_package.py already ships requirements.txt and "
        "BUILD_WINDOWS.cmd needs it; it must actually exist")
    body = REQUIREMENTS.read_text(encoding="utf-8")
    assert "pymupdf==" in body.lower()
    assert "selenium==" in body.lower()


def test_the_manifest_pins_the_versions_the_tests_actually_ran_against():
    """No guessed versions: every pin is the version installed in this run."""
    import importlib.metadata as md

    body = REQUIREMENTS.read_text(encoding="utf-8")
    pins = {}
    for line in body.splitlines():
        line = line.split("#")[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()

    for name in ("pymupdf", "selenium", "pytest"):
        assert name in pins, f"{name} must be pinned"
        assert pins[name] == md.version(name), (
            f"{name} pinned at {pins[name]} but the suite ran against "
            f"{md.version(name)} - a pin must describe the tested environment")


def test_the_windows_build_consumes_the_manifest_and_does_not_duplicate_it():
    script = (REPO / "BUILD_WINDOWS.cmd").read_text(encoding="utf-8", errors="replace")
    assert "requirements.txt" in script, (
        "the Windows build must install the declared set, not its own list")
    assert "--upgrade pyinstaller selenium PyQt5 PyMuPDF" not in script, (
        "an unpinned --upgrade install makes the Windows build irreproducible")


def test_no_secret_cache_or_user_data_is_packaged():
    """Shipping the fixture must not become an excuse to ship everything.

    ``docs/evidence/transaction_journal.baseline.jsonl`` is deliberately
    included: it is committed baseline EVIDENCE, not a runtime journal of a
    real patient.  The runtime artifact is ``transaction_journal.jsonl`` and
    that one is forbidden.
    """
    if not (REPO / ZIP_NAME).exists():
        pytest.skip("ZIP not built yet")
    with zipfile.ZipFile(REPO / ZIP_NAME) as archive:
        for name in archive.namelist():
            lowered = name.lower()
            for forbidden in ("__pycache__", ".pyc", ".git/", ".env",
                              "credential", "/transaction_journal.jsonl",
                              ".sha256"):
                assert forbidden not in f"/{lowered}", name
        pdfs = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
        assert pdfs == ["tests/fixtures/39538.pdf"], (
            "only the declared regression fixture may be shipped", pdfs)
