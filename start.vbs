' Launches start.bat with no visible console window.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
' Hidden launches have no console, so keep start.bat's output in bridge.log.
' At each start a log past 5 MB is set aside as bridge.log.1 (replacing the
' previous one). A log still held by a running Bridge cannot move; it is then
' left as it is, and the previous bridge.log.1 is kept.
logPath = sh.CurrentDirectory & "\bridge.log"
On Error Resume Next
If fso.FileExists(logPath) Then
    If fso.GetFile(logPath).Size > 5242880 Then
        fso.MoveFile logPath, logPath & ".rotating"
        If Err.Number = 0 Then
            If fso.FileExists(logPath & ".1") Then fso.DeleteFile logPath & ".1", True
            fso.MoveFile logPath & ".rotating", logPath & ".1"
        End If
        Err.Clear
    End If
End If
On Error GoTo 0
sh.Run "cmd /c """"" & sh.CurrentDirectory & "\start.bat"" >> """ & logPath & """ 2>&1""", 0, False
