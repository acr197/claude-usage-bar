' Claude Usage Bar - silent launcher
' Double-click this (or create a shortcut to it) to start the app with no
' command window. Run run.bat once first to set up the virtual environment.

Set oShell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

scriptDir  = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe  = scriptDir & "\.venv\Scripts\pythonw.exe"
mainScript = scriptDir & "\claude_usage_bar.py"

If Not fso.FileExists(pythonExe) Then
    MsgBox "Virtual environment not found." & vbCrLf & vbCrLf & _
           "Run run.bat once to set up dependencies, then use this shortcut.", _
           16, "Claude Usage Bar"
    WScript.Quit
End If

' Window style 0 = hidden; False = don't wait for it to finish
oShell.Run """" & pythonExe & """ """ & mainScript & """", 0, False
