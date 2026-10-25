# -*- coding: utf-8 -*-
"""浮层：置顶、透明、点击穿透的字幕窗口 + 一个可拖动的小控制条。

字幕用 PIL 画成图片再贴到无边框窗口上——这样圆角、行距、中文字体全部可控，
不会出现系统控件那种方框和豆腐块。
"""

import ctypes
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageTk

import branding

KEY = "#010203"
BG = (10, 9, 15, 255)
NAME_C = (232, 170, 56, 255)
TEXT_C = (255, 248, 236, 255)
THINK_C = (255, 245, 226, 110)
PINK = (255, 150, 190, 255)      # 好感提示用的粉（偏柔，别太荧光）
# 只有正面情绪显示在字幕上：开心暖黄、害羞粉、感动暖橙。难过/生气/害怕不显示。
MOOD_C = {"开心": (245, 200, 107, 255),
          "害羞": (255, 158, 196, 255),
          "感动": (255, 205, 150, 255)}
PINK_DIM = (255, 122, 176, 150)
WIN_ALPHA = 0.82

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

_FONT_CACHE = {}
_FONT_PATHS = [
    (r"C:\Windows\Fonts\msyh.ttc", 0),
    (r"C:\Windows\Fonts\msyhbd.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", 0),
    (r"C:\Windows\Fonts\simsun.ttc", 0),
]


def pick_font_path():
    import os

    for p, idx in _FONT_PATHS:
        if os.path.exists(p):
            return p, idx
    return None, 0


FONT_PATH, FONT_INDEX = pick_font_path()


