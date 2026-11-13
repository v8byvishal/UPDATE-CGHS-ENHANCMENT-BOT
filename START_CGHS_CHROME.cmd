@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ===================================================================
REM  CGHS Automation Console - dedicated Chrome launcher
REM
REM  WHY THIS FILE EXISTS
REM  Chrome 136 and later REFUSE to expose the remote debugging port
REM  when Chrome is running on its DEFAULT user data directory.  The
REM  previous instructions told the operator to run
REM      chrome.exe --remote-debugging-port=9222
REM  with no profile flag, so on current Chrome the port silently never
REM  opens and the bot waits on an endpoint that will never answer.
REM
REM  This launcher starts Chrome on a DEDICATED, PERSISTENT automation
REM  profile, which is what makes the debugging port work again.
REM
REM  It does NOT touch the operator's normal Chrome profile, and it does
REM  NOT automate any login, password, OTP, CAPTCHA or MFA.  The operator
REM  signs in to the portal manually in the window this opens.
REM ===================================================================

REM ---- PORT CONFIGURATION (shared with the Python application) -------
REM  Resolution order, IDENTICAL to cghs/session.py:
REM     1. the first command-line argument (this launcher only)
REM     2. the CGHS_CHROME_PORT environment variable
REM     3. the default, 9222
REM  If you pass a port as an argument, this script also exports
REM  CGHS_CHROME_PORT for you, so the application attaches to the same port.

set "DEBUG_PORT=9222"
if not "%CGHS_CHROME_PORT%"=="" set "DEBUG_PORT=%CGHS_CHROME_PORT%"
set "PORT_FROM_ARG="
if not "%~1"=="" (
    set "DEBUG_PORT=%~1"
    set "PORT_FROM_ARG=1"
)

REM ---- validate the port is a number in 1..65535 ----------------------
echo %DEBUG_PORT%| findstr /r "^[1-9][0-9]*$" >nul
if errorlevel 1 (
    echo  [ERROR] Port "%DEBUG_PORT%" is not a whole number.
    echo          Give a TCP port between 1 and 65535.
    pause
    exit /b 3
)
if %DEBUG_PORT% GTR 65535 (
    echo  [ERROR] Port %DEBUG_PORT% is above the maximum TCP port 65535.
    pause
    exit /b 3
)

set "PROFILE_DIR=%LOCALAPPDATA%\CGHS-Enhancement-Bot\ChromeProfile"

echo.
echo  ================================================================
echo   CGHS Automation Console - Chrome launcher
echo  ================================================================
echo   Debug port : %DEBUG_PORT%
echo   Profile    : %PROFILE_DIR%
echo.

REM ---- 1. locate Chrome ---------------------------------------------
set "CHROME_EXE="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
) do (
    if exist "%%~P" if not defined CHROME_EXE set "CHROME_EXE=%%~P"
)

if not defined CHROME_EXE (
    for /f "usebackq tokens=2,*" %%A in (
        `reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul`
    ) do set "CHROME_EXE=%%B"
)

if not defined CHROME_EXE (
    echo  [ERROR] Google Chrome was not found in any standard location.
    echo.
    echo   Looked in:
    echo     %ProgramFiles%\Google\Chrome\Application\
    echo     %ProgramFiles(x86)%\Google\Chrome\Application\
    echo     %LOCALAPPDATA%\Google\Chrome\Application\
    echo     and the Windows App Paths registry key.
    echo.
    echo   Install Chrome, or edit this file and set CHROME_EXE manually.
    echo.
    pause
    exit /b 2
)

echo  [OK]    Chrome found:
echo          %CHROME_EXE%

REM ---- 2. is the automation profile already running? -----------------
REM  If the debug port already answers, a usable Chrome is up.  Re-launching
REM  would either steal the port or open a second window for no reason.
powershell -NoProfile -Command ^
  "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', %DEBUG_PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1

if %ERRORLEVEL%==0 (
    echo.
    echo  [INFO]  Something is ALREADY listening on 127.0.0.1:%DEBUG_PORT%.
    echo.
    powershell -NoProfile -Command ^
      "try { $r = Invoke-RestMethod -TimeoutSec 3 -Uri 'http://127.0.0.1:%DEBUG_PORT%/json/version'; Write-Host ('         It identifies itself as: ' + $r.Browser) } catch { Write-Host '         It did NOT answer the DevTools /json/version request.' }"
    echo.
    echo          If that is Chrome, you are ready - just use that window
    echo          and start the automation.
    echo.
    echo          If it is NOT Chrome, another program is holding port
    echo          %DEBUG_PORT%.  Close it, or start this launcher with a
    echo          different port, for example:
    echo              START_CGHS_CHROME.cmd 9333
    echo          and set the same port in the application.
    echo.
    pause
    exit /b 0
)

