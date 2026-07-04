Option Explicit

Dim oFSO, oShell, oLink
Set oFSO   = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")

' 이 파일이 있는 폴더 경로
Dim strFolder
strFolder = oFSO.GetParentFolderName(WScript.ScriptFullName)

Dim strVBS, strICO, strDesktop, strLNK
strVBS     = strFolder & "\FDC이상감지_실행.vbs"
strICO     = strFolder & "\fdc_icon.ico"
strDesktop = oShell.SpecialFolders("Desktop")
strLNK     = strDesktop & "\FDC 이상감지 시스템.lnk"

' VBS 런처 확인
If Not oFSO.FileExists(strVBS) Then
    MsgBox "FDC이상감지_실행.vbs 파일을 찾을 수 없습니다." & vbCrLf & vbCrLf & strVBS, 16, "FDC 이상감지 - 오류"
    WScript.Quit 1
End If

' 바탕화면 바로가기 생성
Set oLink = oShell.CreateShortcut(strLNK)
oLink.TargetPath       = "wscript.exe"
oLink.Arguments        = """" & strVBS & """"
oLink.WorkingDirectory = strFolder
oLink.Description      = "FDC RAW DATA 이상감지 시스템"
If oFSO.FileExists(strICO) Then
    oLink.IconLocation = strICO & ",0"
Else
    oLink.IconLocation = "imageres.dll,175"
End If
oLink.Save

' 결과 확인
If oFSO.FileExists(strLNK) Then
    MsgBox "바탕화면에 'FDC 이상감지 시스템' 아이콘이 등록되었습니다!" & vbCrLf & vbCrLf & _
           "주의: 아래 파일들을 현재 폴더에서 이동하지 마세요." & vbCrLf & _
           "  - index.html" & vbCrLf & _
           "  - FDC이상감지_실행.vbs" & vbCrLf & _
           "  - fdc_icon.ico", 64, "FDC 이상감지 - 완료"
Else
    MsgBox "바로가기 생성에 실패했습니다." & vbCrLf & "바탕화면 경로: " & strLNK, 16, "FDC 이상감지 - 오류"
End If
