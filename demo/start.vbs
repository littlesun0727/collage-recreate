Set fso = CreateObject("Scripting.FileSystemObject")

Set shell = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)

shell.Run Chr(34) & base & "\runtime\pythonw.exe" & Chr(34) & " " & Chr(34) & base & "\edit.py" & Chr(34) & "", 0, False
