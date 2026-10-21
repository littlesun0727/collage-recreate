Option Explicit
Dim shell, fso, root, python, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = "D:\codes\visual-recreate-validation\clean-env\Scripts\python.exe"
If Not fso.FileExists(python) Then
    MsgBox "Configure Python in start.vbs, or run Python launch.py from this directory.", 48, "Collage v6"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " -B " & Chr(34) & root & "\launch.py" & Chr(34)
If shell.Run(command, 0, True) = 0 Then
    shell.Run "http://127.0.0.1:8796", 1, False
Else
    MsgBox "Startup failed. Check the sibling collage-recreate-v6-output\runtime\server-error.log.", 48, "Collage v6"
End If