def font(size, bold=False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    size = max(9, int(size))
    if FONT_PATH:
        try:
            order = [FONT_PATH]
            if bold and FONT_PATH.endswith("msyh.ttc") and not FONT_PATH.endswith("msyhbd.ttc"):
                alt = r"C:\Windows\Fonts\msyhbd.ttc"
                import os
                if os.path.exists(alt):
                    order.insert(0, alt)
            f = ImageFont.truetype(order[0], size, index=FONT_INDEX)
            _FONT_CACHE[key] = f
            return f
        except Exception:
            pass
    try:
        _FONT_CACHE[key] = ImageFont.load_default()
    except Exception:
        _FONT_CACHE[key] = ImageFont.load_default(size)
    return _FONT_CACHE[key]


def wrap_cjk(text, fnt, max_w):
    lines, cur = [], ""
    for ch in text.replace("\r", ""):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if fnt.getlength(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur or not lines:
        lines.append(cur)
    return lines


def render_subtitle(text, name, cfg, screen_w, thinking=False, affection=0, mood=""):
    """把一句台词渲染成 RGBA 图（圆角外是键色，用于窗口透明）。"""
    o = cfg.get("overlay", {})
    fs = int(o.get("font_size") or 0)
    if fs <= 0:
        fs = int(max(22, min(36, round(screen_w / 62.0))))
    f_body = font(fs)
    f_name = font(max(12, int(fs * 0.52)))

    pad_x, pad_y = int(fs * 0.62), int(fs * 0.46)
    gap = int(fs * 0.30)
    max_w = int(screen_w * float(o.get("max_width_pct", 54)) / 100.0) - pad_x * 2

    show_name = bool(o.get("show_name", True)) and bool(name)
    if thinking:
        body_lines = ["·  ·  ·"]
    else:
        body_lines = wrap_cjk("“" + text + "”" if text else "", f_body, max_w)

    asc, desc = f_body.getmetrics()
    line_h = int((asc + desc) * 1.28)
    name_h = (int(f_name.getmetrics()[0] + f_name.getmetrics()[1]) + gap) if show_name else 0
    # 好感提示：一行粉字，贴在正文下面靠右。0 = 这一格没有好感变化。
    aff = ("好感 +%d" % int(affection)) if affection and affection > 0 else ""
    mood_txt = ("（%s）" % mood) if mood in MOOD_C else ""
    aff_h = int(line_h * 0.82) if (aff or mood_txt) else 0

    text_w = 0
    for ln in body_lines:
        text_w = max(text_w, f_body.getlength(ln))
    if show_name:
        text_w = max(text_w, f_name.getlength(name))
    if thinking:
        text_w = max(text_w, fs * 5.0)
    f_aff = font(max(12, int(fs * 0.56)))
    if aff and mood_txt:
        # 两个标签同在一行：左边情绪、右边好感。只取较大的一个会把它们叠在一起，
        # 短台词时尤其明显——宽度要按两个标签之和算。
        text_w = max(text_w, f_aff.getlength(aff) + f_aff.getlength(mood_txt) + fs * 0.6)
    elif aff:
        text_w = max(text_w, f_aff.getlength(aff))
    elif mood_txt:
        text_w = max(text_w, f_aff.getlength(mood_txt))

    W = int(min(screen_w * 0.96, text_w + pad_x * 2))
    H = int(pad_y * 2 + name_h + line_h * len(body_lines) + aff_h)
    W, H = max(W, int(fs * 4)), max(H, int(fs * 2))

    S = 4
    bg = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    db = ImageDraw.Draw(bg)
    r = int(fs * 0.42) * S
    db.rounded_rectangle([0, 0, W * S - 1, H * S - 1], radius=r, fill=BG)

    txt = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    f_body_big = font(fs * S)
    f_name_big = font(max(12, int(fs * 0.52)) * S)

    y = pad_y * S
    if show_name:
        d.text((pad_x * S, y), name, font=f_name_big, fill=NAME_C)
        y += name_h * S

    for ln in body_lines:
        d.text((pad_x * S, y), ln, font=f_body_big, fill=THINK_C if thinking else TEXT_C)
        y += line_h * S
    if aff or mood_txt:
        f_aff_big = font(max(12, int(fs * 0.56)) * S)
        if mood_txt:
            d.text((pad_x * S, y), mood_txt, font=f_aff_big, fill=MOOD_C[mood])
        if aff:
            aw = f_aff_big.getlength(aff)
            d.text((W * S - pad_x * S - aw, y), aff, font=f_aff_big, fill=PINK)

    small = Image.alpha_composite(bg, txt).resize((W, H), Image.LANCZOS)

    out = Image.new("RGB", (W, H), KEY)
    mask = small.split()[3].point(lambda v: 255 if v >= 96 else 0)
    out.paste(small.convert("RGB"), (0, 0), mask)
    return out


def make_click_through(win):
    try:
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id())
        if not hwnd:
            hwnd = win.winfo_id()
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = get_long(hwnd, GWL_EXSTYLE)
        set_long(hwnd, GWL_EXSTYLE,
                 style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
    except Exception:
        pass


class SubtitleOverlay:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except Exception:
            pass
        self.label = tk.Label(self.win, bd=0, bg=KEY, highlightthickness=0)
        self.label.pack()
        self._img = None
        self._after = []
        self._visible = False
        self._hwnd = None

    def _screen(self):
        return self.win.winfo_screenwidth(), self.win.winfo_screenheight()

    def _cancel(self):
        for a in self._after:
            try:
                self.win.after_cancel(a)
            except Exception:
                pass
        self._after = []

    def _place(self, W, H):
        o = self.cfg.get("overlay", {})
        sw, sh = self._screen()
        x = int(sw * float(o.get("x_pct", 25)) / 100.0)
        y = int(sh * float(o.get("y_pct", 66)) / 100.0)
        x = max(0, min(x, sw - W))
        y = max(0, min(y, sh - H))
        self.win.geometry("%dx%d+%d+%d" % (W, H, x, y))

    def show(self, text, name, thinking=False, affection=0, mood=""):
        self._cancel()
        sw, _ = self._screen()
        img = render_subtitle(text, name, self.cfg, sw, thinking=thinking,
                              affection=affection, mood=mood)
        self._img = ImageTk.PhotoImage(img)
        self.label.configure(image=self._img)
        self._place(img.width, img.height)
        try:
            self.win.attributes("-alpha", WIN_ALPHA)
        except Exception:
            pass
        self.win.deiconify()
        self.win.lift()
        force_topmost(self.win)
        if self._hwnd is None:
            make_click_through(self.win)
            self._hwnd = True
        self._visible = True

    def set_clickable(self, on):
        """临时解除点击穿透，让字幕能被鼠标拖动。"""
        try:
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = get_long(hwnd, GWL_EXSTYLE)
            if on:
                style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
            else:
                style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            set_long(hwnd, GWL_EXSTYLE, style)
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    def begin_drag(self, text, name, on_done):
        """显示一句样本，让用户把它拖到想去的位置；松手回调百分比坐标。"""
        self._drag_done = on_done
        self.speak(text, name, 120)
        self.root.after(120, lambda: self.set_clickable(True))
        self.root.after(140, self._bind_drag)

    def _bind_drag(self):
        for w in (self.win, self.label):
            w.configure(cursor="fleur")
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

    def _drag_start(self, e):
        self._dx = e.x_root - self.win.winfo_x()
        self._dy = e.y_root - self.win.winfo_y()

    def _drag_move(self, e):
        self.win.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))

    def _drag_end(self, e):
        try:
            for w in (self.win, self.label):
                w.unbind("<Button-1>")
                w.unbind("<B1-Motion>")
                w.unbind("<ButtonRelease-1>")
                w.configure(cursor="")
        except Exception:
            pass
        self.set_clickable(False)
        x, y = self.win.winfo_x(), self.win.winfo_y()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        cb = getattr(self, "_drag_done", None)
        self._drag_done = None
        self.clear()
        if cb:
            cb(round(x * 100.0 / max(1, sw), 1), round(y * 100.0 / max(1, sh), 1))

    def show_thinking(self, name):
        self.show("……", name, thinking=True)

    def fade_out(self, ms=420, steps=8):
        def step(i):
            a = max(0.0, WIN_ALPHA * (1.0 - i / float(steps)))
            try:
                self.win.attributes("-alpha", a)
            except Exception:
                pass
            if i >= steps:
                self.hide()
            else:
                self._after.append(self.win.after(int(ms / steps), lambda: step(i + 1)))
        step(0)

    def hide(self):
        try:
            self.win.withdraw()
        except Exception:
            pass
        self._visible = False

    def clear(self):
        self._cancel()
        self.hide()

    def speak(self, text, name, duration=5.0, affection=0, mood=""):
        self.show(text, name, affection=affection, mood=mood)
        self._after.append(self.win.after(int(duration * 1000), self.fade_out))

    def stream_update(self, text, name, affection=0, mood=""):
        """流式更新：只在内容变化时重画，不做重复的置顶/点击穿透。"""
        sw, _ = self._screen()
        img = render_subtitle(text, name, self.cfg, sw, thinking=False,
                              affection=affection, mood=mood)
        self._img = ImageTk.PhotoImage(img)
        self.label.configure(image=self._img)
        self._place(img.width, img.height)
        if not self._visible:
            try:
                self.win.attributes("-alpha", WIN_ALPHA)
            except Exception:
                pass
            self.win.deiconify()
            self.win.lift()
            force_topmost(self.win)
            if self._hwnd is None:
                make_click_through(self.win)
                self._hwnd = True
            self._visible = True

    def stream_finish(self, text, name, duration=0.0, affection=0, mood=""):
        if text:
            self.stream_update(text, name, affection=affection, mood=mood)
        if duration and duration > 0:
            self._after.append(self.win.after(int(duration * 1000), self.fade_out))
        else:
            self._after.append(self.win.after(0, self.fade_out))


