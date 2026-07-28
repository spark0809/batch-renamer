@echo off
REM BatchRenamer - Windows one-click build script
REM Requires Python 3.9+ installed and added to PATH
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo [2/3] Building EXE...
python -m PyInstaller --noconfirm --onefile --windowed --name BatchRenamer renamer_app.py
if errorlevel 1 goto fail

echo [3/3] Done!
echo.
echo EXE is at: %~dp0dist\BatchRenamer.exe
echo Copy it anywhere and double-click to run. No installation needed.
pause
exit /b 0

:fail
echo.
echo Build failed. Make sure Python is installed and added to PATH.
pause
exit /b 1
