@echo off
title Build Hand Slide Controller EXE
cd /d "%~dp0"

echo ========================================================
echo        DONG GOI HAND SLIDE CONTROLLER SANG FILE .EXE
echo ========================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Khong tim thay moi truong ao .venv!
    pause
    exit /b 1
)

echo [1/3] Kiem tra va cai dat PyInstaller vao .venv...
".venv\Scripts\pip.exe" install pyinstaller -q

echo.
echo [2/3] Dang dong goi chuong trinh thanh file .exe (Khong hien CLI)...
echo       (Qua trinh nay mat khoang 30 giay, vui long doi...)
echo.

".venv\Scripts\pyinstaller.exe" --noconfirm --onedir --noconsole --collect-all mediapipe --add-data "hand_landmarker.task;." --name "HandSlideController" main.py

if %errorlevel% neq 0 (
    echo.
    echo [LOI] Co loi xay ra trong qua trinh dong goi!
    pause
    exit /b %errorlevel%
)

echo.
echo ========================================================
echo [3/3] THANH CONG! 
echo File .exe cua ban da duoc tao tai thu muc:
echo dist\HandSlideController\HandSlideController.exe
echo ========================================================
echo.
pause