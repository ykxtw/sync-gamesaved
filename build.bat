@echo off
rem ============================================================
rem  构建独立 exe：需要已安装 Python 3.11+
rem  产物: dist\云档.exe     （图形启动器，双击使用）
rem        dist\云档-cli.exe （命令行版，供脚本/计划任务）
rem  注意：本文件必须保存为 ANSI/GBK 编码。若被编辑器转存为 UTF-8，
rem        中文 Windows 的 cmd 会乱码并报"不是内部或外部命令"；
rem        也不要在里面加 chcp 65001（会让 cmd 行定位错乱）。
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist "云档GUI.py" goto :nosrc
if not exist "云档.py" goto :nosrc

rem ---- 定位 Python：优先 py 启动器，找不到再试 python ----
set "PY=py"
%PY% --version >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul || goto :nopy
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || goto :badver

echo [1/3] 安装依赖（首次较慢；直连失败会自动换清华镜像重试）...
%PY% -m pip install -U psutil customtkinter pillow pyinstaller || %PY% -m pip install -U psutil customtkinter pillow pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple || goto :err

echo [2/3] 打包图形版 dist\云档.exe ...
%PY% -m PyInstaller -F -w -n 云档 --collect-all customtkinter --clean --noconfirm 云档GUI.py || goto :err

echo [3/3] 打包命令行版 dist\云档-cli.exe ...
%PY% -m PyInstaller -F -n 云档-cli --clean --noconfirm 云档.py || goto :err

echo.
echo 构建完成: dist\云档.exe  +  dist\云档-cli.exe
echo 用新的 exe 覆盖旧版即可；云档.toml 配置/统计/封面完全兼容。
pause
exit /b 0

:nosrc
echo.
echo [错误] 当前目录找不到 云档GUI.py / 云档.py。
echo        若解压源码 zip 后中文文件名变成了乱码，请用 Windows 自带
echo        解压功能或 7-Zip 重新解压，再运行本脚本。
pause
exit /b 1

:nopy
echo.
echo [错误] 未找到 Python。请安装 Python 3.11+，
echo        安装时务必勾选 "Add python.exe to PATH"。
pause
exit /b 1

:badver
echo.
echo [错误] Python 版本过低，云档需要 3.11 或更新版本。
pause
exit /b 1

:err
echo.
echo 构建失败，请检查上方报错。
pause
exit /b 1
