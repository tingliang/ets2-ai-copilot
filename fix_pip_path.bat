@echo off
rem ============================================================
rem  fix_pip_path.bat  - 永久修复 pip 命令不可用的问题
rem
rem  作用：把 Python 的 Scripts 目录（pip.exe 所在处）永久加入
rem        用户 PATH，重启后依然生效。双击运行一次即可。
rem
rem  原理：pip 不能用，是因为 Scripts 目录不在 PATH 里。
rem        本脚本自动定位 Python 的 Scripts 目录，永久写入
rem        用户环境变量 PATH（用 setx，重启不丢）。
rem ============================================================

setlocal enabledelayedexpansion

echo ==== 修复 pip 命令 ====
echo.

rem 1. 定位 Python 所在目录
set "PY_DIR="
where python >nul 2>nul
if !errorlevel!==0 (
    for /f "delims=" %%i in ('where python') do (
        set "PY_EXE=%%i"
        goto :found
    )
)

:found
if not defined PY_EXE (
    echo [!] 未找到 python，请先安装 Python 并勾选 "Add to PATH"。
    echo     或手动运行：python -m pip install xxx
    pause
    exit /b 1
)

rem 2. 从 python.exe 的完整路径推导 Scripts 目录
for %%F in ("!PY_EXE!") do set "PY_DIR=%%~dpF"
set "SCRIPTS_DIR=!PY_DIR!Scripts"

echo Python 可执行文件: !PY_EXE!
echo Scripts 目录:     !SCRIPTS_DIR!
echo.

rem 3. 校验 Scripts 目录是否存在、里面有没有 pip.exe
if not exist "!SCRIPTS_DIR!\pip.exe" (
    echo [!] 未在 !SCRIPTS_DIR! 找到 pip.exe
    echo     请检查 Python 安装是否完整，或手动确认 Scripts 目录。
    pause
    exit /b 1
)

rem 4. 检查当前 PATH（用户级 + 系统级）是否已包含该目录
set "ALREADY="
for %%P in ("!SCRIPTS_DIR!") do (
    echo %PATH% | find /i "%%~P" >nul 2>nul && set "ALREADY=1"
)
if defined ALREADY (
    echo [OK] Scripts 目录已经在 PATH 里，无需重复添加。
    echo.
    echo 如果 pip 仍不可用，请关闭并重新打开命令行窗口
    echo （旧窗口不会自动继承新 PATH），或重启电脑后再试。
    pause
    exit /b 0
)

rem 5. 永久写入用户 PATH（保留原有内容，不覆盖）
rem    先用 PowerShell 读出并去重，再 setx 写回（避免 setx 的 1024 字符限制）
powershell -NoProfile -Command ^
  "$cur = [Environment]::GetEnvironmentVariable('Path','User');" ^
  "$target = '!SCRIPTS_DIR!';" ^
  "$parts = @($cur -split ';' | Where-Object { $_ -and ($_ -ne $target) });" ^
  "$parts += $target;" ^
  "$new = ($parts -join ';');" ^
  "[Environment]::SetEnvironmentVariable('Path', $new, 'User');" ^
  "Write-Host ('[OK] 已写入用户 PATH: ' + $target)"

if errorlevel 1 (
    echo [!] 写入失败，请尝试手动添加：把下面目录加入系统 PATH
    echo     !SCRIPTS_DIR!
    pause
    exit /b 1
)

echo.
echo ==== 完成 ====
echo 已把 Scripts 目录永久加入用户 PATH（重启不丢）。
echo.
echo 注意：已经打开的命令行窗口不会自动继承新 PATH，
echo 请「关闭旧窗口重新打开」或「重启电脑」，然后验证：
echo.
echo     pip --version
echo.
pause
