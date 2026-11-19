@echo off
cd /d "%~dp0..\..\.."
python .github\tools\release\build.py picamarkdown --install
exit /b %ERRORLEVEL%
