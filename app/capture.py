# -*- coding: utf-8 -*-
"""窗口枚举 + 截帧 + 画面质量闸门。

截帧优先走 Windows Graphics Capture（只截目标窗口，不会把自己的浮层拍进去）；
不可用时退回 mss 全屏截图再裁窗口矩形（调用方需先隐藏浮层）。
"""

import ctypes
import threading
import time
from ctypes import wintypes

from PIL import Image, ImageFilter, ImageStat

# 帧签名只保留一份实现（在 signals 里）。原来 capture 和 signals 各有一份一模一样的，
# 同一张图每 tick 要算两次 32×20 缩略，纯浪费。
from signals import signature

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WGC_OK = False
try:
    from windows_capture import WindowsCapture

    WGC_OK = True
except Exception:
    WindowsCapture = None

MSS_OK = False
try:
    import mss

    MSS_OK = True
except Exception:
    mss = None

SKIP_TITLES = {
    "Program Manager", "Windows 输入体验", "Microsoft Text Input Application",
    "设置", "任务栏", "开始",
}


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def list_windows():
    """返回可见、有标题的顶层窗口 [(hwnd, title, rect)]。"""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.IsIconic(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value.strip()
            if not title or title in SKIP_TITLES:
                return True
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            w, h = r.right - r.left, r.bottom - r.top
            if w < 320 or h < 200:
                return True
            if w >= 32767 or h >= 32767:
                return True
            out.append((int(hwnd), title, (r.left, r.top, w, h)))
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(title_part):
    """按标题关键字找窗口，返回 (hwnd, title, rect) 或 None。"""
    if not title_part:
        return None
    low = title_part.lower()
    wins = list_windows()
    for hwnd, title, rect in wins:
        if title.lower() == low:
            return hwnd, title, rect
    for hwnd, title, rect in wins:
        if low in title.lower():
            return hwnd, title, rect
    return None


def window_rect(hwnd):
    r = RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return None


def is_foreground(hwnd):
    """目标窗口是不是正在前台。切出去看网页时就没必要截了。"""
    try:
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        if fg == hwnd:
            return True
        root = user32.GetAncestor(fg, 2)
        return root == hwnd
    except Exception:
        return True


def _buf_to_image(frame):
    """windows_capture 的 frame_buffer 是 BGRA ndarray。"""
    import numpy as np

    arr = frame
    if hasattr(frame, "frame_buffer"):
        arr = frame.frame_buffer
    a = np.asarray(arr)
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError("unexpected frame shape: %r" % (a.shape,))
    rgb = a[:, :, [2, 1, 0]]
    return Image.fromarray(rgb)


def grab_wgc(hwnd, title, timeout=2.5):
    """用 WGC 抓一帧。返回 (PIL.Image | None, err)。"""
    if not WGC_OK:
        return None, "windows-capture 未安装"

    box = {}
    done = threading.Event()

    cap = None
    last_err = None
    for kwargs in (
        {"window_handle": hwnd, "cursor_capture": False, "draw_border": False},
        {"window_name": title, "cursor_capture": False, "draw_border": False},
        {"cursor_capture": False, "draw_border": False, "window_name": title},
    ):
        try:
            cap = WindowsCapture(**kwargs)
            break
        except TypeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    if cap is None:
        return None, "WGC 初始化失败: %s" % last_err

    @cap.event
    def on_frame_arrived(frame, control):
        try:
            box["img"] = _buf_to_image(frame)
        except Exception as e:
            box["err"] = str(e)
        try:
            control.stop()
        except Exception:
            pass
        done.set()

    @cap.event
    def on_closed():
        box["closed"] = True
        done.set()

    t = threading.Thread(target=cap.start, daemon=True)
    t.start()
    got = done.wait(timeout)
    if not got:
        return None, "WGC 超时（游戏可能是独占全屏，请改成无边框窗口）"
    if box.get("img") is None:
        return None, box.get("err") or "WGC 未产出帧"
    return box["img"], ""


def grab_mss(hwnd):
    """退路：全屏截图后裁窗口矩形。注意会把自己的浮层拍进去。"""
    if not MSS_OK:
        return None, "mss 未安装"
    rect = window_rect(hwnd)
    if not rect:
        return None, "拿不到窗口矩形"
    x, y, w, h = rect
    with mss.mss() as sct:
        shot = sct.grab({"left": max(0, x), "top": max(0, y), "width": max(1, w), "height": max(1, h)})
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return img, ""


def grab_region(region):
    """直接截屏幕上的一块矩形。region = {left, top, width, height}"""
    if not MSS_OK:
        return None, "mss 未安装"
    try:
        left = int(region.get("left", 0))
        top = int(region.get("top", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w < 32 or h < 32:
            return None, "选区太小"
        with mss.mss() as sct:
            shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img, ""
    except Exception as e:
        return None, "截区失败: %s" % e


def grab_fullscreen():
    """框选时用的整屏快照（不含自己的浮层）。"""
    if not MSS_OK:
        return None
    try:
        with mss.mss() as sct:
            m = sct.monitors[1]
            shot = sct.grab(m)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        return None


def point_is_foreground(x, y):
    """屏幕上这个点，露出来的是不是前台窗口。用来判断玩家是不是真的在看那块区域。"""
    try:
        class _P(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        pt = _P(int(x), int(y))
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return False
        root = user32.GetAncestor(hwnd, 2)
        return root == user32.GetForegroundWindow() or hwnd == user32.GetForegroundWindow()
    except Exception:
        return True


def grab(hwnd, title):
    if WGC_OK:
        img, err = grab_wgc(hwnd, title)
        if img is not None:
            return img, ""
        if MSS_OK:
            img2, err2 = grab_mss(hwnd)
            if img2 is not None:
                return img2, err
        return None, err
    if MSS_OK:
        return grab_mss(hwnd)
    return None, "没有可用的截帧后端"


def grab_current(cfg):
    """按配置抓一帧，返回 (img, err, label)。设置面板实测也走这里。"""
    c = cfg.get("capture") or {}
    region = c.get("region") or {}
    if c.get("mode", "region") == "region" and int(region.get("width", 0) or 0) >= 32:
        img, err = grab_region(region)
        return img, err, "选区 %d×%d" % (int(region.get("width", 0)), int(region.get("height", 0)))
    title = c.get("window_title", "")
    found = find_window(title)
    if not found:
        return None, "没找到窗口", title or "窗口"
    hwnd, real_title, _ = found
    img, err = grab(hwnd, real_title)
    return img, err, real_title


def prepare(img, max_side, quality, scale=1.0):
    """送到模型前的预处理：按比例缩小 → 限长边 → JPEG。

    scale 是相对截图原尺寸的比例（1.0 = 原样）。抓屏时拿不到"指定分辨率"，
    只能先抓全尺寸再缩；这几毫秒在截图同一个动作里，感知不到。
    """
    import io

    im = img.convert("RGB")
    w, h = im.size

    s = float(scale or 1.0)
    if 0 < s < 0.999:
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        im = im.resize((nw, nh), Image.LANCZOS)
        w, h = nw, nh

    if max_side and max(w, h) > max_side:
        k = max_side / float(max(w, h))
        im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=int(quality), optimize=True)
    return buf.getvalue(), im.size


def frame_gate(img, cfg, last_sig, sig=None):
    """画面值不值得看。返回 (ok, reason, sig)。

    sig 可以由调用方先算好传进来（和信号层共用一次计算）。
    """
    c = cfg.get("capture", {})
    try:
        g = img.convert("L")
        if min(g.size) > 720:
            g2 = g.copy()
            g2.thumbnail((720, 720), Image.BILINEAR)
        else:
            g2 = g
        mean = ImageStat.Stat(g2).mean[0]
        if mean < float(c.get("min_mean", 14)):
            return False, "黑屏/过暗（均值 %.1f）" % mean, None

        edges = g2.filter(ImageFilter.FIND_EDGES)
        sharp = ImageStat.Stat(edges).stddev[0]
        if sharp < float(c.get("blur_threshold", 9.0)):
            return False, "画面模糊（边缘 %.1f）" % sharp, None

        sig = sig or signature(img)
        if last_sig:
            n = min(len(sig), len(last_sig))
            diff = sum(abs(sig[i] - last_sig[i]) for i in range(n)) / float(n)
            if diff < float(c.get("dup_threshold", 2.2)):
                return False, "和上次看的画面几乎一样（差 %.2f）" % diff, sig
        return True, "", sig
    except Exception as e:
        return True, "闸门异常: %s" % e, None
