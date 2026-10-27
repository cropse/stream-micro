Option Explicit
Dim sh, fso, root, pythonw, command
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "Python environment missing. Run uv sync --dev first.", 16, "Stream Micro"
    WScript.Quit 1
End If
sh.CurrentDirectory = root
command = """" & pythonw & """ -m stream_micro.cli tray --bridge-config """ & root & "\bridge.yaml"""
sh.Run command, 0, False