class ControlBar:
    """右上角的小控制条：普通窗口，可拖、可最小化到后台。"""

    def __init__(self, root, cfg, on_shoot, on_toggle, on_settings, on_quit,
                 on_pick=None, on_prompts=None):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.title(branding.APP_NAME)
        self.win.resizable(False, False)
        self.win.configure(bg="#15141C")
        # 右上角 X = 退出；最小化交给系统的最小化按钮。
        # 两件事分开，别让用户点 X 和点最小化得到同一个结果。
        self.win.protocol("WM_DELETE_WINDOW", on_quit)
        self.state_var = tk.StringVar(value="○ 暂停")
        self.auto_on = False

        wrap = tk.Frame(self.win, bg="#15141C", padx=10, pady=6)
        wrap.pack()
        self.dot = tk.Label(wrap, textvariable=self.state_var, fg="#C9C4D4", bg="#15141C",
                            font=(FONT_NAME(), 10))
        self.dot.pack(side="left", padx=(0, 10))

        items = []
        if on_pick:
            items.append(("框选", on_pick))
        items.append(("抓拍", on_shoot))
        if on_prompts:
            items.append(("提示词", on_prompts))
        items += [("设置", on_settings), ("退出", on_quit)]
        for text, cmd in items:
            lb = tk.Label(wrap, text=text, fg="#FFF5E2", bg="#23212C", padx=9, pady=3,
                          font=(FONT_NAME(), 10), cursor="hand2")
            lb.pack(side="left", padx=2)
            lb.bind("<Button-1>", lambda e, c=cmd: c())

        self.toggle = tk.Label(wrap, text="开始", fg="#0F0E14", bg="#E8AA38", padx=9, pady=3,
                               font=(FONT_NAME(), 10), cursor="hand2")
        self.toggle.pack(side="left", padx=(2, 0))
        self.toggle.bind("<Button-1>", lambda e: on_toggle())

        for w in (wrap, self.dot):
            w.bind("<Button-1>", self._start)
            w.bind("<B1-Motion>", self._drag)

        self._dragged = False
        self.win.update_idletasks()
        self.snap_right()

    def snap_right(self):
        if self._dragged:
            return
        self.win.update_idletasks()
        w = max(200, self.win.winfo_reqwidth())
        sw = self.win.winfo_screenwidth()
        self.win.geometry("+%d+%d" % (max(0, sw - w - 12), 14))

    def _start(self, e):
        self._dragged = True
        self._ox, self._oy = e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y()

    def _drag(self, e):
        self.win.geometry("+%d+%d" % (e.x_root - self._ox, e.y_root - self._oy))

    def set_state(self, text, auto_on=None):
        self.state_var.set(text)
        if auto_on is not None:
            self.auto_on = auto_on
            self.toggle.configure(text="暂停" if auto_on else "开始",
                                  bg="#23212C" if auto_on else "#E8AA38",
                                  fg="#FFF5E2" if auto_on else "#0F0E14")
        self.snap_right()

    def set_visible(self, v):
        if v:
            self.win.deiconify()
        else:
            self.win.withdraw()


