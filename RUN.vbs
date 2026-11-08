' Double-click this entry to start both interpreter discovery and the GUI hidden.
Option Explicit
Dim shell, env, fs, command, i, arg, key, status
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
Set env = shell.Environment("Process")
shell.CurrentDirectory = fs.GetParentFolderName(WScript.ScriptFullName)
env("DSA_WINDOWLESS_LAUNCH") = "1"
command = Chr(34) & env("ComSpec") & Chr(34) & " /d /v:off /c RUN.bat"
For i = 0 To WScript.Arguments.Count - 1
    arg = WScript.Arguments(i)
    If InStr(arg, Chr(34)) > 0 Then WScript.Quit 2
    key = "DSA_GUI_ARG_" & i
    env(key) = arg
    command = command & " " & Chr(34) & "%" & key & "%" & Chr(34)
Next
status = shell.Run(command, 0, True)
If status = 2 Then MsgBox "No existing Python runtime. Set DIE_SHIFT_PYTHON or use the packaged EXE.", vbCritical, "Die Shift Analyze"
WScript.Quit status
