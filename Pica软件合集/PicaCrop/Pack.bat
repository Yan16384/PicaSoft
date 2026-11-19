@echo off
cd /d "%~dp0..\.."
python .github\tools\release\build.py picacrop --install
exit /b %ERRORLEVEL%
