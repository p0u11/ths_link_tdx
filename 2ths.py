# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
import sys
import time

import win32gui
import win32process

THS_STOCK_MSG = 1168
PAGE_READWRITE = 0x04
PROCESS_ACCESS = 0x0400 | 0x1000 | 0x0008 | 0x0010 | 0x0020
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
FREE_DELAY_SEC = 0.2


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.VirtualAllocEx.restype = wintypes.LPVOID
kernel32.VirtualFreeEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def _find_ths_window() -> int:
    hwnds: list[int] = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if "同花顺" in title:
            hwnds.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return hwnds[0] if hwnds else 0


def _build_payload(raw: str) -> bytes:
    text = raw.strip().upper()
    if text.endswith(".SH") or text.endswith(".SZ"):
        text = text[:-3]
    elif text.startswith("SH") or text.startswith("SZ"):
        text = text[2:]
    return b"\x11" + text.encode("gbk") + b"\x00"


def send_ths_jump(raw_code: str) -> None:
    hwnd = _find_ths_window()
    if not hwnd:
        raise RuntimeError("未找到同花顺窗口")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not handle:
        raise RuntimeError("OpenProcess 失败")

    payload = _build_payload(raw_code)
    addr = kernel32.VirtualAllocEx(handle, None, len(payload), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not addr:
        kernel32.CloseHandle(handle)
        raise RuntimeError("VirtualAllocEx 失败")

    ok = kernel32.WriteProcessMemory(handle, addr, payload, len(payload), None)
    if not ok:
        kernel32.VirtualFreeEx(handle, addr, 0, MEM_RELEASE)
        kernel32.CloseHandle(handle)
        raise RuntimeError("WriteProcessMemory 失败")

    user32.SendMessageW(hwnd, THS_STOCK_MSG, 0, addr)
    time.sleep(FREE_DELAY_SEC)
    kernel32.VirtualFreeEx(handle, addr, 0, MEM_RELEASE)
    kernel32.CloseHandle(handle)


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    try:
        send_ths_jump(sys.argv[1])
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
