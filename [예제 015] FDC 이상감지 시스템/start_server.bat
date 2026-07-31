@echo off
chcp 65001 >nul
echo =============================================
echo   FDC ML 이상감지 서버 설치 및 시작
echo =============================================

REM pip 패키지 설치 (최초 1회)
pip install -r requirements.txt --quiet

echo.
echo 서버 시작 중... http://localhost:8000
echo 종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
echo.

python server.py
pause
