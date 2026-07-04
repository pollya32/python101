@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

echo.
echo  ================================================
echo   FDC 이상감지 시스템 - 바탕화면 아이콘 등록
echo  ================================================
echo.

:: 이 BAT 파일이 있는 폴더 기준으로 경로 설정
set "FDC_DIR=%~dp0"
set "FDC_VBS=%FDC_DIR%FDC이상감지_실행.vbs"
set "FDC_ICO=%FDC_DIR%fdc_icon.ico"

:: VBS 파일 존재 확인
if not exist "%FDC_VBS%" (
    echo [오류] FDC이상감지_실행.vbs 파일이 없습니다.
    echo        이 BAT 파일과 같은 폴더에 있는지 확인하세요.
    pause
    exit /b 1
)

:: PowerShell로 바탕화면 바로가기 생성
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$vbs = $env:FDC_VBS;" ^
 "$ico = $env:FDC_ICO;" ^
 "$lnk = [IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'FDC 이상감지 시스템.lnk');" ^
 "$ws  = New-Object -ComObject WScript.Shell;" ^
 "$sc  = $ws.CreateShortcut($lnk);" ^
 "$sc.TargetPath       = 'wscript.exe';" ^
 "$sc.Arguments        = '\"' + $vbs + '\"';" ^
 "$sc.WorkingDirectory = Split-Path $vbs;" ^
 "$sc.Description      = 'FDC RAW DATA 이상감지 시스템';" ^
 "if (Test-Path $ico) { $sc.IconLocation = $ico + ',0' } else { $sc.IconLocation = 'imageres.dll,175' };" ^
 "$sc.Save();" ^
 "Write-Host '  [완료] 바탕화면에 아이콘이 등록되었습니다.'"

echo.
echo  주의: index.html, fdc_icon.ico, FDC이상감지_실행.vbs 파일을
echo        현재 폴더에서 이동하지 마세요.
echo.
pause
