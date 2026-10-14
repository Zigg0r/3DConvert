@echo off
rem Builds 3DConvert.exe. Needs Windows, Python 3.12 (64-bit, with the py launcher) and internet for pip on the first run.
rem Result: <repo>\dist\3DConvert.exe, built in the virtual environment <repo>\.venv (created when missing).
rem When the repo folder is called FILES (layout <project>\FILES), the exe and .venv go into <project> instead.
setlocal
set "BUILD=%~dp0"
for %%I in ("%BUILD%..") do set "REPO=%%~fI" & set "REPONAME=%%~nxI"
set "ROOT=%REPO%"
set "OUT=%REPO%\dist"
if /i "%REPONAME%"=="FILES" for %%I in ("%REPO%\..") do set "ROOT=%%~fI" & set "OUT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" py -3.12 -m venv "%ROOT%\.venv"
"%PY%" -m pip install --quiet -r "%BUILD%requirements.txt" pyinstaller || goto :failed
"%PY%" "%BUILD%make_icon.py" || goto :failed
"%PY%" "%BUILD%make_splash.py" || goto :failed
"%PY%" -m PyInstaller --noconfirm --distpath "%OUT%" --workpath "%LOCALAPPDATA%\3DConvert\build" "%BUILD%3DConvert.spec" || goto :failed

echo.
echo Built: %OUT%\3DConvert.exe
goto :done

:failed
echo.
echo Build failed.
set "FAILED=1"

:done
if /i not "%~1"=="nopause" pause
if defined FAILED exit /b 1
