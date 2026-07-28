@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
set "SCRIPT=RPA자동화스튜디오_단일파일.py"

where py >nul 2>nul
if !errorlevel! equ 0 (
    set "PYEXE=py"
) else (
    where python >nul 2>nul
    if !errorlevel! equ 0 (
        set "PYEXE=python"
    ) else (
        echo Python이 설치되어 있지 않습니다.
        echo https://www.python.org/downloads/ 에서 설치하세요.
        echo 설치 화면에서 반드시 "Add python.exe to PATH"를 체크해야 합니다.
        pause
        exit /b 1
    )
)

"!PYEXE!" -c "import pyautogui, pynput, PIL" >nul 2>nul
if not !errorlevel! equ 0 (
    echo 처음 실행이라 필요한 패키지를 설치합니다. 잠시만 기다려 주세요...
    "!PYEXE!" -m pip install --disable-pip-version-check pyautogui pynput pillow
    if not !errorlevel! equ 0 (
        echo 패키지 설치에 실패했습니다. 인터넷 연결을 확인하거나 관리자 권한으로 다시 실행해 보세요.
        pause
        exit /b 1
    )
)

where pyw >nul 2>nul
if !errorlevel! equ 0 (
    start "" pyw "%SCRIPT%"
) else (
    start "" pythonw "%SCRIPT%"
)
