Option Explicit

Dim oFSO, oShell, oLink
Set oFSO   = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")

Dim strFolder, strVBS, strICO, strDesktop, strLNK
strFolder  = oFSO.GetParentFolderName(WScript.ScriptFullName)
strVBS     = strFolder & "\FDC_Run.vbs"
strICO     = strFolder & "\fdc_icon.ico"
strDesktop = oShell.SpecialFolders("Desktop")
strLNK     = strDesktop & "\FDC Anomaly Detection.lnk"

If Not oFSO.FileExists(strVBS) Then
    MsgBox "FDC_Run.vbs not found:" & vbCrLf & strVBS, 16, "FDC Error"
    WScript.Quit 1
End If

Set oLink = oShell.CreateShortcut(strLNK)
oLink.TargetPath       = "wscript.exe"
oLink.Arguments        = Chr(34) & strVBS & Chr(34)
oLink.WorkingDirectory = strFolder
oLink.Description      = "FDC RAW DATA Anomaly Detection"
If oFSO.FileExists(strICO) Then
    oLink.IconLocation = strICO & ",0"
Else
    oLink.IconLocation = "imageres.dll,175"
End If
oLink.Save

If oFSO.FileExists(strLNK) Then
    MsgBox "Desktop shortcut created!" & vbCrLf & strLNK, 64, "FDC Done"
Else
    MsgBox "Failed to create shortcut:" & vbCrLf & strLNK, 16, "FDC Error"
End If
