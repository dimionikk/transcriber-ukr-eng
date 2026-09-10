' Double-click to open the lecture transcriber WINDOW with no console at all.
' First run: if dependencies are missing it launches setup.bat (visible) and waits.
Option Explicit
Dim fso, sh, q, base, pyw, gui, setup
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
q = Chr(34)
base  = fso.GetParentFolderName(WScript.ScriptFullName)
pyw   = base & "\.venv\Scripts\pythonw.exe"
gui   = base & "\gui.py"
setup = base & "\setup.bat"

sh.CurrentDirectory = base

If Not fso.FileExists(pyw) Then
    sh.Run "cmd /c " & q & setup & q, 1, True   ' visible installer, wait for it
End If

If fso.FileExists(pyw) Then
    sh.Run q & pyw & q & " " & q & gui & q, 0, False   ' hidden launcher, no wait
Else
    MsgBox "Залежності не встановлено. Запустіть setup.bat вручну.", _
           vbExclamation, "Транскрипція лекції"
End If