def force_topmost(win):
    """tk 的 -topmost 只是加了个样式位。从命令行/后台启动时进程没有前台权限，
    窗口会被压在其他 topmost 窗口下面——必须主动 SetWindowPos 抢一次 Z 序。"""
    try:
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    except Exception:
        pass


class RegionPicker:
    """全屏压暗 + 拖拽框选。松手即确认，Esc 取消。"""

    def __init__(self, root, on_done, on_cancel=None):
        import capture as cap

        base = cap.grab_fullscreen()
        if base is None:
            if on_cancel:
                on_cancel()
            return

        self.root = root
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.img = base
        self.W, self.H = base.size
        self.x0 = self.y0 = self.x1 = self.y1 = 0
        self.dragging = False

        dim = Image.eval(base, lambda v: int(v * 0.38))
        self._dim = ImageTk.PhotoImage(dim)

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("%dx%d+0+0" % (self.W, self.H))
        self.win.configure(cursor="crosshair")

        self.cv = tk.Canvas(self.win, width=self.W, height=self.H, bd=0,
                            highlightthickness=0, cursor="crosshair")
        self.cv.pack()
        self.cv.create_image(0, 0, anchor="nw", image=self._dim)

        fs = max(15, int(self.W / 100))
        self.cv.create_text(self.W // 2, 42,
                            text="拖一个框，只截这块区域　·　Esc 取消",
                            fill="#FFF5E2", font=("Microsoft YaHei UI", fs),
                            tags="hint")

        self.cv.bind("<Button-1>", self._down)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._up)
        self.win.bind("<Escape>", lambda e: self._cancel())
        self.win.focus_force()
        force_topmost(self.win)
        try:
            self.win.grab_set()
        except Exception:
            pass

    def _down(self, e):
        self.dragging = True
        self.x0, self.y0 = e.x, e.y
        self.x1, self.y1 = e.x, e.y
        self.cv.delete("sel")

    def _move(self, e):
        if not self.dragging:
            return
        self.x1, self.y1 = e.x, e.y
        self._draw()

    def _draw(self):
        self.cv.delete("sel")
        x0, y0 = min(self.x0, self.x1), min(self.y0, self.y1)
        x1, y1 = max(self.x0, self.x1), max(self.y0, self.y1)
        w, h = x1 - x0, y1 - y0

        if w > 2 and h > 2:
            piece = self.img.crop((x0, y0, x1, y1)).resize((w, h), Image.NEAREST)
            self._piece = ImageTk.PhotoImage(piece)
            self.cv.create_image(x0, y0, anchor="nw", image=self._piece, tags="sel")

        for (cx, cy) in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.cv.create_line(cx - 14, cy, cx + 14, cy, fill="#E8AA38", width=3, tags="sel")
            self.cv.create_line(cx, cy - 14, cx, cy + 14, fill="#E8AA38", width=3, tags="sel")

        self.cv.create_rectangle(x0, y0, x1, y1, outline="#E8AA38", width=2, tags="sel")

        label = "%d × %d" % (w, h)
        fsz = max(13, int(self.W / 130))
        lw = len(label) * fsz * 0.62 + 18
        bx, by = x0 + 10, y0 + 10
        self.cv.create_rectangle(bx, by, bx + lw, by + fsz + 14,
                                 fill="#E8AA38", outline="", tags="sel")
        self.cv.create_text(bx + lw / 2, by + (fsz + 14) / 2, text=label,
                            fill="#0F0E14", font=("Microsoft YaHei UI", fsz, "bold"),
                            tags="sel")

    def _up(self, e):
        if not self.dragging:
            return
        self.dragging = False
        x0, y0 = min(self.x0, self.x1), min(self.y0, self.y1)
        x1, y1 = max(self.x0, self.x1), max(self.y0, self.y1)
        if x1 - x0 < 40 or y1 - y0 < 40:
            return
        self._draw()
        region = {"left": int(x0), "top": int(y0), "width": int(x1 - x0), "height": int(y1 - y0)}
        self.win.after(180, lambda: self._finish(region))

    def _finish(self, region):
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_done:
            self.on_done(region)

    def _cancel(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_cancel:
            self.on_cancel()


def FONT_NAME():
    return "Microsoft YaHei UI" if FONT_PATH else "Segoe UI"
