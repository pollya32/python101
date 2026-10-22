<#
탐색기 보기 설정 되돌리기 (탐색기_보기설정_일괄변경.ps1 실행 전 상태로)

동작
  1. 현재 폴더 보기 설정(레지스트리 Bags/BagMRU)을 .reg 파일로 백업
  2. 탐색기를 잠시 종료한 뒤 폴더별 보기 설정을 모두 삭제
  3. 'BagMRU Size'를 기본값(5000)으로 되돌림
  4. 탐색기를 다시 실행

주의
  - 탐색기에는 'D 드라이브 폴더만 골라서 지우는' 방법이 없어서,
    D 드라이브뿐 아니라 PC의 모든 폴더 보기 설정이 윈도우 기본값으로 초기화됩니다.
  - 열려 있는 탐색기 창은 모두 닫히고, 작업 표시줄이 잠깐 사라졌다 돌아옵니다.
  - 백업 파일(.reg)을 더블클릭하면 초기화 전 상태로 다시 복원할 수 있습니다.

사용법 (PowerShell)
  powershell -ExecutionPolicy Bypass -File .\탐색기_보기설정_되돌리기.ps1         # 확인 후 실행
  powershell -ExecutionPolicy Bypass -File .\탐색기_보기설정_되돌리기.ps1 -Yes    # 확인 없이 실행
#>

param(
    [switch]$Yes
)

$ShellKey = "Software\Classes\Local Settings\Software\Microsoft\Windows\Shell"
$BagKeys = @(
    "$ShellKey\BagMRU",
    "$ShellKey\Bags",
    "Software\Microsoft\Windows\Shell\BagMRU",
    "Software\Microsoft\Windows\Shell\Bags"
)

Write-Host "PC의 모든 폴더 보기 설정을 윈도우 기본값으로 초기화합니다."
Write-Host "열려 있는 탐색기 창은 모두 닫힙니다."
if (-not $Yes) {
    $answer = Read-Host "계속할까요? (y/N)"
    if ($answer.Trim().ToLower() -ne "y") {
        Write-Host "취소했습니다."
        return
    }
}

$keys = @($BagKeys | Where-Object { Test-Path "HKCU:\$_" })

if ($keys.Count -gt 0) {
    $backupDir = Join-Path $PSScriptRoot ("백업_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    New-Item -ItemType Directory -Path $backupDir | Out-Null
    $i = 0
    foreach ($key in $keys) {
        $i++
        $out = Join-Path $backupDir ("{0}_{1}.reg" -f $i, (Split-Path $key -Leaf))
        reg export "HKCU\$key" "$out" /y | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "백업 실패: $key" }
    }
    Write-Host "백업 위치: $backupDir"
}

# 탐색기가 실행 중이면 종료할 때 설정을 다시 써 버리므로 먼저 종료
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

foreach ($key in $keys) {
    Remove-Item -Path "HKCU:\$key" -Recurse -Force -ErrorAction SilentlyContinue
}

Remove-ItemProperty -Path "HKCU:\$ShellKey" -Name "BagMRU Size" -ErrorAction SilentlyContinue

Start-Process explorer.exe
Write-Host "완료: 폴더 보기 설정을 초기화했습니다."
