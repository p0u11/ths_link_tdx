# -*- coding: utf-8 -*-
"""
TDX <-> THS 双向联动桥
"""
import time
import threading
import ctypes
from ctypes import wintypes

import pymem
import win32gui
import win32process

# ==================== 配置 ====================
class TDXConfig:
    process = "TdxW.exe"
    dll = "Viewthem.dll"
    offset = 0x160A64

class THSConfig:
    process = "hexin.exe"
    base_offset = 0x01E9A5B0
    pointer_offset = 0x1
    interval = 1

# 注入相关
THS_STOCK_MSG = 1168
PAGE_READWRITE = 0x04
PROCESS_ACCESS = 0x0400 | 0x1000 | 0x0008 | 0x0010 | 0x0020
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
FREE_DELAY_SEC = 0.2

# ==================== Windows API ====================
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualAllocEx.restype = wintypes.LPVOID
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

# ==================== 代码转换 ====================
def ths_to_tdx(code: str) -> str | None:
    """THS代码 -> TDX代码，过滤非A股返回None"""
    if not (len(code) == 6 and code.isdigit()):
        print(f"  ths_to_tdx: {code} -> [过滤]")
        return None
    if code.startswith("399") or code.startswith("0003"):
        print(f"  ths_to_tdx: {code} -> [过滤]")
        return None
    if code.startswith("6"):
        result = "7" + code  # 上海
    elif code.startswith(("0", "3")):
        result = "6" + code  # 深圳
    elif code.startswith("92"):
        result = "4" + code  # 北交
    else:
        print(f"  ths_to_tdx: {code} -> [过滤]")
        return None
    print(f"  ths_to_tdx: {code} -> {result} | hex: {code.encode().hex()} -> {result.encode().hex()}")
    return result

def tdx_to_ths(code: str) -> bytes | None:
    """TDX代码 -> THS注入载荷，过滤非6位数字返回None"""
    if not (len(code) == 6 and code.isdigit()):
        print(f"  tdx_to_ths: {code} -> [过滤]")
        return None
    if code.startswith("399") or code.startswith("0003"):
        print(f"  tdx_to_ths: {code} -> [过滤]")
        return None
    if code.startswith("6"):
        prefix = 0x11  # 上海
    elif code.startswith(("0", "3")):
        prefix = 0x21  # 深圳
    elif code.startswith("92"):
        prefix = 0x97  # 北交
    else:
        print(f"  tdx_to_ths: {code} -> [过滤]")
        return None
    result = bytes([prefix]) + code.encode("gbk") + b"\x00"
    print(f"  tdx_to_ths: {code} -> {result} | hex: {code.encode()} -> {result.hex()}")
    return result

# ==================== 日志 ====================
def log(direction: str, msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] [{direction}] {msg}")

# ==================== 共享状态 ====================
class SharedState:
    ths_broadcast_code = ""   # THS刚广播到TDX的代码，用来阻止TDX->THS回传
    lock = threading.Lock()

def find_ths_window() -> int:
    hwnds = []
    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "同花顺" in win32gui.GetWindowText(hwnd):
            hwnds.append(hwnd)
        return True
    win32gui.EnumWindows(callback, None)
    return hwnds[0] if hwnds else 0

