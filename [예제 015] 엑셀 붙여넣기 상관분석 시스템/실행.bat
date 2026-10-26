@echo off
rem Double-click to run. Commands are ASCII only; only messages are Korean (UTF-8, chcp 65001).
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 엑셀 붙여넣기 분석 시스템

rem ---- 1. App file: the only .py file in this folder ----
set "APP="
for %%F in ("%~dp0*.py") do set "APP=%%~fF"
if defined APP goto :find_python
echo [오류] 이 폴더에서 실행할 .py 파일을 찾지 못했습니다.
goto :fail

rem ---- 2. Python 3.9+ (py launcher first, then python) ----
:find_python
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3"
if defined PY goto :make_venv
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
if defined PY goto :make_venv
echo [오류] Python 3.9 이상을 찾지 못했습니다.
echo   1. https://www.python.org/downloads/ 에서 Python 을 설치하세요.
echo   2. 설치 첫 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
echo   3. 설치 후 이 파일을 다시 더블클릭하세요.
goto :fail

rem ---- 3. Private environment in .venv (first run only) ----
:make_venv
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
if exist "%VPY%" goto :install
echo 처음 실행: 이 폴더 전용 Python 환경을 만듭니다...
%PY% -m venv "%VENV%"
if errorlevel 1 goto :fail_venv

rem ---- 4. Install packages when requirements.txt is new or changed ----
:install
fc /b "%~dp0requirements.txt" "%VENV%\requirements.installed" >nul 2>nul
if not errorlevel 1 goto :run
echo 필요한 패키지를 설치합니다. 처음 한 번만, 몇 분 걸릴 수 있습니다...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -r "%~dp0requirements.txt" --disable-pip-version-check
if errorlevel 1 goto :fail_pip
copy /y "%~dp0requirements.txt" "%VENV%\requirements.installed" >nul

rem ---- 5. Skip Streamlit's first-run e-mail prompt ----
:run
if exist "%USERPROFILE%\.streamlit\credentials.toml" goto :start
mkdir "%USERPROFILE%\.streamlit" 2>nul
> "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
>> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""

rem ---- 6. Start (browser opens automatically) ----
:start
echo.
echo ============================================================
echo   분석 시스템을 시작합니다. 브라우저가 자동으로 열립니다.
echo   안 열리면 아래 Local URL 을 브라우저 주소창에 입력하세요.
echo   끝내려면 이 창을 닫거나 Ctrl+C 를 누르세요.
echo ============================================================
echo.
"%VPY%" -m streamlit run "%APP%" --server.headless false --server.address localhost --browser.gatherUsageStats false
if errorlevel 1 goto :fail
goto :eof

:fail_venv
echo [오류] Python 환경을 만들지 못했습니다. 폴더에 쓰기 권한이 있는지 확인하세요.
goto :fail

:fail_pip
echo.
echo [오류] 패키지 설치에 실패했습니다.
echo   - 인터넷 연결을 확인하세요.
echo   - 회사망이라면 프록시 설정이 필요할 수 있습니다. 이 파일을 메모장으로 열어
echo     맨 위 부근에 다음 줄을 추가하세요:  set HTTPS_PROXY=http://프록시주소:포트
echo   - 설치를 다시 시도하려면 이 파일을 다시 더블클릭하세요.
goto :fail

:fail
echo.
pause
exit /b 1
