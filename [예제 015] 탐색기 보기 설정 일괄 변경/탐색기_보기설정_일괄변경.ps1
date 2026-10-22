<#
윈도우 11 탐색기 보기 설정 일괄 변경 (PowerShell 버전)
- 대상: D 드라이브(기본값)와 모든 하위 폴더
- 보기: 자세히
- 정렬 기준: 수정한 날짜 (최신 순)
- 분류 방법: 유형

동작 원리
  탐색기 창을 하나 띄운 뒤 폴더를 하나씩 이동하면서 보기 설정을 바꿉니다.
  탐색기는 폴더를 떠날 때 그 폴더의 보기 설정을 레지스트리(Bags)에 저장하므로,
  한 번 실행해 두면 이후에도 설정이 유지됩니다.

사용법 (PowerShell)
  powershell -ExecutionPolicy Bypass -File .\탐색기_보기설정_일괄변경.ps1             # D:\ 전체
  powershell -ExecutionPolicy Bypass -File .\탐색기_보기설정_일괄변경.ps1 E:\사진     # 다른 폴더 지정
  powershell -ExecutionPolicy Bypass -File .\탐색기_보기설정_일괄변경.ps1 -DryRun    # 대상 폴더 개수만 확인
  (실행 전에 확인을 묻습니다. 묻지 않고 실행하려면 -Yes)

주의
  - 실행 중에는 열리는 탐색기 창을 닫거나 조작하지 마세요.
  - 폴더 수가 많으면 시간이 걸립니다 (폴더당 약 0.1~0.5초).
#>

param(
    [string]$Root = "D:\",
    [switch]$DryRun,
    [switch]$Yes
)

$ViewDetails = 4                              # 자세히
$SortColumns = "prop:-System.DateModified;"   # 수정한 날짜, '-' = 내림차순(최신 순)
$GroupBy     = "System.ItemTypeText"          # 유형

$SkipNames = @("System Volume Information", "Config.Msi")
$ShellKey  = "HKCU:\Software\Classes\Local Settings\Software\Microsoft\Windows\Shell"

function Get-TargetFolders([string]$Start) {
    $result = New-Object System.Collections.Generic.List[string]
    $queue  = New-Object System.Collections.Generic.Queue[string]
    $queue.Enqueue($Start)
    while ($queue.Count -gt 0) {
        $dir = $queue.Dequeue()
        $result.Add($dir)
        $children = Get-ChildItem -LiteralPath $dir -Directory -Force -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            if ($SkipNames -contains $child.Name -or $child.Name.StartsWith('$')) { continue }
            # 정션/심볼릭 링크는 무한 반복을 막기 위해 건너뜀
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            $queue.Enqueue($child.FullName)
        }
    }
    return $result
}

function Set-BagLimit([int]$Count) {
    # 탐색기는 기본적으로 폴더 5000개까지만 보기 설정을 기억하므로 한도를 늘린다
    $needed = [Math]::Max($Count + 1000, 5000)
    if (-not (Test-Path $ShellKey)) { New-Item -Path $ShellKey -Force | Out-Null }
    $current = (Get-ItemProperty -Path $ShellKey -Name "BagMRU Size" -ErrorAction SilentlyContinue)."BagMRU Size"
    if (-not $current) { $current = 5000 }
    if ($current -lt $needed) {
        Set-ItemProperty -Path $ShellKey -Name "BagMRU Size" -Value $needed -Type DWord
        Write-Host "BagMRU Size: $current -> $needed"
    }
}

function Get-WindowPath($Window) {
    try { return $Window.Document.Folder.Self.Path.TrimEnd('\').ToLower() } catch { return $null }
}

function Open-Explorer($Shell, [string]$Path) {
    $before = @($Shell.Windows() | ForEach-Object { $_.HWND })
    Start-Process explorer.exe -ArgumentList "`"$Path`""
    for ($i = 0; $i -lt 100; $i++) {
        Start-Sleep -Milliseconds 100
        foreach ($w in $Shell.Windows()) {
            try {
                if ($before -notcontains $w.HWND -and $null -ne $w.Document) { return $w }
            } catch { }
        }
    }
    throw "탐색기 창을 찾지 못했습니다."
}

function Wait-Navigation($Window, [string]$Path, [int]$TimeoutSec = 10) {
    $target = $Path.TrimEnd('\').ToLower()
    $end = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $end) {
        try {
            if (-not $Window.Busy -and (Get-WindowPath $Window) -eq $target) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 50
    }
    return $false
}

$Root = (Resolve-Path -LiteralPath $Root).ProviderPath
Write-Host "폴더 목록 수집 중: $Root"
$folders = Get-TargetFolders $Root
Write-Host "대상 폴더: $($folders.Count)개"
if ($DryRun) { return }
if (-not $Yes) {
    $answer = Read-Host "보기 설정을 바꿀까요? (y/N)"
    if ($answer.Trim().ToLower() -ne "y") {
        Write-Host "취소했습니다."
        return
    }
}

Set-BagLimit $folders.Count

$shell  = New-Object -ComObject Shell.Application
$window = Open-Explorer $shell $Root

$failed = New-Object System.Collections.Generic.List[string]
$i = 0
foreach ($folder in $folders) {
    $i++
    try {
        $window.Navigate2($folder)
        if (Wait-Navigation $window $folder) {
            $doc = $window.Document
            $doc.CurrentViewMode = $ViewDetails
            $doc.SortColumns     = $SortColumns
            $doc.GroupBy         = $GroupBy
        } else {
            $failed.Add($folder)
        }
    } catch {
        $failed.Add($folder)
    }
    if ($i % 50 -eq 0 -or $i -eq $folders.Count) {
        Write-Host "  $i/$($folders.Count)"
    }
}

# 마지막 폴더의 설정도 저장되도록 루트로 돌아간 뒤 창을 닫는다
$window.Navigate2($Root)
Wait-Navigation $window $Root | Out-Null
$window.Quit()

Write-Host "완료: $($folders.Count - $failed.Count)개 성공, $($failed.Count)개 실패"
$failed | Select-Object -First 20 | ForEach-Object { Write-Host "  실패: $_" }
