@echo off
rem ============================================================
rem  构建独立 exe：需要已安装 Python 3.11+
rem  产物: dist\云档.exe     （图形启动器，双击使用）
rem        dist\云档-cli.exe （命令行版，供脚本/计划任务）
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

py -m pip install -U psutil customtkinter pillow pyinstaller || goto :err
py -m PyInstaller -F -w -n 云档 --collect-all customtkinter --clean --noconfirm 云档GUI.py || goto :err
py -m PyInstaller -F -n 云档-cli --clean --noconfirm 云档.py || goto :err

echo.
echo 构建完成: dist\云档.exe  +  dist\云档-cli.exe
pause
exit /b 0

:err
echo.
echo 构建失败，请检查上方报错。
pause
exit /b 1
