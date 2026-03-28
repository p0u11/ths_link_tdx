# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
import time
import pymem
import win32gui
import win32process

# --- 通达信内存配置 ---
TDX_PROCESS = "TdxW.exe"
TDX_DLL = "Viewthem.dll"
TDX_OFFSET = 0x160A64



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



MARKET_MAP = [
    ["sh", 0x11],  # 上海
    ["sz", 0x21],  # 深圳
    ["hk", 0xb1],  # 香港
    ["bj", 0x97]   # 北交
]

def tdx_to_ths_payload(tdx_code):
    """
    输入: '600821' (通达信通用代码)
    输出: b'\x11600821\x00' (同花顺注入字节流)
    """
    if not tdx_code: return None
    
    code_len = len(tdx_code)
    target_sid = None

    # --- 1. 查表判定过程 ---
    if code_len == 5:
        target_sid = "hk"
    elif code_len == 6:
        if tdx_code.startswith('6'):
            target_sid = "sh"
        elif tdx_code.startswith(('0', '3')):
            target_sid = "sz"
        else:
            target_sid = "bj"

    if not target_sid: return None

    # --- 2. 提取同花顺前缀 ---
    prefix_val = 0x11 # 默认兜底
    for sid, ths_hex in MARKET_MAP:
        if sid == target_sid:
            prefix_val = ths_hex
            break
            
    # --- 3. 构造最终字节流 ---
    return bytes([prefix_val]) + tdx_code.encode('gbk') + b"\x00"

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

def send_ths_jump(raw_code: str) -> None:
    hwnd = _find_ths_window()
    if not hwnd:
        raise RuntimeError("未找到同花顺窗口")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not handle:
        raise RuntimeError("OpenProcess 失败")

    payload = tdx_to_ths_payload(raw_code)
    print(f"Hex: {payload.hex(' ')} | Char: {payload}")
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

# --- 主循环：通达信内存监控 ---
def start_sync():
    print(f"--- 启动联动: 通达信({TDX_OFFSET}) -> 同花顺(注入法) ---")
    try:
        pm = pymem.Pymem(TDX_PROCESS)
        module = pymem.process.module_from_name(pm.process_handle, TDX_DLL)
        final_addr = module.lpBaseOfDll + TDX_OFFSET
        
        last_code = ""
        while True:
            try:
                # 从通达信静默读取 6 位代码
                current_code = pm.read_string(final_addr, 6)
                if current_code.isdigit() and current_code != last_code:
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"[{timestamp}] 通达信变动:{last_code}=>{current_code}")
                    
                    # 执行昨天那个牛逼的跳转代码
                    send_ths_jump(current_code)
                    
                    last_code = current_code
            except Exception as e:
                 print(f"运行出错: {e}")
            time.sleep(0.5)
            
    except Exception as e:
        print(f"运行出错: {e}")

if __name__ == "__main__":
    start_sync()