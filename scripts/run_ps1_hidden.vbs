' Run a PowerShell script with no console window (WScript window style 0).
' Task Scheduler's powershell.exe -WindowStyle Hidden still allocates a console
' at interactive logon; this wrapper avoids that.
'
' Usage:
'   wscript.exe //B //Nologo scripts\run_ps1_hidden.vbs "C:\path\to\script.ps1"
'
Option Explicit
Dim sh, script, i, extra, cmd, exitCode

If WScript.Arguments.Count < 1 Then
  WScript.Quit 1
End If

script = WScript.Arguments(0)
extra = ""
For i = 1 To WScript.Arguments.Count - 1
  extra = extra & " " & WScript.Arguments(i)
Next

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """" & extra
Set sh = CreateObject("WScript.Shell")
' 0 = hidden; True = wait so Task Scheduler gets a real exit code
exitCode = sh.Run(cmd, 0, True)
WScript.Quit exitCode
