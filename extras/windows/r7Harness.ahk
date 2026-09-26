#Requires AutoHotkey v2.0
#SingleInstance Force

; Optional Windows companion for r7Harness running in WSL.
; Set the environment variables below before starting the script to change the defaults.

global TerminalExecutable := EnvOrDefault("R7HARNESS_TERMINAL_EXE", "wt.exe")
global ExpectedTerminalProcess := EnvOrDefault("R7HARNESS_TERMINAL_PROCESS", "WindowsTerminal.exe")
global SessionCommand := EnvOrDefault("R7HARNESS_LAUNCH_COMMAND", "wsl.exe -e bash -lc " Chr(34) "r7harness launch" Chr(34))
global WindowTitle := "r7Harness"
global LayoutMode := 0

SetWinDelay -1

!CapsLock::RecallOrLaunch()
^!CapsLock::LaunchSession()
!+CapsLock::CycleLayout()

RecallOrLaunch() {
    windows := HarnessWindows()
    if (windows.Length = 0) {
        LaunchSession()
        return
    }

    ; the list is in z-order, so bringing up the bottom one each time cycles through all of them
    active := WinExist("A")
    next := windows[1]
    for hwnd in windows {
        if (hwnd = active) {
            next := windows[windows.Length]
            break
        }
    }
    WinActivate "ahk_id " next
}

LaunchSession() {
    global TerminalExecutable, SessionCommand, WindowTitle

    ; Each session gets its own window. The title starts as r7Harness, then follows the task title.
    command := QuoteArgument(TerminalExecutable) " -w new --title " WindowTitle " " SessionCommand
    try Run(command)
    catch as err
        MsgBox "Could not start r7Harness.`n`n" err.Message, "r7Harness", "Iconx"
}

CycleLayout() {
    global LayoutMode

    windows := HarnessWindows()
    if (windows.Length = 0) {
        LaunchSession()
        return
    }

    LayoutMode := Mod(LayoutMode, 3) + 1
    left := 0, top := 0, right := 0, bottom := 0
    GetWorkArea(windows[1], &left, &top, &right, &bottom)
    width := right - left
    height := bottom - top

    if (LayoutMode = 1) {
        PlaceWindow(windows[1], left, top, width, height)
        return
    }

    if (LayoutMode = 2) {
        halfWidth := Floor(width / 2)
        PlaceWindow(windows[1], left, top, halfWidth, height)
        if (windows.Length >= 2)
            PlaceWindow(windows[2], left + halfWidth, top, width - halfWidth, height)
        return
    }

    halfWidth := Floor(width / 2)
    halfHeight := Floor(height / 2)
    slots := [
        [left, top],
        [left + halfWidth, top],
        [left, top + halfHeight],
        [left + halfWidth, top + halfHeight]
    ]
    limit := Min(windows.Length, 4)
    Loop limit {
        slot := slots[A_Index]
        slotWidth := (Mod(A_Index, 2) = 1) ? halfWidth : width - halfWidth
        slotHeight := (A_Index <= 2) ? halfHeight : height - halfHeight
        PlaceWindow(windows[A_Index], slot[1], slot[2], slotWidth, slotHeight)
    }
}

HarnessWindows() {
    global ExpectedTerminalProcess
    ; the title changes with each task, so every Windows Terminal window counts
    return WinGetList("ahk_exe " ExpectedTerminalProcess)
}

PlaceWindow(hwnd, left, top, width, height) {
    try {
        WinRestore "ahk_id " hwnd
        WinMove left, top, width, height, "ahk_id " hwnd
    }
}

GetWorkArea(hwnd, &left, &top, &right, &bottom) {
    monitor := DllCall("MonitorFromWindow", "ptr", hwnd, "uint", 2, "ptr")
    info := Buffer(40, 0)
    NumPut("uint", 40, info, 0)
    if (monitor && DllCall("GetMonitorInfo", "ptr", monitor, "ptr", info.Ptr, "int")) {
        left := NumGet(info, 20, "int")
        top := NumGet(info, 24, "int")
        right := NumGet(info, 28, "int")
        bottom := NumGet(info, 32, "int")
        return
    }
    MonitorGetWorkArea(MonitorGetPrimary(), &left, &top, &right, &bottom)
}

QuoteArgument(value) {
    quote := Chr(34)
    return quote StrReplace(value, quote, quote quote) quote
}

EnvOrDefault(name, fallback) {
    value := EnvGet(name)
    return value != "" ? value : fallback
}
