@echo off
title Hand Slide Controller
cd /d "%~dp0"

echo ========================================================
echo        HAND SLIDE CONTROLLER (DONG BO SLIDE BANG TAY)
echo ========================================================
echo.

if exist ".venv\Scripts\python.exe" (
    echo [INFO] Dang khoi dong bang moi truong Python ao (.venv)...
    ".venv\Scripts\python.exe" main.py
) else (
    echo [INFO] Dang khoi dong bang Python he thong...
    python main.py
)

if %errorlevel% neq 0 (
    echo.
    echo [LOI] Chuong trinh bi dung dot ngot (Ma loi: %errorlevel%).
    pause
)