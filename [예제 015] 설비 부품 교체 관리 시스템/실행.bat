@echo off
chcp 65001 >nul
title 설비 부품 교체 관리 시스템

set "SCRIPT_DIR=%~dp0"
set "APP_FILE=%SCRIPT_DIR%설비부품교체관리_단일파일.py"

if not exist "%APP_FILE%" (
    echo [오류] "%APP_FILE%" 파일을 찾을 수 없습니다.
    echo 이 배치 파일을 단일파일 배포판^(설비부품교체관리_단일파일.py^)과 같은 폴더에 두고 실행해주세요.
    pause
    exit /b 1
)

set "PYCMD="
where python >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
    where py >nul 2>nul
    if not errorlevel 1 set "PYCMD=py"
)
if not defined PYCMD (
    echo [오류] Python이 설치되어 있지 않은 것 같습니다.
    echo https://www.python.org 에서 Python을 설치한 뒤 다시 실행해주세요.
    pause
    exit /b 1
)

set "PORT=5000"
set /p "PORT_INPUT=포트 번호를 입력하세요 (1024~65535 사이 추천, 기본값: 5000, 그대로 두려면 Enter): "
if not "%PORT_INPUT%"=="" set "PORT=%PORT_INPUT%"

echo %PORT%| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo [안내] 올바른 숫자가 아니라서 기본 포트^(5000^)로 실행합니다.
    set "PORT=5000"
)

echo(
echo 설비 부품 교체 관리 시스템을 포트 %PORT%번으로 시작합니다...
echo 같은 컴퓨터에서 다른 설비군을 위해 프로그램을 하나 더 띄우려면, 이 폴더를
echo 통째로 복사한 뒤 복사본에서 다른 포트 번호를 입력해 실행하세요.
echo 종료하려면 이 창에서 Ctrl+C를 누르세요.
echo(

%PYCMD% "%APP_FILE%" %PORT%

echo(
echo 프로그램이 종료되었습니다.
pause
