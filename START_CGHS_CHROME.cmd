@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ===================================================================
REM  CGHS Automation Console - dedicated Chrome launcher
REM
REM  WHY THIS FILE EXISTS
REM  Chrome 136 and later REFUSE to expose the remote debugging port
REM  when Chrome is running on its DEFAULT user data directory.  The
REM  old instructions told the operator to launch chrome.exe with only
REM  a debugging port flag, so on current Chrome the port silently
REM  never opens and the bot waits on an endpoint that never answers.
REM
REM  This launcher starts Chrome on a DEDICATED, PERSISTENT automation
REM  profile, which is what makes the debugging port work again, and
REM  records the chosen port in a shared configuration file that the
REM  Python application reads.
REM
REM  It does NOT touch the operator's normal Chrome profile, and it does
REM  NOT automate any login, password, OTP, CAPTCHA or MFA.  The
REM  operator signs in to the portal manually.
REM
REM  EXIT CODES
REM     0  Chrome DevTools verified and ready
REM     1  endpoint did not pass verification (CONNECTION FAILED)
REM     2  Chrome could not be found
REM     3  invalid port argument
REM     4  the port is held by something that is not Chrome
REM ===================================================================

set "CONFIG_DIR=%LOCALAPPDATA%\CGHS-Enhancement-Bot"
set "CONFIG_FILE=%CONFIG_DIR%\chrome_config.json"
set "PROFILE_DIR=%CONFIG_DIR%\ChromeProfile"
set "DEBUG_HOST=127.0.0.1"

REM ---- 1. resolve the port -------------------------------------------
REM  Order: command-line argument > existing shared config > default.
REM  The shared file is the authority the Python application reads, so a
REM  port given here is written to it BEFORE anything else happens.

set "DEBUG_PORT=9222"

if exist "%CONFIG_FILE%" (
    for /f "usebackq delims=" %%P in (`
        powershell -NoProfile -Command ^
          "try { $c = Get-Content -Raw '%CONFIG_FILE%' | ConvertFrom-Json; if ($c.port) { $c.port } } catch { }"
    `) do set "DEBUG_PORT=%%P"
)

if not "%~1"=="" set "DEBUG_PORT=%~1"

REM ---- 2. validate the port ------------------------------------------
echo %DEBUG_PORT%| findstr /r "^[1-9][0-9]*$" >nul
if errorlevel 1 (
    echo.
    echo  [ERROR] Port "%DEBUG_PORT%" is not a whole number.
    echo          Give a TCP port between 1 and 65535.
    echo.
    pause
    exit /b 3
)
if %DEBUG_PORT% GTR 65535 (
    echo.
    echo  [ERROR] Port %DEBUG_PORT% is above the maximum TCP port 65535.
    echo.
    pause
    exit /b 3
)

echo.
echo  ================================================================
echo   CGHS Automation Console - Chrome launcher
echo  ================================================================
echo   Debug port : %DEBUG_PORT%
echo   Profile    : %PROFILE_DIR%
echo   Config     : %CONFIG_FILE%
echo.

REM ---- 3. PERSIST the port BEFORE any connection check ----------------
REM  This must happen before the already-running check, otherwise that
REM  branch can exit while the application is still pointed at the old
REM  port.  setx is deliberately NOT relied upon: it does not update a
REM  Command Prompt that is already open, so the file is the real source.

if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%" 2>nul

powershell -NoProfile -Command ^
  "$cfg = [ordered]@{ host = '%DEBUG_HOST%'; port = [int]'%DEBUG_PORT%' };" ^
  "$cfg | ConvertTo-Json | Set-Content -Encoding UTF8 '%CONFIG_FILE%'" >nul 2>&1

if exist "%CONFIG_FILE%" (
    echo  [OK]    Shared configuration written - the application will use
    echo          %DEBUG_HOST%:%DEBUG_PORT%
) else (
    echo  [WARN]  Could not write %CONFIG_FILE%
    echo          The application may fall back to its default port.
)
echo.

REM ---- 4. is a usable Chrome ALREADY on this port? --------------------
call :PORT_IN_USE
if "!PORT_BUSY!"=="1" (
    echo  [INFO]  Something is ALREADY listening on %DEBUG_HOST%:%DEBUG_PORT%.
    echo  [..]    Verifying whether it is really Chrome...
    echo.

    call :VERIFY_DEVTOOLS
    if !VERIFY_RESULT!==0 (
        call :SHOW_READY
        echo   Chrome was already running on this port - no new window was
        echo   opened. Sign in to the portal there and start the automation.
        echo.
        pause
        exit /b 0
    )

    if !VERIFY_RESULT!==2 (
        echo  ================================================================
        echo   PORT CONFLICT - NOT CHROME
        echo  ================================================================
        echo.
        echo   Port %DEBUG_PORT% is held by a program that is NOT Chrome, so
        echo   the automation cannot attach to it.
        echo.
        echo   Fix it in one of two ways:
        echo     1. Close whatever is using port %DEBUG_PORT%, then run this
        echo        launcher again.
        echo     2. Use a different port, for example:
        echo            START_CGHS_CHROME.cmd 9333
        echo        The launcher records the port in the shared configuration
        echo        file, so the application follows automatically.
        echo.
        pause
        exit /b 4
    )

    echo  ================================================================
    echo   CONNECTION FAILED
    echo  ================================================================
    echo.
    echo   Something answered on port %DEBUG_PORT% but did not pass DevTools
    echo   verification. Chrome is NOT ready.
    echo.
    echo   If Chrome is starting up, wait a moment and run this again.
    echo.
    pause
    exit /b 1
)

