@echo off
REM BatchRenamer - Windows 一键打包脚本
REM 需要本机已安装 Python 3.9+ 且 pip 可用
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] 安装依赖 (PyInstaller + tkinterdnd2)...
python -m pip install --upgrade pyinstaller tkinterdnd2
if errorlevel 1 goto fail

echo [2/4] 开始打包 (单文件 / 无控制台窗口)...
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name BatchRenamer ^
  --hidden-import tkinterdnd2 ^
  --collect-data tkinterdnd2 ^
  renamer_app.py
if errorlevel 1 goto fail

echo [3/4] 清理临时构建文件...
rmdir /s /q build 2>nul
del /q BatchRenamer.spec 2>nul

echo [4/4] 完成!
echo.
echo 生成的 EXE 位于: %~dp0dist\BatchRenamer.exe
echo 把它复制到任意 Windows 电脑双击即可运行，无需安装 Python。
pause
exit /b 0

:fail
echo.
echo 打包失败。请确认已安装 Python 3.9+ 并已加入 PATH。
pause
exit /b 1
