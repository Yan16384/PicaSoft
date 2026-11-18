@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  set "PICA_PYTHON=python"
) else (
  set "PICA_PYTHON=py -3"
)
%PICA_PYTHON% -m pip install -r requirements.txt pyinstaller==6.22.3
if errorlevel 1 exit /b 1
%PICA_PYTHON% -m PyInstaller --onefile --windowed --name PicaMarkdownExporter paper_export_app.py
exit /b %ERRORLEVEL%
