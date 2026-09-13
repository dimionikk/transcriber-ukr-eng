Option Explicit
Dim fso, sh, q, base, pyw, gui, setup, answer, desktop, shortcutPath, sc
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
q = Chr(34)
base  = fso.GetParentFolderName(WScript.ScriptFullName)
pyw   = base & "\.venv\Scripts\pythonw.exe"
gui   = base & "\gui.py"
setup = base & "\setup.bat"

sh.CurrentDirectory = base

On Error Resume Next
desktop = sh.SpecialFolders("Desktop")
shortcutPath = desktop & "\Repo Installer.lnk"
If Not fso.FileExists(shortcutPath) Then
    Set sc = sh.CreateShortcut(shortcutPath)
    sc.TargetPath = base & "\Launch.vbs"
    sc.WorkingDirectory = base
    sc.Description = "Lecture Transcriber"
    If fso.FileExists(pyw) Then
        sc.IconLocation = pyw & ", 0"
    End If
    sc.Save
End If
On Error Goto 0

If Not fso.FileExists(pyw) Then
    answer = MsgBox("Не вистачає залежностей для роботи програми (~1.5 ГБ, одноразово)." & vbCrLf & vbCrLf & "Встановити зараз?", vbYesNo + vbQuestion, "Транскрипція лекції")
    If answer = vbYes Then
        sh.Run "cmd /c " & q & setup & q, 1, True
    End If
End If

If fso.FileExists(pyw) Then
    sh.Run q & pyw & q & " " & q & gui & q, 0, False
Else
    MsgBox "Залежності не встановлено. Запустіть setup.bat вручну.", vbExclamation, "Транскрипція лекції"
End If
