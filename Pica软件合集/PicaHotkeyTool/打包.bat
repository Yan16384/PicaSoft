@echo off
cd /d "%~dp0..\.."
python .github\tools\release\build.py picahotkeytool --install
exit /b %ERRORLEVEL%
