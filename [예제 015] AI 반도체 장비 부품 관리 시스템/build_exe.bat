@echo off
chcp 65001 >nul
rem ============================================================
rem  SmartParts.exe 빌드 스크립트 (Windows)
rem  더블클릭 한 번이면 dist\SmartParts.exe 가 생성됩니다.
rem  필요 조건: Python 3.9+ 설치 (python.org, "Add to PATH" 체크)
rem ============================================================
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)

echo [1/3] 필요한 패키지 설치 중...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :err

echo [2/3] 실행 파일 빌드 중... (1~3분 소요)
%PY% -m PyInstaller --noconfirm --clean --onefile --name SmartParts ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --hidden-import qrcode.image.svg ^
  --hidden-import waitress ^
  app.py
if errorlevel 1 goto :err

echo [3/3] 완료!
echo.
echo  생성 위치: %~dp0dist\SmartParts.exe
echo  이 파일 하나만 호스트 PC 아무 폴더에 복사해 실행하면 됩니다.
echo  (DB 파일 smart_parts.db 는 exe 옆에 자동 생성됩니다)
echo.
pause
exit /b 0

:err
echo.
echo  [오류] 빌드에 실패했습니다. Python 설치 여부를 확인하세요.
pause
exit /b 1
