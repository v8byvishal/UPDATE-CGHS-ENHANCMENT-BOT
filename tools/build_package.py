#!/usr/bin/env python3
"""Build - and then VERIFY BY REOPENING - the delivery ZIP.

The verification half is the point of this script.  A build that is not
reopened, listed, extracted and hashed has not been proven to contain what it
claims, so the exit status here depends on:

* every critical file is present in the archive,
* the extracted bytes match the workspace bytes (SHA-256 per file),
* ``app.py`` inside the archive is the HARDENED one (no baseline marker, no
  duplicated automation engine),
* no build intermediates, caches or journals leaked in.

Usage::

    python3 tools/build_package.py                 # build + verify
    python3 tools/build_package.py --verify-only   # verify an existing zip
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import zipfile
from typing import Dict, List, Tuple

REPO = pathlib.Path(__file__).resolve().parents[1]
ZIP_NAME = "CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip"

#: files that MUST be in the archive, byte-identical to the workspace
CRITICAL_FILES = [
    "app.py",
    "ui_theme.py",
    "CGHS_Enhancement_Bot.spec",
    "BUILD_WINDOWS.cmd",
    "cghs/__init__.py",
    "cghs/locators.py",
    "cghs/rules.py",
    "cghs/parsing.py",
    "cghs/telemetry.py",
    "cghs/dom.py",
    "cghs/txstate.py",
    "cghs/tabs.py",
    "cghs/controllers.py",
    "cghs/session.py",
    "cghs/orchestrator.py",
    "docs/AUTOMATION_PERFORMANCE_HARDENING.md",
    "requirements.txt",
]

#: Fixtures the regression suite READS.  Each is shipped and hash-verified.
#:
#: Without this, INCLUDE_GLOBS had no tests/fixtures entry, so the real-bill
#: specimen was absent from the archive while the shipped git-archive ZIP
#: contained it - two divergent artifacts from one commit.  Worse, the
#: extracted archive still looked green: the 23 real-39538 regressions simply
#: reported "ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository" and
#: skipped, so the artifact passed its suite while no longer exercising the
#: real document at all.  A fixture the tests require is part of the product.
REQUIRED_FIXTURES = {
    "tests/fixtures/39538.pdf":
        "1825f727981314ec5b6241f967ee9e26ee1bd76e4cbac96b5ceb4404da04993a",
}

INCLUDE_GLOBS = [
    "app.py",
    "ui_theme.py",
    "CGHS_Enhancement_Bot.spec",
    "BUILD_WINDOWS.cmd",
    "requirements.txt",
    ".gitignore",
    "pytest.ini",
    "conftest.py",
    "cghs/*.py",
    "tests/*.py",
    "tests/support/*.py",
    "tests/support/*.txt",
    "tests/fixtures/*.pdf",
    "tools/*.py",
    "docs/*.md",
    "docs/perf/*",
    "docs/evidence/*",
]

#: nothing matching these may ever enter the archive
FORBIDDEN_PARTS = (
    "__pycache__", ".pyc", ".pyo", "/build/", "/dist/", ".git/",
    "struct.pyc", "base_library.zip", "warn-", "xref-",
    "Analysis-00.toc", "EXE-00.toc", "PKG-00.toc", "PYZ-00",
    "transaction_journal.jsonl",
    # the verification report DESCRIBES this archive, so a copy inside it
    # would necessarily be from the previous build - keep it out
    "packaging_verification.json",
)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_files() -> List[pathlib.Path]:
    found: List[pathlib.Path] = []
    for pattern in INCLUDE_GLOBS:
        for path in sorted(REPO.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO).as_posix()
            if any(part in f"/{rel}" for part in FORBIDDEN_PARTS):
                continue
            if path not in found:
                found.append(path)
    return found


def build(zip_path: pathlib.Path) -> List[pathlib.Path]:
    files = collect_files()
    missing = [name for name in CRITICAL_FILES if not (REPO / name).exists()]
    if missing:
        raise SystemExit(f"FAIL - critical file(s) missing from the workspace: {missing}")

    selected = {path.relative_to(REPO).as_posix() for path in files}
    for fixture, expected in REQUIRED_FIXTURES.items():
        source = REPO / fixture
        if not source.exists():
            raise SystemExit(
                f"FAIL - required fixture missing from the workspace: {fixture}")
        if sha256(source) != expected:
            raise SystemExit(
                f"FAIL - required fixture {fixture} does not match its recorded "
                f"SHA-256 (expected {expected}, got {sha256(source)})")
        if fixture not in selected:
            raise SystemExit(
                f"FAIL - required fixture {fixture} exists but no INCLUDE_GLOBS "
                "pattern selects it, so the archive would lose it silently")

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(REPO).as_posix())
    return files


# ---------------------------------------------------------------------------
# verification - reopen, extract, inspect, hash
# ---------------------------------------------------------------------------

def verify(zip_path: pathlib.Path) -> Tuple[bool, Dict[str, object]]:
    report: Dict[str, object] = {"zip": zip_path.name, "checks": []}
    ok = True

    def check(name: str, passed: bool, detail: str = ""):
        nonlocal ok
        ok = ok and passed
        report["checks"].append({"check": name,
                                 "status": "PASS" if passed else "FAIL",
                                 "detail": detail})

    if not zip_path.exists():
        return False, {"zip": zip_path.name,
                       "checks": [{"check": "zip exists", "status": "FAIL",
                                   "detail": str(zip_path)}]}

    report["zip_sha256"] = sha256(zip_path)
    report["zip_bytes"] = zip_path.stat().st_size

    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        check("archive integrity (CRC)", bad is None, bad or "all entries OK")

        names = archive.namelist()
        report["entry_count"] = len(names)

        missing = [name for name in CRITICAL_FILES if name not in names]
        check("all critical files present", not missing, f"missing={missing}")

        leaked = [n for n in names if any(part in f"/{n}" for part in FORBIDDEN_PARTS)]
        check("no build intermediates / caches / journals", not leaked,
              f"leaked={leaked[:5]}")

        # --- fixtures the regression suite READS must be in the archive ---
        fixture_report = {}
        fixture_failures = []
        for fixture, expected in REQUIRED_FIXTURES.items():
            if fixture not in names:
                fixture_failures.append(f"{fixture}: ABSENT")
                fixture_report[fixture] = {"present": False}
                continue
            actual = sha256_bytes(archive.read(fixture))
            fixture_report[fixture] = {"present": True, "sha256": actual,
                                       "matches_expected": actual == expected}
            if actual != expected:
                fixture_failures.append(f"{fixture}: {actual} != {expected}")
        report["required_fixtures"] = fixture_report
        check("required real-bill fixtures present and hash-verified",
              not fixture_failures, f"failures={fixture_failures}")

        # --- extract to a scratch dir and compare BYTES -------------------
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cghs_zip_verify_"))
        try:
            archive.extractall(tmp)
            hashes = {}
            mismatched = []
            for name in CRITICAL_FILES:
                if name not in names:
                    continue
                extracted = tmp / name
                source = REPO / name
                extracted_hash = sha256(extracted)
                source_hash = sha256(source)
                hashes[name] = {"sha256": extracted_hash,
                                "bytes": extracted.stat().st_size,
                                "matches_workspace": extracted_hash == source_hash}
                if extracted_hash != source_hash:
                    mismatched.append(name)
            report["critical_file_hashes"] = hashes
            check("extracted bytes match the workspace", not mismatched,
                  f"mismatched={mismatched}")

            # --- the archived app.py must be the HARDENED one -------------
            app_source = (tmp / "app.py").read_text(encoding="utf-8", errors="replace")
            check("archived app.py has no ASSUMED-COMMITTED literal",
                  "ASSUMED-COMMITTED" not in app_source.upper(),
                  "baseline success-on-unknown marker")
            check("archived app.py delegates to the cghs package",
                  "from cghs.orchestrator import" in app_source,
                  "canonical import present")

            tree = ast.parse(app_source, "app.py")
            classes = {node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef)}
            duplicated = classes & {"TreatmentPlanOrchestrator", "SmartDOMResolver",
                                    "PortalSynchronizer", "CGHSParsingEngine",
                                    "RowCodeQtyVerifier", "EnterpriseLogger"}
            check("archived app.py holds no duplicate engine", not duplicated,
                  f"duplicated={sorted(duplicated)}")

            lines = app_source.count("\n")
            check("archived app.py is the slim UI file", lines < 1800,
                  f"{lines} lines (baseline was 3247)")

            # --- the package must import from the EXTRACTED copy ----------
            import subprocess
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, '.');"
                 "import cghs, cghs.orchestrator, cghs.rules, cghs.parsing;"
                 "print(cghs.__version__)"],
                cwd=tmp, capture_output=True, text=True, timeout=120)
            check("extracted cghs package imports cleanly",
                  probe.returncode == 0,
                  (probe.stdout or probe.stderr).strip()[:200])
            report["extracted_package_version"] = probe.stdout.strip()

            # --- the extracted test suite must still collect --------------
            collect = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q"],
                cwd=tmp, capture_output=True, text=True, timeout=300)
            tail = (collect.stdout or collect.stderr).strip().splitlines()
            check("extracted test suite collects", collect.returncode == 0,
                  tail[-1] if tail else "")
            report["extracted_test_collection"] = tail[-1] if tail else ""
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    report["result"] = "PASS" if ok else "FAIL"
    return ok, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", default=str(REPO / ZIP_NAME))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--report", default="", help="write the JSON report here")
    args = parser.parse_args()

    zip_path = pathlib.Path(args.zip)
    if not args.verify_only:
        files = build(zip_path)
        print(f"built {zip_path.name} from {len(files)} files "
              f"({zip_path.stat().st_size} bytes)")

    ok, report = verify(zip_path)
    print(json.dumps(report, indent=2))
    if args.report:
        pathlib.Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nPACKAGING: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
