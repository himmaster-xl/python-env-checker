@echo off
rem Python 环境检查器 — 双击启动
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py
) else (
    start "" python main.py
)
