Option Explicit

Dim oFSO, oShell
Set oFSO  = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")

' 이 스크립트와 같은 폴더의 index.html 경로
Dim strFolder, strHTML, strURL
strFolder = oFSO.GetParentFolderName(WScript.ScriptFullName)
strHTML   = strFolder & "\index.html"

If Not oFSO.FileExists(strHTML) Then
    MsgBox "index.html 파일을 찾을 수 없습니다." & vbCrLf & strHTML, 16, "FDC 이상감지"
    WScript.Quit 1
End If

' 파일 URL 변환 (백슬래시 -> 슬래시)
strURL = "file:///" & Replace(strHTML, "\", "/")

' Chrome / Edge 경로 순서대로 시도
Dim aBrowsers, b
aBrowsers = Array( _
    oShell.ExpandEnvironmentStrings("%ProgramFiles%\Google\Chrome\Application\chrome.exe"), _
    oShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"), _
    oShell.ExpandEnvironmentStrings("%LocalAppData%\Google\Chrome\Application\chrome.exe"), _
    oShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"), _
    oShell.ExpandEnvironmentStrings("%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") _
)

For Each b In aBrowsers
    If oFSO.FileExists(b) Then
        ' --app 모드: 주소창 없이 단독 앱 창으로 실행
        oShell.Run """" & b & """ --app=""" & strURL & """ --window-size=1440,900", 0, False
        WScript.Quit 0
    End If
Next

' Chrome/Edge 없으면 기본 브라우저로 열기
oShell.Run """" & strHTML & """"
