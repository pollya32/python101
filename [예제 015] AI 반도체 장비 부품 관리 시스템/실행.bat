@echo off
chcp 65001 >nul
rem exe 빌드 없이 파이썬으로 바로 실행하고 싶을 때 사용하세요.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% -m pip install -r requirements.txt
%PY% app.py
pause
