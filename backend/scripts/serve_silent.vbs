' LifeLab backend launcher -- starts serve.cmd with NO visible console window.
'
' Why this exists: the "LifeLab Server" scheduled task runs under an *interactive*
' logon, and launching a .cmd that way pops up a console window. serve.cmd sends all
' of its output to ~/.lifelab/server.log, so that window shows NOTHING -- it looks
' like a stray, empty cmd. Closing it delivers Ctrl+C to uvicorn, which then exits
' with 0xC000013A. In practice the service kept dying minutes after logon, and the
' "empty cmd" was the service's own window the whole time.
'
' WScript.Shell.Run with window style 0 creates the child with a hidden console.
' Same trick pc_collector.py uses for --install-autostart.
'
' ASCII-only on purpose (same reason as serve.cmd): wscript parses a .vbs under the
' OEM code page, and a mis-decoded byte can leak out of a comment line as code.
Option Explicit

Dim fso, sh, here, target
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(here, "serve.cmd")

' 0 = hidden window; True = wait for the child. Waiting matters: it keeps the task
' "Running" for as long as uvicorn is alive, so the task's RestartCount (3, 1 min)
' still applies. With False, wscript would exit immediately, the task would report
' success at once, and a crashed uvicorn would never be restarted.
sh.Run """" & target & """", 0, True
