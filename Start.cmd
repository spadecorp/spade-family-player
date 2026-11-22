@echo off
setlocal
cd /d "%~dp0"
where ffmpeg >nul 2>&1 || goto missing
where ffprobe >nul 2>&1 || goto missing
py -3.11 client\player.py
if errorlevel 1 pause
exit /b
:missing
echo Developer setup requires FFmpeg and FFprobe on PATH. See README.md.
pause
