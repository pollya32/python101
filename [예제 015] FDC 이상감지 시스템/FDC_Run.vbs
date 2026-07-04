Option Explicit

Dim oFSO, oShell
Set oFSO   = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")

Dim strFolder, strHTML, strURL
strFolder = oFSO.GetParentFolderName(WScript.ScriptFullName)
strHTML   = strFolder & "\index.html"

If Not oFSO.FileExists(strHTML) Then
    MsgBox "index.html not found:" & vbCrLf & strHTML, 16, "FDC Error"
    WScript.Quit 1
End If

strURL = "file:///" & Replace(strHTML, "\", "/")

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
        oShell.Run Chr(34) & b & Chr(34) & " --app=" & Chr(34) & strURL & Chr(34) & " --window-size=1440,900", 0, False
        WScript.Quit 0
    End If
Next

oShell.Run Chr(34) & strHTML & Chr(34)
