@echo off
rem Run outside the Claude MSIX package (via WMI) so Python lands in the real user profile.
winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements > "%~dp0install-python.log" 2>&1
echo EXIT %ERRORLEVEL% >> "%~dp0install-python.log"
