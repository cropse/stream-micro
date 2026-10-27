@echo off
REM Compatibility shortcut: all desktop launches use the windowless tray entry.
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0start.vbs"