def send_to_ths(code: str):
    hwnd = find_ths_window()
    if not hwnd:
        raise RuntimeError("未找到同花顺窗口")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not handle:
        raise RuntimeError("OpenProcess失败")

    payload = tdx_to_ths(code)
    if payload is None:
        return
    addr = kernel32.VirtualAllocEx(handle, None, len(payload), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not addr:
        kernel32.CloseHandle(handle)
        raise RuntimeError("VirtualAllocEx失败")

    if not kernel32.WriteProcessMemory(handle, addr, payload, len(payload), None):
        kernel32.VirtualFreeEx(handle, addr, 0, MEM_RELEASE)
        kernel32.CloseHandle(handle)
        raise RuntimeError("WriteProcessMemory失败")

    user32.SendMessageW(hwnd, THS_STOCK_MSG, 0, addr)
    time.sleep(FREE_DELAY_SEC)
    kernel32.VirtualFreeEx(handle, addr, 0, MEM_RELEASE)
    kernel32.CloseHandle(handle)

def broadcast_to_tdx(code: str):
    """向通达信广播THS的代码"""
    tdx = ths_to_tdx(code)
    if tdx is None:
        return
    msg = user32.RegisterWindowMessageW("stock")
    user32.PostMessageW(0xFFFF, msg, int(tdx), 0)

# ==================== 进程初始化 ====================
def init_processes(tdx_cfg: TDXConfig, ths_cfg: THSConfig):
    pm_tdx = pymem.Pymem(tdx_cfg.process)
    mod_tdx = pymem.process.module_from_name(pm_tdx.process_handle, tdx_cfg.dll)
    addr_tdx = mod_tdx.lpBaseOfDll + tdx_cfg.offset
    log("SYS", f"TDX addr=0x{addr_tdx:X}")

    pm_ths = pymem.Pymem(ths_cfg.process)
    mod_ths = pymem.process.module_from_name(pm_ths.process_handle, ths_cfg.process)
    addr_ths = mod_ths.lpBaseOfDll + ths_cfg.base_offset
    log("SYS", f"THS addr=0x{addr_ths:X}")

    return pm_tdx, addr_tdx, pm_ths, addr_ths

# ==================== 线程：TDX -> THS ====================
def tdx_to_ths_loop(enabled: threading.Event, tdx_cfg: TDXConfig, ths_cfg: THSConfig,
                    pm_tdx, addr_tdx, pm_ths, addr_ths, state: SharedState):
    last_code = ""
    while True:
        try:
            if enabled.is_set():
                code = pm_tdx.read_string(addr_tdx, 6)
                if code.isdigit() and code != last_code:
                    skip = False
                    with state.lock:
                        if code == state.ths_broadcast_code:
                            log("TDX->THS", f"来自THS广播，跳过")
                            state.ths_broadcast_code = ""
                            skip = True
                    if not skip:
                        ptr = pm_ths.read_uint(addr_ths)
                        ths_code = pm_ths.read_string(ptr + ths_cfg.pointer_offset, 6)
                        if ths_code and ths_code.isdigit() and ths_code == code:
                            log("TDX->THS", f"THS已显示{code}，跳过")
                        else:
                            log("TDX->THS", f"{last_code} -> {code}")
                            send_to_ths(code)
                    last_code = code
        except RuntimeError as e:
            if "未找到同花顺窗口" in str(e):
                log("TDX->THS", "等待同花顺窗口...")
                time.sleep(1)
                continue
            log("TDX->THS", str(e))
        except Exception as e:
            log("TDX->THS", str(e))
        time.sleep(0.01)

# ==================== 线程：THS -> TDX ====================
def ths_to_tdx_loop(enabled: threading.Event, tdx_cfg: TDXConfig, ths_cfg: THSConfig,
                    pm_tdx, addr_tdx, pm_ths, addr_ths, state: SharedState):
    last_code = ""
    while True:
        try:
            if enabled.is_set():
                ptr = pm_ths.read_uint(addr_ths)
                code = pm_ths.read_string(ptr + ths_cfg.pointer_offset, 7)
                if code and code != last_code:
                    tdx_code = pm_tdx.read_string(addr_tdx, 6)
                    if tdx_code and tdx_code == code[:6]:
                        log("THS->TDX", f"TDX已显示{tdx_code}，跳过")
                    else:
                        log("THS->TDX", f"{last_code} -> {code}")
                        broadcast_to_tdx(code)
                        # 标记刚广播的代码，防止TDX->THS回传
                        with state.lock:
                            state.ths_broadcast_code = code[:6]
                    last_code = code
        except Exception as e:
            log("THS->TDX", str(e))
        time.sleep(ths_cfg.interval)

# ==================== 主程序 ====================
def main():
    tdx_cfg = TDXConfig()
    ths_cfg = THSConfig()

    tdx_enabled = threading.Event()
    ths_enabled = threading.Event()
    tdx_enabled.set()
    ths_enabled.set()

    state = SharedState()
    pm_tdx, addr_tdx, pm_ths, addr_ths = init_processes(tdx_cfg, ths_cfg)

    threading.Thread(target=tdx_to_ths_loop, args=(
        tdx_enabled, tdx_cfg, ths_cfg, pm_tdx, addr_tdx, pm_ths, addr_ths, state), daemon=True).start()
    threading.Thread(target=ths_to_tdx_loop, args=(
        ths_enabled, tdx_cfg, ths_cfg, pm_tdx, addr_tdx, pm_ths, addr_ths, state), daemon=True).start()

    log("SYS", "启动完成 | enable tdx/disable tdx | enable ths/disable ths | status | exit")
    while True:
        cmd = input("> ").strip().lower()
        if cmd == "exit":
            break
        elif cmd == "status":
            log("SYS", f"TDX->THS: {'ON' if tdx_enabled.is_set() else 'OFF'}")
            log("SYS", f"THS->TDX: {'ON' if ths_enabled.is_set() else 'OFF'}")
        elif cmd == "enable tdx":
            tdx_enabled.set()
            log("SYS", "TDX->THS: ON")
        elif cmd == "disable tdx":
            tdx_enabled.clear()
            log("SYS", "TDX->THS: OFF")
        elif cmd == "enable ths":
            ths_enabled.set()
            log("SYS", "THS->TDX: ON")
        elif cmd == "disable ths":
            ths_enabled.clear()
            log("SYS", "THS->TDX: OFF")
        else:
            log("SYS", "未知命令")

if __name__ == "__main__":
    main()
