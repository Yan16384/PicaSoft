@echo off
cd /d "%~dp0..\.."
python .github\tools\release\build.py picaphoto --install
exit /b %ERRORLEVEL%