REM ---- 5. locate Chrome ------------------------------------------------
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
    pause
    exit /b 2
)

echo  [OK]    Chrome found:
echo          %CHROME_EXE%

REM ---- 6. launch on the dedicated profile ------------------------------
if not exist "%PROFILE_DIR%" (
    mkdir "%PROFILE_DIR%" 2>nul
    echo  [OK]    Created the dedicated automation profile directory.
) else (
    echo  [OK]    Reusing the existing automation profile.
)

echo  [..]    Starting Chrome on the automation profile...

start "" "%CHROME_EXE%" ^
    --remote-debugging-port=%DEBUG_PORT% ^
    --remote-debugging-address=%DEBUG_HOST% ^
    --user-data-dir="%PROFILE_DIR%" ^
    --no-first-run ^
    --no-default-browser-check

REM ---- 7. verify the NEWLY launched Chrome, same routine ---------------
echo  [..]    Validating the DevTools endpoint...

set "LAUNCH_OK="
for /l %%I in (1,1,25) do (
    if not defined LAUNCH_OK (
        call :VERIFY_DEVTOOLS
        if !VERIFY_RESULT!==0 (
            set "LAUNCH_OK=1"
        ) else (
            powershell -NoProfile -Command "Start-Sleep -Milliseconds 400" >nul 2>&1
        )
    )
)

echo.
if not defined LAUNCH_OK (
    echo  ================================================================
    echo   CONNECTION FAILED
    echo  ================================================================
    echo.
    echo   Port %DEBUG_PORT% did not pass DevTools verification, so Chrome
    echo   is NOT ready and the automation will not be able to attach.
    echo.
    echo   Checked: TCP reachable, HTTP 200, valid JSON, Chrome identity,
    echo            and a DevTools WebSocket URL.
    echo.
    echo   Most likely causes:
    echo     - Chrome was already running on your NORMAL profile. Chrome 136+
    echo       will not open a debugging port in that case. Close ALL Chrome
    echo       windows and run this launcher again.
    echo     - Security software is blocking the port.
    echo.
    pause
    exit /b 1
)

call :SHOW_READY

echo   NEXT STEPS
echo     1. Sign in to the CGHS / NHA portal in the window that opened.
echo     2. Open the patient's Treatment Plan page.
echo     3. Start the CGHS Automation Console and run the batch.
echo.
echo   This profile is remembered, so you will not have to sign in
echo   every time. Your normal Chrome profile is untouched.
echo.
pause
exit /b 0


REM ===================================================================
REM  SUBROUTINES
REM ===================================================================

:PORT_IN_USE
REM  Sets PORT_BUSY=1 when any process holds the port.
set "PORT_BUSY=0"
powershell -NoProfile -Command ^
  "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('%DEBUG_HOST%', %DEBUG_PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL%==0 set "PORT_BUSY=1"
goto :eof


:VERIFY_DEVTOOLS
REM  THE SINGLE DevTools verification used by BOTH the already-running and
REM  the newly-launched paths.  There is deliberately only one
REM  implementation, so the two can never disagree.
REM
REM  Checks, in order: HTTP 200, valid JSON, a Chrome/Chromium Browser
REM  identity, and a usable ws:// DevTools URL.
REM
REM  VERIFY_RESULT  0 = verified Chrome
REM                 1 = no usable answer (not running / malformed / error)
REM                 2 = an HTTP service answered, but it is NOT Chrome
powershell -NoProfile -Command ^
  "$ErrorActionPreference='Stop';" ^
  "try {" ^
  "  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://%DEBUG_HOST%:%DEBUG_PORT%/json/version';" ^
  "  if ($r.StatusCode -ne 200) { exit 2 };" ^
  "  $j = $r.Content | ConvertFrom-Json;" ^
  "  if (-not $j.Browser) { exit 2 };" ^
  "  if ($j.Browser -notmatch '^(?i)(chrome|chromium|headlesschrome|edg)') { exit 2 };" ^
  "  if (-not $j.webSocketDebuggerUrl) { exit 2 };" ^
  "  if ($j.webSocketDebuggerUrl -notmatch '^wss?://') { exit 2 };" ^
  "  exit 0" ^
  "} catch {" ^
  "  try {" ^
  "    $c = New-Object Net.Sockets.TcpClient; $c.Connect('%DEBUG_HOST%', %DEBUG_PORT%); $c.Close(); exit 2" ^
  "  } catch { exit 1 }" ^
  "}" >nul 2>&1
set "VERIFY_RESULT=%ERRORLEVEL%"
goto :eof


:SHOW_READY
echo  ================================================================
echo   CHROME DEVTOOLS READY
echo  ================================================================
echo.
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod -TimeoutSec 3 -Uri 'http://%DEBUG_HOST%:%DEBUG_PORT%/json/version'; Write-Host ('   Browser  : ' + $r.Browser); Write-Host ('   Protocol : ' + $r.'Protocol-Version') } catch { }"
echo    Endpoint : %DEBUG_HOST%:%DEBUG_PORT%
echo    Config   : %CONFIG_FILE%
echo.
goto :eof
