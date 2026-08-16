@echo off
rem Launch the pzctl daemon and open the control panel in your browser.
cd /d "%~dp0"

set PY=py -3
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 set PY=python

%PY% -m pzctl --open %*

echo.
echo pzctl exited.
pause
