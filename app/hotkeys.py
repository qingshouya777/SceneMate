# -*- coding: utf-8 -*-
"""全局热键与鼠标侧键。设置面板捕获按键时也用这里的转换函数。"""

import ctypes
import threading

MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN = 1, 2, 4, 8

VK_NAMES = {
    "SPACE": 0x20, "TAB": 0x09, "ESCAPE": 0x1B, "RETURN": 0x0D, "BACKSPACE": 0x08,
    "HOME": 0x24, "END": 0x23, "PRIOR": 0x21, "NEXT": 0x22,
    "INSERT": 0x2D, "DELETE": 0x2E, "PAUSE": 0x13,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "MULTIPLY": 0x6A, "ADD": 0x6B, "SUBTRACT": 0x6D, "DECIMAL": 0x6E, "DIVIDE": 0x6F,
    "OEM_3": 0xC0, "OEM_MINUS": 0xBD, "OEM_PLUS": 0xBB,
    "OEM_4": 0xDB, "OEM_6": 0xDD, "OEM_5": 0xDC, "OEM_1": 0xBA,
    "OEM_COMMA": 0xBC, "OEM_PERIOD": 0xBE, "OEM_2": 0xBF, "OEM_7": 0xDE,
}
MODIFIER_KEYS = {"shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r",
                 "win_l", "win_r", "super_l", "super_r", "caps_lock", "num_lock"}


def key_to_hotkey(ev):
    """把 tk 的 KeyPress 事件转成热键字符串；只按修饰键时返回 None。"""
    ks = (ev.keysym or "")
    if ks.lower() in MODIFIER_KEYS or not ks:
        return None
    st = int(getattr(ev, "state", 0))
    mods = []
    if st & 0x4:
        mods.append("CTRL")
    if st & 0x1:
        mods.append("SHIFT")
    if (st & 0x8) or (st & 0x20000):
        mods.append("ALT")
    name = ks.upper()
    if len(name) > 1 and name.startswith("F") and name[1:].isdigit():
        pass
    elif len(name) == 1:
        pass
    elif name in VK_NAMES:
        pass
    else:
        return None
    return "+".join(mods + [name])


def hotkey_display(s):
    if not s:
        return "未设置"
    parts = [x.strip() for x in s.split("+") if x.strip()]
    out = []
    for i, p in enumerate(parts):
        u = p.upper()
        if i < len(parts) - 1:
            out.append({"CTRL": "Ctrl", "CONTROL": "Ctrl", "ALT": "Alt",
                        "SHIFT": "Shift", "WIN": "Win"}.get(u, u.title()))
        else:
            out.append({"MOUSE4": "鼠标侧1", "MOUSE5": "鼠标侧2",
                        "SPACE": "空格", "TAB": "Tab", "ESCAPE": "Esc",
                        "RETURN": "Enter", "BACKSPACE": "退格", "PRIOR": "PgUp",
                        "NEXT": "PgDn", "INSERT": "Ins", "DELETE": "Del",
                        "LEFT": "←", "UP": "↑", "RIGHT": "→", "DOWN": "↓"}.get(u, u))
    return "+".join(out)


def parse_hotkey(s):
    s = (s or "").strip().upper()
    if not s:
        return None
    mods = 0
    parts = [p.strip() for p in s.split("+") if p.strip()]
    key = parts[-1]
    for p in parts[:-1]:
        if p in ("CTRL", "CONTROL"):
            mods |= MOD_CTRL
        elif p == "ALT":
            mods |= MOD_ALT
        elif p == "SHIFT":
            mods |= MOD_SHIFT
        elif p in ("WIN", "META"):
            mods |= MOD_WIN
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in VK_NAMES:
        vk = VK_NAMES[key]
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key.upper())
    else:
        return None
    return mods, vk


class HotkeyThread(threading.Thread):
    """在独立线程里 RegisterHotKey + GetMessage，收到就回调。"""

    def __init__(self, mapping, on_hit):
        super().__init__(daemon=True)
        self.mapping = mapping
        self.on_hit = on_hit
        self.ok = {}
        self._stop = False

    def run(self):
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        for i, (name, s) in enumerate(self.mapping.items()):
            hk = parse_hotkey(s)
            if not hk:
                continue
            mods, vk = hk
            hid = 0xA000 + i
            if user32.RegisterHotKey(None, hid, mods | 0x4000, vk):
                self.ok[name] = hid
        msg = wintypes.MSG()
        while not self._stop:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got == 0 or got == -1:
                break
            if msg.message == 0x0312:
                for name, hid in self.ok.items():
                    if hid == int(msg.wParam):
                        try:
                            self.on_hit(name)
                        except Exception:
                            pass
                        break

    def stop(self):
        self._stop = True
        try:
            ctypes.windll.user32.PostThreadMessageW(self.ident, 0x0012, 0, 0)
        except Exception:
            pass


class MouseSideButtons(threading.Thread):
    """监听鼠标侧键（XBUTTON1/2）。RegisterHotKey 不接受鼠标键，只能用低级钩子。"""

    WM_XBUTTONDOWN = 0x020B
    WH_MOUSE_LL = 14

    def __init__(self, on_button):
        super().__init__(daemon=True)
        self.on_button = on_button
        self.ready = threading.Event()
        self._stop = False
        self._hook = None
        self._proc = None

    def run(self):
        from ctypes import wintypes

        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        names = {1: "MOUSE4", 2: "MOUSE5"}

        def proc(nCode, wParam, lParam):
            if nCode == 0 and wParam == self.WM_XBUTTONDOWN:
                try:
                    data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    name = names.get((int(data.mouseData) >> 16) & 0xFFFF)
                    if name:
                        self.on_button(name)
                except Exception:
                    pass
            return u.CallNextHookEx(None, nCode, wParam, lParam)

        PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM)
        cb = PROC(proc)
        self._proc = cb
        self._hook = u.SetWindowsHookExW(self.WH_MOUSE_LL,
                                         ctypes.cast(cb, ctypes.c_void_p), None, 0)
        self.err = ctypes.get_last_error()
        self.ready.set()
        if not self._hook:
            return

        msg = wintypes.MSG()
        while not self._stop:
            r = u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        try:
            u.UnhookWindowsHookEx(ctypes.c_void_p(self._hook))
        except Exception:
            pass
        self._hook = None

    def stop(self):
        self._stop = True
        try:
            ctypes.windll.user32.PostThreadMessageW(self.ident, 0x0012, 0, 0)
        except Exception:
            pass
