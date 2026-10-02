r7-Harness Windows companion
===========================

An optional AutoHotkey v2 script for running r7-Harness from WSL2 in Windows
Terminal. It adds hotkeys to launch sessions, bring them back and tile them.
r7-Harness works fine without it.

You need
--------

- Windows 10 or 11 with WSL2 and Windows Terminal.
- AutoHotkey v2: https://www.autohotkey.com/
- r7-Harness installed in WSL with `r7harness` on the WSL PATH. From the repo:

    ln -s "$PWD/bin/r7harness" ~/.local/bin/r7harness

Setup
-----

Copy r7-Harness.ahk somewhere on Windows and run it with AutoHotkey v2.
Close it from the tray icon to turn the hotkeys off.

Hotkeys
-------

  Alt+CapsLock          Bring up a terminal window. Press again to cycle
                        through them. Launches r7-Harness if none are open.
  Ctrl+Alt+CapsLock     Launch a new r7-Harness session.
  Alt+Shift+CapsLock    Cycle the terminal windows through full screen, two
                        columns and a 2x2 grid on the first window's monitor.

Each session opens in its own Windows Terminal window. The window title
follows the agent's task title, so the hotkeys work on every Windows Terminal
window, not just r7-Harness ones.

Settings
--------

Set these environment variables before starting the script to change the
defaults. Restart the script after changing them.

  R7HARNESS_TERMINAL_EXE       wt.exe
  R7HARNESS_TERMINAL_PROCESS   WindowsTerminal.exe
  R7HARNESS_LAUNCH_COMMAND     wsl.exe -e bash -lc "r7harness launch"

R7HARNESS_LAUNCH_COMMAND is the command run in the new terminal window. For a
specific distro use something like:

  wsl.exe -d Ubuntu -e bash -lc "r7harness launch"

Don't run it next to another script that uses the same CapsLock hotkeys.
