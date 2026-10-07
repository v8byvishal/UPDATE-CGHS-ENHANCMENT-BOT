@echo off
REM ===================================================================
REM  CGHS Enhancement Bot - Windows build
REM
REM  Produces dist\CGHS_Enhancement_Bot.exe from CGHS_Enhancement_Bot.spec.
REM
REM  This script did not exist in the repository before; it is written to
REM  match the spec file that is already here. It has NOT been executed on
REM  Windows from the development sandbox (Linux), so the Windows runtime
REM  status is reported as NOT_YET_VERIFIED until an operator runs it.
REM
REM  Usage:
REM      BUILD_WINDOWS.cmd              build
REM      BUILD_WINDOWS.cmd /clean       remove build artefacts first
REM      BUILD_WINDOWS.cmd /test        run the test suite, then build
REM ===================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "EXIT_CODE=0"
set "RUN_TESTS=0"
set "DO_CLEAN=0"

:parse_args
if "%~1"=="" goto after_args
if /i "%~1"=="/clean" set "DO_CLEAN=1"
if /i "%~1"=="/test"  set "RUN_TESTS=1"
shift
goto parse_args
:after_args

echo.
echo ============================================================
echo  CGHS Enhancement Bot - Windows build
echo ============================================================
echo.

REM ---- 1. Python ----------------------------------------------------
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        echo [FAIL] Python was not found on PATH.
        echo        Install Python 3.9+ and re-run this script.
        exit /b 1
    )
    set "PY=python"
)

for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do echo [OK]   %%v

REM ---- 2. Dependencies ----------------------------------------------
echo [..]   Checking build dependencies
%PY% -m pip --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] pip is not available for this interpreter.
    exit /b 1
)

REM requirements.txt is the ONE authoritative manifest - do not duplicate the
REM dependency list here.  The previous "--upgrade pyinstaller selenium PyQt5
REM PyMuPDF" installed whatever was newest on build day, so the EXE was never
REM built against the versions the test suite had passed with.
if not exist "%~dp0requirements.txt" (
    echo [FAIL] requirements.txt is missing - cannot install a declared,
    echo        reproducible dependency set. Aborting.
    exit /b 1
)

%PY% -m pip install -r "%~dp0requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] Dependency installation failed.
    exit /b 1
)

REM Fail clearly if anything the build needs is still not importable.
%PY% -c "import PyQt5, selenium, fitz, PyInstaller" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] A declared dependency is not importable after installation.
    echo        Check requirements.txt against this interpreter.
    exit /b 1
)
echo [OK]   Dependencies present (from requirements.txt)

REM ---- 3. Sanity: the automation package must import ------------------
echo [..]   Import check: cghs package
%PY% -c "import cghs, cghs.orchestrator, cghs.session, cghs.controllers, cghs.rules, cghs.parsing; print('cghs', cghs.__version__)"
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] The cghs package does not import - aborting before build.
    exit /b 1
)
echo [OK]   cghs package imports

REM ---- 4. Optional test run -------------------------------------------
if "%RUN_TESTS%"=="1" (
    echo [..]   Running test suite
    %PY% -m pytest -q
    if !ERRORLEVEL! NEQ 0 (
        echo [FAIL] Tests failed - refusing to build a broken EXE.
        exit /b 1
    )
    echo [OK]   Tests passed
)

REM ---- 5. Clean --------------------------------------------------------
if "%DO_CLEAN%"=="1" (
    echo [..]   Cleaning previous build output
    if exist "build" rmdir /s /q "build"
    if exist "dist"  rmdir /s /q "dist"
    echo [OK]   Clean
)

REM ---- 6. Build --------------------------------------------------------
if not exist "CGHS_Enhancement_Bot.spec" (
    echo [FAIL] CGHS_Enhancement_Bot.spec is missing.
    exit /b 1
)

echo [..]   Running PyInstaller
%PY% -m PyInstaller --noconfirm CGHS_Enhancement_Bot.spec
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] PyInstaller build failed.
    exit /b 1
)

REM ---- 7. Verify the artefact -----------------------------------------
if not exist "dist\CGHS_Enhancement_Bot.exe" (
    echo [FAIL] Build reported success but dist\CGHS_Enhancement_Bot.exe is absent.
    exit /b 1
)

for %%A in ("dist\CGHS_Enhancement_Bot.exe") do set "EXE_SIZE=%%~zA"
echo.
echo ============================================================
echo  BUILD OK
echo    dist\CGHS_Enhancement_Bot.exe   (!EXE_SIZE! bytes)
echo ============================================================
echo.
echo  Reminder - the bot ATTACHES to a Chrome you have already
echo  authenticated yourself. It performs no login, MFA, cookie or
echo  profile automation. Start Chrome with:
echo.
echo     chrome.exe --remote-debugging-port=9222
echo.
echo  then log in manually and open the patient Treatment Plan.
echo.

endlocal
exit /b 0