REM ---- 3. launch on the dedicated profile ----------------------------
if not exist "%PROFILE_DIR%" (
    mkdir "%PROFILE_DIR%" 2>nul
    echo  [OK]    Created the dedicated automation profile directory.
) else (
    echo  [OK]    Reusing the existing automation profile.
)

echo  [..]    Starting Chrome on the automation profile...

start "" "%CHROME_EXE%" ^
    --remote-debugging-port=%DEBUG_PORT% ^
    --remote-debugging-address=127.0.0.1 ^
    --user-data-dir="%PROFILE_DIR%" ^
    --no-first-run ^
    --no-default-browser-check

REM ---- 4. VALIDATE the endpoint - not just a TCP connect --------------
REM  A successful TCP connect proves only that SOMETHING holds the port.
REM  Chrome is only READY when /json/version returns HTTP 200, valid JSON,
REM  a Chrome/Chromium browser identity AND DevTools WebSocket metadata.

echo  [..]    Validating the DevTools endpoint...

set "DEVTOOLS_OK="
for /l %%I in (1,1,25) do (
    if not defined DEVTOOLS_OK (
        powershell -NoProfile -Command ^
          "$ErrorActionPreference='Stop';" ^
          "try {" ^
          "  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://127.0.0.1:%DEBUG_PORT%/json/version';" ^
          "  if ($r.StatusCode -ne 200) { exit 1 };" ^
          "  $j = $r.Content | ConvertFrom-Json;" ^
          "  if (-not $j.Browser) { exit 1 };" ^
          "  if ($j.Browser -notmatch '^(?i)(chrome|chromium|headlesschrome|edg)') { exit 1 };" ^
          "  if (-not $j.webSocketDebuggerUrl) { exit 1 };" ^
          "  if ($j.webSocketDebuggerUrl -notmatch '^wss?://') { exit 1 };" ^
          "  exit 0" ^
          "} catch { exit 1 }" >nul 2>&1
        if !ERRORLEVEL!==0 (
            set "DEVTOOLS_OK=1"
        ) else (
            powershell -NoProfile -Command "Start-Sleep -Milliseconds 400" >nul 2>&1
        )
    )
)

echo.
if not defined DEVTOOLS_OK (
    echo  ================================================================
    echo   CONNECTION FAILED
    echo  ================================================================
    echo.
    echo   Port %DEBUG_PORT% did not pass DevTools validation. Chrome is NOT
    echo   ready, and the automation will not be able to attach.
    echo.
    echo   Checked: TCP reachable, HTTP 200, valid JSON, Chrome identity,
    echo            and a DevTools WebSocket URL.
    echo.
    echo   Most likely causes:
    echo     - Chrome was already running on your NORMAL profile. Chrome 136+
    echo       will not open a debugging port in that case. Close ALL Chrome
    echo       windows and run this launcher again.
    echo     - Another program is occupying port %DEBUG_PORT%.
    echo     - Security software is blocking the port.
    echo.
    echo   To use a different port, run:   START_CGHS_CHROME.cmd 9333
    echo   and make the application match: setx CGHS_CHROME_PORT 9333
    echo.
    pause
    exit /b 1
)

echo  ================================================================
echo   CHROME DEVTOOLS READY
echo  ================================================================
echo.
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -TimeoutSec 3 -Uri 'http://127.0.0.1:%DEBUG_PORT%/json/version'; Write-Host ('   Browser  : ' + $r.Browser); Write-Host ('   Protocol : ' + $r.'Protocol-Version') } catch { }"
echo    Endpoint : 127.0.0.1:%DEBUG_PORT%
echo.

REM ---- keep the application in step with a custom port -----------------
if defined PORT_FROM_ARG (
    if not "%DEBUG_PORT%"=="9222" (
        setx CGHS_CHROME_PORT %DEBUG_PORT% >nul 2>&1
        echo   [SYNC] CGHS_CHROME_PORT has been set to %DEBUG_PORT% so the
        echo          application attaches to this same port. Restart the
        echo          application if it is already open.
        echo.
    )
)

echo   NEXT STEPS
echo     1. Sign in to the CGHS / NHA portal in the window that opened.
echo     2. Open the patient's Treatment Plan page.
echo     3. Start the CGHS Automation Console and run the batch.
echo.
echo   This profile is remembered, so you will not have to sign in
echo   every time. Your normal Chrome profile is untouched.
echo.
pause
endlocal
exit /b 0
