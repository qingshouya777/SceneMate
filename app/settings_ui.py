# -*- coding: utf-8 -*-
"""设置面板：常用 / 角色 / 记忆 / 提示词 / 更多。

提示词页是一个真正的工作台：文件列表、编辑器、外部修改自动载入、
拼好的提示词预览、抓当前画面直接实测（会显示完整提示词和模型原始返回）。
"""

import base64
import copy
import io as _io
import os
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from PIL import Image

import brain
import capture
import config as cfgmod
import hotkeys
import keystore
import memory
import overlay as ov
import prompts
import providers
import router as routermod
import signals

SCALE_CHOICES = [("原尺寸", 1.0), ("75%", 0.75), ("50%", 0.5), ("35%", 0.35), ("25%", 0.25)]
SCALE_LABEL = {v: k for k, v in SCALE_CHOICES}
SCALE_VALUE = {k: v for k, v in SCALE_CHOICES}

THINK_LABELS = {"none": "关闭", "low": "低", "medium": "中", "high": "高"}
THINK_CODES = {v: k for k, v in THINK_LABELS.items()}

THINK_STYLE_LABELS = {
    "auto": "自动（按服务商）",
    "deepseek": "DeepSeek 格式",
    "standard": "标准 reasoning_effort",
    "none": "不发送",
}
THINK_STYLE_CODES = {v: k for k, v in THINK_STYLE_LABELS.items()}

# 标签按"她话多话少"来写，不按"沉默多少"来写。
# 原来 rare 叫"少沉默（多说话）"，可 35_沉默倾向 里 rare 写的是"今天话少"——正好反着。
SILENCE_LABELS = {"rare": "话少（一两句就够）", "normal": "正常", "often": "话多（愿意铺开）"}
SILENCE_CODES = {v: k for k, v in SILENCE_LABELS.items()}

DETAIL_LABELS = {"high": "高（看得细，费 token）", "low": "低（省 token）"}
DETAIL_CODES = {v: k for k, v in DETAIL_LABELS.items()}

STREAM_MODE_LABELS = {"replace": "逐句替换", "accumulate": "整段累积"}
STREAM_MODE_CODES = {v: k for k, v in STREAM_MODE_LABELS.items()}


def _set_text(widget, text):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text or "")
    widget.configure(state="disabled")


class SettingsWindow:
    BG = "#221F2B"
    FG = "#EFE9DF"
    DIM = "#9A93AC"
    ACC = "#E8AA38"
    CARD = "#171520"
    LINE = "#3A3548"

    def __init__(self, app, page="main"):
        self.app = app
        self.cfg = app.cfg
        self._F = ov.FONT_NAME()
        self._region = "unset"
        self._cap = None
        self._cap_mouse = None
        self._hot_btns = {}
        self._alive = True
        self._test_running = False
        self._compress_running = False
        self._current_prompt = None
        self._prompt_buffers = {}
        self._prompt_saved = {}
        self._prompt_mtime = {}
        self._prompt_dirty = set()
        self._test_ctx = None
        self.app.settings_win = self

        self.win = tk.Toplevel(app.root)
        self.win.title("游戏搭子 · 设置")
        self.win.configure(bg=self.BG)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w, h = 940, min(1040, sh - 46)
        self.win.geometry("%dx%d+%d+%d" % (w, h, max(10, (sw - w) // 2), max(10, (sh - h) // 2 - 24)))
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._close_win)

        self._build_bottom_bar()
        self._build_tabs()
        self._build_main_page()
        self._build_connect_page()
        self._build_persona_page()
        self._build_memory_page()
        self._build_prompts_page()
        self._build_more_page()

        self.var_mem_budget.trace_add("write", lambda *a: self._refresh_memory_page())
        self.var_ctx.trace_add("write", lambda *a: self._refresh_memory_page())
        self.var_digest_share.trace_add("write", lambda *a: self._refresh_memory_page())

        self.refresh_windows()
        self._refresh_persona_list()
        self._load_persona(self.cur_p)
        self._refresh_memory_page()
        self._select_prompt(self._prompt_keys[0])
        self.show_page(page)
        self.win.after(180, lambda: ov.force_topmost(self.win))
        self.win.after(2000, self._watch_prompts)

    # ---------- 基础控件 ----------

    def _lab(self, parent, text, width=0, color=None, size=10, bold=False,
             justify="left", wraplength=0):
        return tk.Label(parent, text=text, bg=self.BG, fg=color or self.FG,
                        font=(self._F, size, "bold") if bold else (self._F, size),
                        anchor="w", width=width, justify=justify, wraplength=wraplength)

    def _hint(self, parent, text, color=None, size=9, padx=18, pady=(1, 0)):
        lb = self._lab(parent, text, color=color or self.DIM, size=size, justify="left")
        lb.pack(anchor="w", padx=padx, pady=pady)
        return lb

    def _entry(self, parent, var, width=16, show=None):
        e = tk.Entry(parent, textvariable=var, bg=self.CARD, fg=self.FG,
                     insertbackground=self.FG, relief="flat", bd=0,
                     highlightthickness=1, highlightbackground=self.LINE,
                     highlightcolor=self.ACC, font=(self._F, 10), width=width)
        if show:
            e.configure(show=show)
        return e

    def _spin(self, parent, var, lo, hi, width=7, inc=1):
        return tk.Spinbox(parent, from_=lo, to=hi, increment=inc, textvariable=var, width=width,
                          bg=self.CARD, fg=self.FG, buttonbackground="#2C2938",
                          insertbackground=self.FG, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=self.LINE,
                          highlightcolor=self.ACC, font=(self._F, 10))

    def _combo(self, parent, var, values, width=12, readonly=True):
        return ttk.Combobox(parent, textvariable=var, values=list(values),
                            state="readonly" if readonly else "normal",
                            width=width, font=(self._F, 10))

    def _check(self, parent, var, text, cmd=None):
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd,
                              bg=self.BG, fg=self.FG, selectcolor=self.CARD,
                              activebackground=self.BG, activeforeground=self.FG,
                              font=(self._F, 10), anchor="w", bd=0,
                              highlightthickness=0)

    def _textbox(self, parent, height, value, wrap="word"):
        t = tk.Text(parent, height=height, bg=self.CARD, fg=self.FG, relief="flat", bd=0,
                    insertbackground=self.FG, padx=9, pady=7, font=(self._F, 10),
                    wrap=wrap, undo=True,
                    highlightthickness=1, highlightbackground=self.LINE,
                    highlightcolor=self.ACC)
        t.insert("1.0", value or "")
        return t

    def _button(self, parent, text, cmd, primary=False):
        return tk.Button(parent, text=text, command=cmd,
                         bg=self.ACC if primary else "#2C2938",
                         fg="#0F0E14" if primary else self.FG,
                         activebackground="#F0BC55" if primary else self.LINE,
                         activeforeground="#0F0E14" if primary else self.FG,
                         relief="flat", bd=0, padx=12, pady=5,
                         font=(self._F, 10), cursor="hand2")

    def _block(self, title, parent):
        tk.Frame(parent, bg=self.LINE, height=1).pack(fill="x", padx=18, pady=(12, 6))
        self._lab(parent, title, color=self.ACC, size=10, bold=True).pack(anchor="w", padx=18)
        f = tk.Frame(parent, bg=self.BG)
        f.pack(fill="x", padx=18, pady=(4, 2))
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)
        f._next_row = 0
        return f

    def _next_row(self, f):
        r = getattr(f, "_next_row", 0)
        f._next_row = r + 1
        return r

    def _pair(self, f, l1, w1, l2, w2):
        r = self._next_row(f)
        self._lab(f, l1, width=12).grid(row=r, column=0, sticky="w", pady=3)
        w1.grid(row=r, column=1, sticky="we", pady=3, padx=(0, 14))
        self._lab(f, l2, width=10).grid(row=r, column=2, sticky="w", pady=3)
        w2.grid(row=r, column=3, sticky="we", pady=3)

    def _line(self, f, label, widget):
        r = self._next_row(f)
        self._lab(f, label, width=12).grid(row=r, column=0, sticky="w", pady=3)
        widget.grid(row=r, column=1, columnspan=3, sticky="we", pady=3)

    def _center(self, top, w, h):
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry("%dx%d+%d+%d" % (w, h, max(10, (sw - w) // 2), max(10, (sh - h) // 2 - 20)))

    # ---------- 框架 ----------

    def _build_bottom_bar(self):
        bar = tk.Frame(self.win, bg=self.BG)
        bar.pack(fill="x", padx=18, pady=10, side="bottom")
        self._button(bar, "保存并关闭", lambda: self.save(close=True), primary=True).pack(side="right")
        self._button(bar, "保存", lambda: self.save(close=False)).pack(side="right", padx=8)
        self._button(bar, "取消", self._cancel).pack(side="right", padx=(0, 8))
        self._button(bar, "看日志", self.open_log).pack(side="left")
        self.save_status = tk.Label(bar, text="", bg=self.BG, fg=self.DIM, font=(self._F, 9))
        self.save_status.pack(side="left", padx=12)

    def _make_scroll_body(self, outer):
        """给设置页做一个带右侧滚动条的画布；返回真正放内容的 frame。"""
        canvas = tk.Canvas(outer, bg=self.BG, highlightthickness=0, bd=0)
        vs = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.configure(yscrollcommand=vs.set, yscrollincrement=20)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        def wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _build_tabs(self):
        tabbar = tk.Frame(self.win, bg=self.BG)
        tabbar.pack(fill="x", padx=18, pady=(10, 0))
        self._tabbtns = {}
        self.pages = {}
        self.page_bodies = {}
        for key, label in (("main", "常用"), ("connect", "连接"), ("persona", "角色"),
                           ("memory", "记忆"), ("prompts", "提示词"), ("more", "更多")):
            b = tk.Label(tabbar, text=label, bg="#2C2938", fg="#B9B2C4",
                         padx=16, pady=4, font=(self._F, 10), cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            b.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            self._tabbtns[key] = b
            outer = tk.Frame(self.win, bg=self.BG)
            self.pages[key] = outer
            # 常用/连接/角色内容长，统一放进可滚动区域；
            # 提示词和更多页自己已经有内部滚动，记忆页有预览区，不重复包。
            if key in ("main", "connect", "persona"):
                self.page_bodies[key] = self._make_scroll_body(outer)
            else:
                self.page_bodies[key] = outer

    def show_page(self, which):
        if which not in self.pages:
            which = "main"
        for k, f in self.pages.items():
            if k == which:
                f.pack(fill="both", expand=True)
            else:
                f.pack_forget()
        for k, b in self._tabbtns.items():
            b.configure(bg=self.ACC if k == which else "#2C2938",
                        fg="#0F0E14" if k == which else "#B9B2C4")
        if which == "memory":
            self._refresh_memory_page()

    # ---------- 常用 ----------

    def _build_main_page(self):
        p = self.page_bodies["main"]
        c = self.cfg
        # 当前角色放在最上面：打开「常用」第一眼就能看到，不用再往下滚。
        f = self._block("当前角色", p)
        r = self._next_row(f)
        self._lab(f, "正在用", width=12).grid(row=r, column=0, sticky="w", pady=3)
        self.cur_persona_label = self._lab(f, "—", color=self.ACC, size=12, bold=True)
        self.cur_persona_label.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        self.var_p_main = tk.StringVar()
        self.pcombo_main = self._combo(f, self.var_p_main, [], 16)
        self.pcombo_main.grid(row=r, column=3, sticky="e", pady=3, padx=(8, 0))
        self.pcombo_main.bind("<<ComboboxSelected>>", self._on_pick_persona)
        self._hint(p, "右边下拉可以切换角色；名字、称呼、关系、语气去「角色」页改。")

        self.var_model = tk.StringVar(value=c["vlm"].get("model", "deepseek-flash"))
        self.var_base = tk.StringVar(value=c["vlm"].get("base_url", "https://api.deepseek.com"))
        self.var_ctx = tk.StringVar(value=str(cfgmod.context_k(c)))
        self.var_think = tk.StringVar(
            value=THINK_LABELS.get((c["vlm"].get("reasoning_effort") or "none").lower(), "关闭"))

        f = self._block("模型", p)
        self.conn_label = tk.Label(f, text="", bg=self.BG, fg=self.ACC,
                                   font=(self._F, 10), anchor="w")
        self._pair(f, "接的是", self.conn_label,
                   "模型", self._entry(f, self.var_model))
        self._pair(f, "上下文 K", self._spin(f, self.var_ctx, 4, 4096),
                   "思考", self._combo(f, self.var_think, list(THINK_LABELS.values()), 10))
        self._line(f, "Base URL", self._entry(f, self.var_base))
        r = self._next_row(f)
        self._lab(f, "Key", width=12).grid(row=r, column=0, sticky="w", pady=3)
        self.key_hint = tk.Label(f, text="", bg=self.BG, fg=self.DIM, font=(self._F, 9),
                                 anchor="w", justify="left")
        self.key_hint.grid(row=r, column=1, columnspan=2, sticky="we", pady=3)
        self._button(f, "去配置", lambda: self.show_page("connect"),
                     primary=True).grid(row=r, column=3, sticky="e")
        self._update_key_hint()
        self._hint(p, "上下文以模型实际支持为准（DeepSeek/GPT-4o/Qwen 常见 128K）。"
                      "记忆预算再大，也不会超过上下文的 60%，剩下的留给提示词、图片和输出。"
                      "“思考”关着最快，也最不容易把输出预算耗在推理上；多句输出建议把「更多」页的输出上限调到 600 以上。")

        f = self._block("截哪块画面", p)
        self.var_region_info = tk.StringVar(value=self._region_text())
        r = self._next_row(f)
        self._lab(f, "当前", width=12).grid(row=r, column=0, sticky="w", pady=3)
        tk.Label(f, textvariable=self.var_region_info, bg=self.BG, fg=self.ACC,
                 font=(self._F, 10), anchor="w").grid(row=r, column=1, columnspan=2,
                                                      sticky="w", pady=3)
        self._button(f, "拖框选区域", self.pick_region, primary=True).grid(
            row=r, column=3, sticky="e", pady=3)

        r = self._next_row(f)
        self._lab(f, "或整个窗口", width=12).grid(row=r, column=0, sticky="w", pady=3)
        self.var_win = tk.StringVar(value=c["capture"].get("window_title", ""))
        self.combo = self._combo(f, self.var_win, [], 24, readonly=False)
        self.combo.grid(row=r, column=1, columnspan=2, sticky="we", pady=3, padx=(0, 8))
        bf = tk.Frame(f, bg=self.BG)
        bf.grid(row=r, column=3, sticky="e", pady=3)
        self._button(bf, "刷新", self.refresh_windows).pack(side="left", padx=(0, 4))
        self._button(bf, "用它", self.use_window).pack(side="left")

        cur_scale = float(c["capture"].get("scale", 1.0) or 1.0)
        self.var_scale = tk.StringVar(value=SCALE_LABEL.get(cur_scale, "75%"))
        scb = self._combo(f, self.var_scale, [a for a, _ in SCALE_CHOICES], 10)
        self._pair(f, "发送尺寸", scb, "", tk.Label(f, text="", bg=self.BG))
        scb.bind("<<ComboboxSelected>>", lambda e: self._update_scale_hint())
        self.scale_label = tk.Label(f, text="", bg=self.BG, fg=self.DIM,
                                    font=(self._F, 9), anchor="w", justify="left")
        self.scale_label.grid(row=self._next_row(f), column=0, columnspan=4, sticky="we", pady=(3, 0))
        self._update_scale_hint()

        f = self._block("节奏", p)
        self.var_int = tk.StringVar(value=str(c["capture"].get("interval", 5)))
        self.var_jit = tk.StringVar(value=str(c["capture"].get("jitter", 3)))
        self.var_dur = tk.StringVar(value=str(c["overlay"].get("duration", 5)))
        self.var_auto_duration = tk.BooleanVar(value=bool(c["overlay"].get("auto_duration", True)))
        self._pair(f, "间隔（秒）", self._spin(f, self.var_int, 3, 120),
                   "抖动（秒）", self._spin(f, self.var_jit, 0, 30))
        self._pair(f, "字幕（秒）", self._spin(f, self.var_dur, 1, 20),
                   "长句自动加时", self._check(f, self.var_auto_duration, "开启"))

        f = self._block("故事节奏", p)
        self.var_story_int = tk.StringVar(value=str(c["capture"].get("story_interval", 2.5)))
        self.var_story_jit = tk.StringVar(value=str(c["capture"].get("story_jitter", 0.8)))
        self.var_story_dur = tk.StringVar(value=str(c["overlay"].get("story_duration", 2.0)))
        self._pair(f, "故事间隔（秒）", self._spin(f, self.var_story_int, 1, 10, inc=0.5),
                   "故事抖动（秒）", self._spin(f, self.var_story_jit, 0, 3, inc=0.1))
        self._pair(f, "故事字幕停留（秒）", self._spin(f, self.var_story_dur, 1, 10, inc=0.5),
                   "", tk.Label(f, text="", bg=self.BG))
        self._hint(p, "故事线程（4~6 格）的中间段落用这一组节奏：默认 2.5±0.8 秒抓一帧、"
                      "字幕停留 2 秒；普通格仍用上面的间隔/字幕。\n"
                      "实际下一句还要等模型返回和打字时间，所以设置再小也不会低于“打字+停留”。")

        f = self._block("她说话的尺度", p)
        self.var_silence = tk.StringVar(
            value=SILENCE_LABELS.get(c["behavior"].get("silence", "normal"), "正常"))
        self.var_force = tk.StringVar(value=str(c["behavior"].get("force_speak_after_skips", 4)))
        self.var_max_chars = tk.StringVar(value=str(c["behavior"].get("max_line_chars", 60)))
        self.var_sentences = tk.StringVar(value=str(c["behavior"].get("sentences_per_take", 3)))
        self.var_window = tk.StringVar(
            value=str((c.get("router") or {}).get("window_ticks", 24)))
        self.var_thread_life = tk.StringVar(
            value=str((c.get("router") or {}).get("thread_life_ticks", 3)))
        self._pair(f, "话量", self._combo(f, self.var_silence, list(SILENCE_LABELS.values()), 16),
                   "一轮说几句", self._spin(f, self.var_sentences, 1, 6))
        self._pair(f, "单句上限（字）", self._spin(f, self.var_max_chars, 0, 400),
                   "沉默几次后必说", self._spin(f, self.var_force, 0, 99))
        self._pair(f, "基线窗口（格）", self._spin(f, self.var_window, 6, 120, inc=6),
                   "普通话题寿命", self._spin(f, self.var_thread_life, 2, 3))
        self._hint(p, "0 = 不限字数。一轮建议 1–3 句；说太多会像在解说画面。"
                      "基线窗口：调度层拿最近这么多格的画面统计当相对基线（24 格 ≈ 2 分钟），"
                      "样本不够时退回绝对地板。话题寿命：普通话题 2~3 格（开头/中间/收尾），"
                      "一个招牌、一阵光不会连着说四五格；看一个地方固定走完 3~4 格，"
                      "七成第三格就走（请求过去 → 看一会 → 离开）；故事线程另外随机 4~6 格。"
                      "这几类线程中途不会被招牌/光变抢断，只有「他停了」能打断。\n"
                      "「话量」只改她说话的**语气**（一两句还是一小段），"
                      "不改变多久开一次口——开不开口由调度层按画面事件决定。"
                      "流式速度和整段/逐句显示在「更多」页调。")

    def _update_key_hint(self):
        try:
            c = self._conn_cfg() if hasattr(self, "var_provider") else self.cfg
            src, how = cfgmod.api_key_source(c)
            name = providers.get(cfgmod.provider_id(c)).get("name", "")
        except Exception:
            src, how, name = "none", "", ""
        if hasattr(self, "conn_label"):
            try:
                self.conn_label.configure(text=name or "—")
            except Exception:
                pass
        if not hasattr(self, "key_hint"):
            return
        if src == "env":
            self.key_hint.configure(
                text="已读到 key · 来自环境变量 %s（优先级最高）" % cfgmod.api_key_env(c),
                fg="#8FC97A")
        elif src == "store":
            self.key_hint.configure(text="已读到 key · 来自本机加密存储 keys.dat", fg="#8FC97A")
        else:
            self.key_hint.configure(text="还没配 key · 现在走演示台词", fg=self.ACC)

    def _update_scale_hint(self):
        sc = SCALE_VALUE.get(self.var_scale.get(), 1.0)
        c = self.cfg.get("capture", {})
        r = c.get("region") or {}
        bw = int(r.get("width") or 0) if c.get("mode") == "region" else 0
        bh = int(r.get("height") or 0) if c.get("mode") == "region" else 0
        if bw >= 32 and bh >= 32:
            nw, nh = max(1, int(bw * sc)), max(1, int(bh * sc))
            pct = 100.0 * (nw * nh) / max(1, bw * bh)
            txt = "选区 %d×%d → 发送 %d×%d，图片 token 约为原来的 %.0f%%" % (
                bw, bh, nw, nh, pct)
        else:
            txt = "按截图区域等比缩小；越小的图越省，但也越看不清小字和细节"
        try:
            self.scale_label.configure(text=txt)
        except Exception:
            pass

    # ---------- 连接 ----------

    def _provider_label(self, pid):
        for i, name in providers.names():
            if i == pid:
                return name
        return providers.get(pid).get("name", pid)

    def _provider_id(self):
        label = self.var_provider.get() if hasattr(self, "var_provider") else ""
        for i, name in providers.names():
            if name == label:
                return i
        return cfgmod.provider_id(self.cfg)

    def _conn_cfg(self):
        """按界面上**没保存**的值拼一份配置，用来测连接/看状态。"""
        c = copy.deepcopy(self.cfg)
        c["vlm"].pop("api_key", None)
        pid = self._provider_id()
        c["vlm"]["provider"] = pid
        c["vlm"]["base_url"] = (self.var_base.get().strip()
                                or providers.get(pid).get("base_url") or "")
        c["vlm"]["model"] = (self.var_model.get().strip()
                             or providers.default_model(pid))
        return c

    def _build_connect_page(self):
        p = self.page_bodies["connect"]
        pid = cfgmod.provider_id(self.cfg)
        self.var_provider = tk.StringVar(value=self._provider_label(pid))

        self._lab(p, "接哪家的眼睛", color=self.ACC, size=11, bold=True).pack(
            anchor="w", padx=18, pady=(12, 2))
        self._lab(p, "这个软件是「看图说话」，所以要接一个能看图的模型。"
                     "默认 DeepSeek；别家只要兼容 OpenAI 接口（/chat/completions）就能接。"
                     "换一家、填个 key、点保存，就这套动作。",
                  size=9, color=self.DIM, justify="left", wraplength=820).pack(
            anchor="w", padx=18, pady=(0, 2))

        f = self._block("服务商", p)
        r = self._next_row(f)
        self._lab(f, "接入方", width=12).grid(row=r, column=0, sticky="w", pady=3)
        self.provider_combo = self._combo(f, self.var_provider,
                                          [n for _i, n in providers.names()], 24)
        self.provider_combo.grid(row=r, column=1, columnspan=2, sticky="we",
                                 pady=3, padx=(0, 8))
        self.provider_combo.bind("<<ComboboxSelected>>", lambda e: self._on_provider())
        bf = tk.Frame(f, bg=self.BG)
        bf.grid(row=r, column=3, sticky="e", pady=3)
        self._button(bf, "申请 / 管理 key", self._open_docs).pack(side="left")
        self.provider_note = self._lab(f, "", size=9, color=self.DIM,
                                       justify="left", wraplength=740)
        self.provider_note.grid(row=self._next_row(f), column=0, columnspan=4,
                                sticky="we", pady=(2, 4))

        f = self._block("地址和模型", p)
        self._line(f, "Base URL", self._entry(f, self.var_base, width=54))
        r = self._next_row(f)
        self._lab(f, "模型", width=12).grid(row=r, column=0, sticky="w", pady=3)
        self.model_combo = self._combo(f, self.var_model, [], 30, readonly=False)
        self.model_combo.grid(row=r, column=1, columnspan=2, sticky="we",
                              pady=3, padx=(0, 8))
        self.model_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_vision())
        self.model_combo.bind("<FocusOut>", lambda e: self._refresh_vision())
        bf = tk.Frame(f, bg=self.BG)
        bf.grid(row=r, column=3, sticky="e", pady=3)
        self._button(bf, "拉取模型列表", self._fetch_models).pack(side="left")
        self.vision_hint = self._lab(f, "", size=9, color=self.DIM,
                                     justify="left", wraplength=740)
        self.vision_hint.grid(row=self._next_row(f), column=0, columnspan=4, sticky="we")

        f = self._block("Key", p)
        r = self._next_row(f)
        self._lab(f, "API Key", width=12).grid(row=r, column=0, sticky="w", pady=3)
        self.var_key = tk.StringVar(value="")
        self.key_entry = self._entry(f, self.var_key, width=46, show="●")
        self.key_entry.grid(row=r, column=1, columnspan=2, sticky="we",
                            pady=3, padx=(0, 8))
        self._button(f, "保存到本机", self._save_key, primary=True).grid(
            row=r, column=3, sticky="e")

        self.key_status = tk.Label(f, text="", bg=self.BG, fg=self.DIM,
                                   font=(self._F, 9), anchor="w", justify="left",
                                   wraplength=740)
        self.key_status.grid(row=self._next_row(f), column=0, columnspan=4,
                             sticky="we", pady=(2, 4))

        bf = tk.Frame(f, bg=self.BG)
        bf.grid(row=self._next_row(f), column=0, columnspan=4, sticky="w")
        self._button(bf, "测试连接", self._test_conn).pack(side="left")
        self._button(bf, "清除本机 key", self._clear_key).pack(side="left", padx=8)
        self.probe_status = tk.Label(f, text="", bg=self.BG, fg=self.DIM,
                                     font=(self._F, 9), anchor="w", justify="left",
                                     wraplength=740)
        self.probe_status.grid(row=self._next_row(f), column=0, columnspan=4,
                               sticky="we", pady=(4, 2))

        self._lab(p, "key 的优先级：环境变量 > 本机加密存储。加密用 Windows DPAPI，"
                     "绑当前账户——keys.dat 拷到别的电脑也解不开。"
                     "config.json 里永远不会有 key，打包/备份不会把它带出去。",
                  size=9, color=self.DIM, justify="left", wraplength=820).pack(
            anchor="w", padx=18, pady=(8, 0))
        self._on_provider(fill_only=True)

    def _on_provider(self, fill_only=False):
        pid = self._provider_id()
        p = providers.get(pid)
        ids = [m["id"] for m in p.get("models") or []]
        if not fill_only:
            self.var_base.set(p.get("base_url") or "")
            if ids and self.var_model.get().strip() not in ids:
                self.var_model.set(ids[0])
        self.model_combo.configure(values=ids)
        self.provider_note.configure(text=p.get("note") or "")
        if not fill_only:
            self.probe_status.configure(text="")
        self._refresh_vision()
        self._refresh_key_ui()

    def _refresh_vision(self):
        pid = self._provider_id()
        model = self.var_model.get().strip()
        v, note = providers.model_vision(pid, model)
        if v is True:
            self.vision_hint.configure(
                text="✓ 预设里标了「能看图」" + ("（%s）" % note if note else ""),
                fg="#8FC97A")
        elif v is False:
            self.vision_hint.configure(
                text="⚠ 这个模型不看图 —— 她会看不见画面，换一个视觉模型",
                fg="#E08A7A")
        else:
            self.vision_hint.configure(
                text="这个模型不在预设里，不知道看不看图 —— 点「测试连接」拿一张纯红图验一下",
                fg=self.DIM)

    def _refresh_key_ui(self):
        c = self._conn_cfg()
        src, how = cfgmod.api_key_source(c)
        env = cfgmod.api_key_env(c)
        if src == "env":
            self.key_status.configure(
                text="现在用：环境变量 %s（它优先级最高，本机存了也压不过它）" % env,
                fg="#8FC97A")
        elif src == "store":
            self.key_status.configure(
                text="现在用：本机加密存储 keys.dat（Windows DPAPI，绑当前账户）",
                fg="#8FC97A")
        else:
            self.key_status.configure(
                text="现在没有 key —— 在下面填一个点「保存到本机」，或者设环境变量 %s" % env,
                fg=self.ACC)
        self._update_key_hint()

    def _apply_conn_to_cfg(self):
        c = self.cfg
        pid = self._provider_id()
        c["vlm"]["provider"] = pid
        c["vlm"]["base_url"] = (self.var_base.get().strip()
                                or providers.get(pid).get("base_url") or "")
        c["vlm"]["model"] = (self.var_model.get().strip()
                             or providers.default_model(pid))

    def _open_docs(self):
        pid = self._provider_id()
        url = providers.get(pid).get("docs") or ""
        if not url:
            messagebox.showinfo(
                "没有固定地址",
                "这家没有预设的申请页。搜「%s API key」就能找到控制台。"
                % providers.get(pid).get("name", pid), parent=self.win)
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showinfo("打不开浏览器", "%s\n\n%s" % (url, e), parent=self.win)

    def _save_key(self):
        pid = self._provider_id()
        key = self.var_key.get().strip()
        if not key:
            self.probe_status.configure(text="先在上面填 key，再点保存", fg=self.ACC)
            return
        ok, msg = keystore.put(pid, key)
        if ok:
            self.var_key.set("")
            self.probe_status.configure(text="已保存：" + msg, fg="#8FC97A")
            self._apply_conn_to_cfg()
            # 选的服务商/地址/模型也立刻落盘；不然换个服务商存了 key，
            # 下次启动又回到默认服务商，看起来像"key 没保存"。
            try:
                self._apply_to_cfg()
            except Exception:
                pass
            try:
                self.app.key = cfgmod.api_key(self._conn_cfg())
            except Exception:
                pass
        else:
            self.probe_status.configure(text="没保存成：" + msg, fg="#E08A7A")
        self._refresh_key_ui()
        self._update_key_hint()

    def _clear_key(self):
        pid = self._provider_id()
        name = providers.get(pid).get("name", pid)
        if not keystore.which(pid):
            self.probe_status.configure(text="本机没存过「%s」的 key" % name, fg=self.DIM)
            return
        if not messagebox.askyesno("清除 key", "清除本机保存的「%s」key？" % name,
                                   parent=self.win):
            return
        keystore.clear(pid)
        self.probe_status.configure(text="已清除「%s」的本机 key" % name, fg="#8FC97A")
        self._refresh_key_ui()
        self._update_key_hint()

    @staticmethod
    def _probe_png():
        im = Image.new("RGB", (96, 96), (222, 28, 34))
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    def _test_conn(self):
        cfg = self._conn_cfg()
        key = self.var_key.get().strip() or cfgmod.api_key(cfg)
        if not key:
            self.probe_status.configure(
                text="没有 key —— 先填一个，或设环境变量 %s" % cfgmod.api_key_env(cfg),
                fg=self.ACC)
            return
        self.probe_status.configure(text="① 测接口…", fg=self.DIM)

        def run():
            r1 = brain.probe(cfg, key, None, "只回复两个字：可以", max_tokens=16)
            r2 = {}
            if r1.get("ok"):
                try:
                    self.win.after(0, lambda: self.probe_status.configure(
                        text="① 接口通了，② 拿一张纯红图测看不看图…", fg=self.DIM))
                except Exception:
                    pass
                r2 = brain.probe(cfg, key, self._probe_png(),
                                 "这张图是什么颜色？只回答颜色词。", max_tokens=12)
            try:
                self.win.after(0, lambda: self._show_probe(r1, r2))
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _show_probe(self, r1, r2):
        parts = []
        ok1 = bool(r1.get("ok"))
        parts.append("① 接口%s（%.1fs）" % ("通了" if ok1 else "不通",
                                          r1.get("latency") or 0))
        if ok1:
            parts.append("它回「%s」" % (r1.get("text") or "")[:16])
        else:
            parts.append((r1.get("error") or "")[:90])
        color = "#8FC97A" if ok1 else "#E08A7A"
        if r2:
            ans = (r2.get("text") or "").strip()
            low = ans.lower()
            # 纯红图被说成“橙/橘/red”也算看见了；这里测的是“有没有看到图”，
            # 不是考颜色命名。只说“无法查看/看不到”才判不认图。
            see = r2.get("ok") and any(
                k in low for k in ("红", "red", "橙", "orange", "橘", "朱", "#"))
            parts.append("② 看图：%s" % ("看得见" if see else "没说出颜色 —— 可能不认图"))
            parts.append("它说「%s」" % ans[:16])
            if not see:
                color = "#E08A7A"
        elif ok1:
            parts.append("② 看图：没测（接口没通）")
        self.probe_status.configure(text=" · ".join(parts), fg=color)
        if ok1 and r2:
            self._refresh_vision()

    def _fetch_models(self):
        cfg = self._conn_cfg()
        key = self.var_key.get().strip() or cfgmod.api_key(cfg)
        base = cfg["vlm"]["base_url"]
        if not base:
            self.probe_status.configure(text="先填 Base URL", fg=self.ACC)
            return
        self.probe_status.configure(text="正在从 %s 拉模型列表…" % base, fg=self.DIM)

        def run():
            ids, err = providers.fetch_models(base, key)
            try:
                self.win.after(0, lambda: self._show_models(ids, err))
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _show_models(self, ids, err):
        if not ids:
            self.probe_status.configure(
                text="拉不到模型列表：%s（有些网关不开放 /models，直接手填模型名就行）" % err,
                fg=self.ACC)
            return
        self.model_combo.configure(values=ids)
        cur = self.var_model.get().strip()
        if cur not in ids:
            self.var_model.set(ids[0])
        self.probe_status.configure(
            text="拉到 %d 个模型，可以下拉选（带 vl / vision / 4o 的通常能看图）" % len(ids),
            fg="#8FC97A")
        self._refresh_vision()

    # ---------- 角色 ----------

    def _build_persona_page(self):
        p = self.page_bodies["persona"]
        cur = self.cfg
        self.personas = [dict(x) for x in (cur.get("personas") or [])]
        if not self.personas:
            self.personas = [dict(cur.get("persona") or {})]
        try:
            self.cur_p = int(cur.get("active_persona") or 0)
        except Exception:
            self.cur_p = 0
        if not (0 <= self.cur_p < len(self.personas)):
            self.cur_p = 0

        f = self._block("角色", p)
        head = tk.Frame(f, bg=self.BG)
        head.grid(row=self._next_row(f), column=0, columnspan=4, sticky="we", pady=(0, 5))
        self.var_p = tk.StringVar()
        self.pcombo = self._combo(head, self.var_p, [], 16)
        self.pcombo.pack(side="left")
        self.pcombo.bind("<<ComboboxSelected>>", self._on_pick_persona)
        self._button(head, "新角色", self._new_persona).pack(side="left", padx=(6, 0))
        self._button(head, "删除", self._del_persona).pack(side="left", padx=(6, 0))

        self.var_name = tk.StringVar()
        self.var_call = tk.StringVar()
        self.var_relation = tk.StringVar()
        self.var_tone = tk.StringVar()
        self._pair(f, "名字", self._entry(f, self.var_name),
                   "叫他", self._entry(f, self.var_call))
        self._pair(f, "关系", self._entry(f, self.var_relation),
                   "语气", self._entry(f, self.var_tone))
        self._hint(p, "「叫他」填你希望她怎么称呼你（名字/外号都行）。"
                      "留空就只说「你」。关系、语气都会直接写进提示词。\n"
                      "（原来这里还有一个「示例句」编辑框，它从来没进过提示词；"
                      "而且把例句贴进提示词容易被原样抄走，已经去掉。）")

        f = self._block("好感", p)
        self.aff_label = tk.Label(f, text="", bg=self.BG, fg="#FF7AB0",
                                  font=(self._F, 11), anchor="w")
        self.aff_label.grid(row=self._next_row(f), column=0, columnspan=3,
                            sticky="we", pady=3)
        self._button(f, "清零", self._reset_affection).grid(
            row=0, column=3, sticky="e", pady=3)
        self._hint(p, "她每觉得画面好看一次（【态】里的「心动=是」），随机 +1~5，"
                      "字幕下面会出现一行粉字「好感 +N」。点暂停时结算一段。")
        self._refresh_affection()

    def _refresh_affection(self):
        try:
            n = int((self.app.state or {}).get("affection") or 0)
        except Exception:
            n = 0
        try:
            self.aff_label.configure(text="现在 %d" % n)
        except Exception:
            pass

    def _reset_affection(self):
        if not messagebox.askyesno("清零好感", "把好感清零？这个不能撤回。",
                                   parent=self.win):
            return
        try:
            self.app.state["affection"] = 0
            self.app._save_state()
        except Exception:
            pass
        self._refresh_affection()

    def _refresh_persona_list(self):
        names = []
        for i, per in enumerate(getattr(self, "personas", [])):
            nm = (per.get("name") or "").strip() or ("角色 %d" % (i + 1))
            names.append(nm)
        for cb in (getattr(self, "pcombo", None), getattr(self, "pcombo_main", None)):
            if cb is None:
                continue
            try:
                cb["values"] = names
                cb.current(self.cur_p)
            except Exception:
                pass
        try:
            per = self.personas[self.cur_p] if self.personas else {}
            nm = (per.get("name") or "她").strip()
            call = (per.get("call_player") or "").strip()
            self.cur_persona_label.configure(
                text=nm + (("  ·  他叫你「%s」" % call) if call else ""))
        except Exception:
            pass

    def _load_persona(self, idx):
        per = self.personas[idx] if 0 <= idx < len(self.personas) else {}
        self.var_name.set(per.get("name") or "她")
        self.var_call.set(per.get("call_player") or "")
        self.var_relation.set(per.get("relation") or "和你牵着手一起走的人")
        self.var_tone.set(per.get("tone") or "")

    def _stash_persona(self):
        if not (0 <= getattr(self, "cur_p", 0) < len(getattr(self, "personas", []))):
            return
        self.personas[self.cur_p] = {
            "name": self.var_name.get().strip() or "她",
            "call_player": self.var_call.get().strip(),
            "relation": self.var_relation.get().strip() or "和你牵着手一起走的人",
            "tone": self.var_tone.get().strip(),
        }

    def _on_pick_persona(self, e=None):
        w = getattr(e, "widget", None) or self.pcombo
        try:
            idx = w.current()
        except Exception:
            idx = self.pcombo.current()
        if not (0 <= idx < len(self.personas)) or idx == self.cur_p:
            self._refresh_persona_list()
            return
        self._stash_persona()
        self.cur_p = idx
        self._load_persona(idx)
        self._refresh_persona_list()

    def _new_persona(self):
        self._stash_persona()
        base = dict(self.personas[self.cur_p]) if self.personas else {}
        base["name"] = ((base.get("name") or "她").strip() + " 2")
        self.personas.append(base)
        self.cur_p = len(self.personas) - 1
        self._refresh_persona_list()
        self._load_persona(self.cur_p)

    def _del_persona(self):
        if len(self.personas) <= 1:
            self.app._set_status("○ 至少留一个角色")
            return
        self.personas.pop(self.cur_p)
        self.cur_p = max(0, min(self.cur_p, len(self.personas) - 1))
        self._refresh_persona_list()
        self._load_persona(self.cur_p)

    # ---------- 记忆 ----------

    def _build_memory_page(self):
        p = self.pages["memory"]
        c = self.cfg
        f = self._block("记忆预算", p)
        self.var_mem_budget = tk.StringVar(value=str(c["memory"].get("budget_k", 12)))
        self.var_digest_share = tk.StringVar(value=str(c["memory"].get("digest_share_pct", 40)))
        self._pair(f, "记忆预算 K", self._spin(f, self.var_mem_budget, 0, 4096),
                   "长期摘要占比 %", self._spin(f, self.var_digest_share, 10, 70))
        self.mem_ctx_label = tk.Label(f, text="", bg=self.BG, fg=self.ACC,
                                      font=(self._F, 10), anchor="w", justify="left")
        self.mem_ctx_label.grid(row=self._next_row(f), column=0, columnspan=4,
                                sticky="we", pady=(3, 0))
        self.mem_budget_label = tk.Label(f, text="", bg=self.BG, fg=self.DIM,
                                         font=(self._F, 9), anchor="w", justify="left")
        self.mem_budget_label.grid(row=self._next_row(f), column=0, columnspan=4,
                                   sticky="we", pady=(2, 0))
        self._hint(p, "记忆不再按句数。预算按 token 算：0 = 不用记忆；"
                      "超过上下文 60% 的部分会被自动收紧。原文记满后，旧片段会压成长期摘要。"
                      "长期摘要占比 = 预算里留给摘要的比例；越大越偏长期印象，越小越偏最近原话。"
                      "如果刚改过风格或人设，旧记忆会继续影响语气，建议清空一次。",
                   pady=(3, 0))

        f = self._block("记忆行为", p)
        self.var_auto_compress = tk.BooleanVar(value=bool(c["memory"].get("auto_compress", True)))
        self.var_record_silent = tk.BooleanVar(value=bool(c["memory"].get("record_silent", True)))
        self.var_include_time = tk.BooleanVar(value=bool(c["memory"].get("include_time", True)))
        self.var_persist = tk.BooleanVar(value=bool(c["memory"].get("persist", True)))
        cf = tk.Frame(f, bg=self.BG)
        cf.grid(row=self._next_row(f), column=0, columnspan=4, sticky="w", pady=2)
        self._check(cf, self.var_auto_compress, "自动整理旧记忆").pack(side="left", padx=(0, 14))
        self._check(cf, self.var_record_silent, "记下沉默时看到的画面").pack(side="left", padx=(0, 14))
        self._check(cf, self.var_include_time, "带时间").pack(side="left", padx=(0, 14))
        self._check(cf, self.var_persist, "持久保存").pack(side="left")

        r = self._next_row(f)
        bf = tk.Frame(f, bg=self.BG)
        bf.grid(row=r, column=0, columnspan=4, sticky="we", pady=(7, 2))
        self.btn_compress = self._button(bf, "立即整理记忆", self._compress_now)
        self.btn_compress.pack(side="left", padx=(0, 6))
        self._button(bf, "编辑长期记忆", self._edit_digest).pack(side="left", padx=(0, 6))
        self._button(bf, "打开记忆文件", self._open_memory_file).pack(side="left", padx=(0, 6))
        self._button(bf, "去掉历史称呼", self._strip_history_calls).pack(side="left", padx=(0, 6))
        self._button(bf, "清空记忆", self._clear_memory).pack(side="left")
        self.mem_status = tk.Label(f, text="", bg=self.BG, fg=self.DIM, font=(self._F, 9),
                                   anchor="w", justify="left")
        self.mem_status.grid(row=self._next_row(f), column=0, columnspan=4,
                            sticky="we", pady=(3, 0))

        self.mem_stats_label = tk.Label(p, text="", bg=self.BG, fg="#8FC97A",
                                        font=(self._F, 9), anchor="w", justify="left")
        self.mem_stats_label.pack(anchor="w", padx=18, pady=(8, 2))

        head = tk.Frame(p, bg=self.BG)
        head.pack(fill="x", padx=18, pady=(4, 2))
        self._lab(head, "现在会发给她的记忆与聊天记录", color=self.ACC, size=10, bold=True).pack(side="left")
        self._button(head, "刷新预览", self._refresh_memory_page).pack(side="right")

        box = tk.Frame(p, bg=self.BG)
        box.pack(fill="both", expand=True, padx=18, pady=(2, 10))
        self.mem_preview = tk.Text(box, bg=self.CARD, fg=self.FG, relief="flat", bd=0,
                                   padx=10, pady=8, font=(self._F, 10), wrap="word",
                                   state="disabled", highlightthickness=1,
                                   highlightbackground=self.LINE)
        vs = tk.Scrollbar(box, orient="vertical", command=self.mem_preview.yview)
        self.mem_preview.configure(yscrollcommand=vs.set)
        self.mem_preview.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

    def _mem_cfg(self):
        c = copy.deepcopy(self.cfg)
        c["vlm"]["context_k"] = self._int(self.var_ctx, 128, 4, 4096)
        c["memory"]["budget_k"] = self._int(self.var_mem_budget, 12, 0, c["vlm"]["context_k"])
        c["memory"]["digest_share_pct"] = self._int(self.var_digest_share, 40, 10, 70)
        c["memory"]["auto_compress"] = bool(self.var_auto_compress.get())
        c["memory"]["record_silent"] = bool(self.var_record_silent.get())
        c["memory"]["include_time"] = bool(self.var_include_time.get())
        c["memory"]["persist"] = bool(self.var_persist.get())
        return c

    def _refresh_memory_page(self):
        if not getattr(self, "mem_preview", None) or not self._alive:
            return
        try:
            c = self._mem_cfg()
            st = self.app.mem.stats(c)
            budget_k = st["budget_tokens"] / 1024.0
            req_k = st["requested_tokens"] / 1024.0
            self.mem_ctx_label.configure(
                text="模型上下文 %d K　·　实际可用记忆预算 %.1f K%s" % (
                    c["vlm"]["context_k"], budget_k,
                    ("" if abs(req_k - budget_k) < 0.05 else "（已从 %.1f K 收紧）" % req_k)))
            self.mem_budget_label.configure(
                text="原文 %d 条 · 未整理 %d 条 · 摘要 %d 字 · 聊天记录 %d 轮 · 当前约 %.1f K / %.1f K（%d%%）· 会话 %d 次" % (
                    st["entries"], st["unfolded"], st["digest_chars"],
                    st.get("history_turns", 0),
                    st["used_tokens"] / 1024.0, budget_k, st["fill_pct"],
                    st["sessions"]))
            self.mem_stats_label.configure(
                text="最近说过：「%s」" % (st["last_line"][:40] + ("…" if len(st["last_line"]) > 40 else "")
                                         if st["last_line"] else "—"))
            block = self.app.mem.transcript(c)
            # 只要 transcript 里有摘要/聊天/调度状态，就要显示；
            # 不能只看 entries，否则手动写过的长期摘要会被“还没有记忆”盖掉。
            if block and block != "（现在还没有记忆。）":
                preview = block
            else:
                preview = "（现在还没有记忆。她只看得见这一帧。）"
            if (st["entries"] or st["digest_chars"]) \
                    and not (self.app.mem.build_prompt_memory(c)["has_memory"]):
                preview = "（记忆里有内容，但当前预算为 0，这次不会带记忆。）"
            _set_text(self.mem_preview, preview)
        except Exception as e:
            try:
                self.mem_ctx_label.configure(text="刷新失败：%s" % e)
            except Exception:
                pass

    def _compress_now(self):
        if self._compress_running:
            return
        try:
            c = self._mem_cfg()
            pm = self._prompt_map()
            self._compress_running = True
            self.btn_compress.configure(state="disabled")
            self.mem_status.configure(text="正在整理长期记忆…", fg=self.ACC)

            def done(info):
                try:
                    self.win.after(0, lambda: self._on_compress_done(info))
                except Exception:
                    pass

            ok = self.app.mem.compress_async(
                c, cfgmod.api_key(c), pm, on_done=done, force=True)
            if not ok:
                self._compress_running = False
                self.btn_compress.configure(state="normal")
                self.mem_status.configure(text="已经在整理了，等一下", fg=self.ACC)
        except Exception as e:
            self._compress_running = False
            self.btn_compress.configure(state="normal")
            self.mem_status.configure(text="整理失败：%s" % e, fg="#E08A7A")

    def _on_compress_done(self, info):
        self._compress_running = False
        try:
            self.btn_compress.configure(state="normal")
        except Exception:
            pass
        if info.get("ok"):
            msg = "已整理 %d 条旧记忆 → 摘要 %d 字" % (info.get("folded", 0), info.get("digest_chars", 0))
            if info.get("fallback"):
                msg += "（机械折叠）"
            if info.get("error"):
                msg += " · " + str(info.get("error"))[:40]
            self.mem_status.configure(text=msg, fg="#8FC97A")
        elif info.get("skipped"):
            self.mem_status.configure(text=info.get("message") or "不用整理", fg=self.DIM)
        else:
            self.mem_status.configure(text="整理失败：%s" % (info.get("error") or "未知原因"),
                                      fg="#E08A7A")
        self._refresh_memory_page()

    def _edit_digest(self):
        top = tk.Toplevel(self.win)
        top.title("编辑长期记忆")
        top.configure(bg=self.BG)
        try:
            top.transient(self.win)
        except Exception:
            pass
        top.attributes("-topmost", True)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        w = min(760, max(520, sw - 80))
        h = min(560, max(360, sh - 120))
        self._center(top, w, h)
        top.minsize(520, 320)
        top.resizable(True, True)
        top.lift()
        top.after(30, lambda: (top.lift(), ov.force_topmost(top)))

        # 先把底部按钮条钉在窗口底部，再让正文区域占剩余空间；
        # 这样无论窗口多矮、文字多长，保存/取消都不会被挤出屏幕。
        bar = tk.Frame(top, bg=self.BG)
        bar.pack(side="bottom", fill="x", padx=18, pady=10)

        self._lab(top, "长期摘要（会作为「你记得的」发给她）",
                  color=self.ACC, bold=True).pack(anchor="w", padx=18, pady=(12, 2))
        tk.Label(
            top,
            text=("长期记忆不是逐字记录：聊天超过记忆预算后，旧内容会被压缩成这段摘要，"
                  "和最近的聊天记录一起发给她。可以直接改；保存后下一次抓拍生效。"),
            bg=self.BG, fg=self.DIM, font=(self._F, 9), anchor="w", justify="left",
            wraplength=max(360, w - 40)).pack(anchor="w", padx=18, pady=(0, 6))

        box = tk.Frame(top, bg=self.BG)
        box.pack(fill="both", expand=True, padx=18, pady=(0, 4))
        t = tk.Text(box, bg=self.CARD, fg=self.FG, relief="flat", bd=0, padx=10, pady=8,
                    insertbackground=self.FG, font=(self._F, 10), wrap="word", height=10,
                    highlightthickness=1, highlightbackground=self.LINE, highlightcolor=self.ACC)
        t.insert("1.0", self.app.mem.digest or "")
        vs = tk.Scrollbar(box, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        t.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        def save_digest():
            self.app.mem.set_digest(t.get("1.0", "end-1c"))
            self._refresh_memory_page()
            top.destroy()
            self.mem_status.configure(text="长期记忆已更新", fg="#8FC97A")

        self._button(bar, "保存", save_digest, primary=True).pack(side="right")
        self._button(bar, "取消", top.destroy).pack(side="right", padx=8)
        top.bind("<Control-s>", lambda e: save_digest())
        t.focus_set()

    def _open_memory_file(self):
        try:
            if not self.app.mem.persist:
                messagebox.showinfo("持久保存已关闭",
                                    "先勾选「持久保存」并保存设置，再打开记忆文件。",
                                    parent=self.win)
                return
            self.app.mem.save()
            os.startfile(str(self.app.mem.path))
        except Exception as e:
            messagebox.showinfo("打不开", str(e), parent=self.win)

    def _strip_history_calls(self):
        call = (self.var_call.get() or "").strip()
        if not call:
            messagebox.showinfo("没有称呼", "「角色」页的「叫他」是空的，没东西可清理。",
                                parent=self.win)
            return
        if not messagebox.askyesno(
                "去掉历史称呼",
                ("把本地记忆台词里重复出现的「%s，」去掉？"
                 "只改记忆台词，不动提示词，也不影响画面。") % call,
                parent=self.win):
            return
        n = self.app.mem.strip_call_history(call)
        self._refresh_memory_page()
        self.mem_status.configure(text="已清理 %d 条历史称呼" % n, fg="#8FC97A")

    def _clear_memory(self):
        if not messagebox.askyesno(
                "清空记忆",
                "会清掉：长期摘要、最近说过的话、以及**正在聊的那件事**（话题和欠账）。\n"
                "好感是另一个计数，要清请点「角色 → 清零」。继续？".replace("**", ""),
                parent=self.win):
            return
        self.app.mem.clear()
        # 光清记忆文件不够：运行中的 Router 还挂着"刚才那件事"和"欠的账"，
        # 下一格会把这些写回记忆。清空就得连它一起清。
        try:
            self.app.router.reset()
            self.app.mem.save_router(self.app.router.snapshot())
        except Exception:
            pass
        self._refresh_memory_page()
        self.mem_status.configure(text="记忆已清空（包括正在聊的那件事）", fg="#8FC97A")

    # ---------- 提示词工作台 ----------

    def _build_prompts_page(self):
        p = self.pages["prompts"]
        self._prompt_keys = [k for k, _, _ in prompts.META]
        self._prompt_keys.append("_readme")
        try:
            pm = prompts.load_all(force=True)
        except Exception:
            pm = {}
        for key in self._prompt_keys:
            if key == "_readme":
                self._prompt_buffers[key] = prompts.read_readme()
                self._prompt_mtime[key] = prompts.readme_mtime_ns()
            else:
                self._prompt_buffers[key] = pm.get(key, "")
                self._prompt_mtime[key] = prompts.mtime_ns(key)
            self._prompt_saved[key] = self._prompt_buffers[key]

        head = tk.Frame(p, bg=self.BG)
        head.pack(fill="x", padx=18, pady=(10, 4))
        self._lab(head, "提示词工作台", color=self.ACC, size=11, bold=True).pack(side="left")
        self._lab(head, "　改完保存，下一次抓拍立刻生效；也可以不保存直接实测。"
                       "编辑器里是模板，{{call}} 这类占位符会在发送前替换。",
                  color=self.DIM, size=9).pack(side="left")

        mainf = tk.Frame(p, bg=self.BG)
        mainf.pack(fill="both", expand=True, padx=18, pady=(2, 8))
        mainf.columnconfigure(1, weight=1)
        mainf.rowconfigure(0, weight=1)

        left = tk.Frame(mainf, bg=self.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.prompt_list = tk.Listbox(left, bg=self.CARD, fg=self.FG, bd=0, relief="flat",
                                      font=(self._F, 10), width=27, exportselection=False,
                                      selectbackground=self.ACC, selectforeground="#0F0E14",
                                      highlightthickness=1, highlightbackground=self.LINE,
                                      activestyle="none")
        self.prompt_list.pack(fill="both", expand=True)
        self.prompt_list.bind("<<ListboxSelect>>", self._on_prompt_select)
        lb_btns = tk.Frame(left, bg=self.BG)
        lb_btns.pack(fill="x", pady=(6, 0))
        self._button(lb_btns, "打开文件夹", prompts.open_folder).pack(side="left")
        self._button(lb_btns, "重新载入", self._reload_prompts).pack(side="left", padx=4)
        self._button(lb_btns, "恢复默认", self._restore_prompt_default).pack(side="left")

        right = tk.Frame(mainf, bg=self.BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)

        self.prompt_title = tk.Label(right, text="", bg=self.BG, fg=self.FG,
                                     font=(self._F, 10, "bold"), anchor="w")
        self.prompt_title.grid(row=0, column=0, sticky="we")
        self.prompt_info = tk.Label(right, text="", bg=self.BG, fg=self.DIM,
                                    font=(self._F, 9), anchor="w", justify="left")
        self.prompt_info.grid(row=1, column=0, sticky="we", pady=(1, 4))

        self.prompt_edit = tk.Text(right, bg=self.CARD, fg=self.FG, relief="flat", bd=0,
                                   insertbackground=self.FG, padx=10, pady=8,
                                   font=(self._F, 10), wrap="word", undo=True,
                                   highlightthickness=1, highlightbackground=self.LINE,
                                   highlightcolor=self.ACC)
        self.prompt_edit.grid(row=3, column=0, sticky="nsew")
        pvs = tk.Scrollbar(right, orient="vertical", command=self.prompt_edit.yview)
        self.prompt_edit.configure(yscrollcommand=pvs.set)
        pvs.grid(row=3, column=1, sticky="ns")
        self.prompt_edit.bind("<<Modified>>", self._on_prompt_modified)
        self.prompt_edit.bind("<Control-s>", lambda e: (self._save_current_prompt(), "break")[1])

        self.prompt_banner = tk.Label(right, text="", bg=self.BG, fg=self.DIM,
                                      font=(self._F, 9), anchor="w", justify="left")
        self.prompt_banner.grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))

        bf = tk.Frame(right, bg=self.BG)
        bf.grid(row=5, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self._button(bf, "保存当前文件", self._save_current_prompt).pack(side="left", padx=(0, 5))
        self._button(bf, "保存全部并应用", self._save_all_prompts_btn, primary=True).pack(
            side="left", padx=(0, 5))
        self._button(bf, "预览实际请求", self._preview_prompt).pack(side="left", padx=(0, 5))
        self.var_test_force = tk.BooleanVar(value=False)
        self._check(bf, self.var_test_force, "强制开口").pack(side="left", padx=(0, 6))
        self.btn_test = self._button(bf, "实测这一版", self._test_current, primary=True)
        self.btn_test.pack(side="left", padx=(0, 5))
        self.btn_test_file = self._button(bf, "选图测试", self._test_from_file)
        self.btn_test_file.pack(side="left")

        self.test_status = tk.Label(p, text="", bg=self.BG, fg=self.DIM,
                                    font=(self._F, 9), anchor="w", justify="left")
        self.test_status.pack(anchor="w", padx=18, pady=(0, 6))
        self._refresh_prompt_list()

    def _prompt_filename(self, key):
        if key == "_readme":
            return prompts.README
        return prompts.FILES.get(key, key)

    def _refresh_prompt_list(self):
        if not getattr(self, "prompt_list", None):
            return
        self.prompt_list.delete(0, "end")
        for key in self._prompt_keys:
            mark = "  *" if key in self._prompt_dirty else ""
            self.prompt_list.insert("end", self._prompt_filename(key) + mark)
        if self._current_prompt in self._prompt_keys:
            idx = self._prompt_keys.index(self._current_prompt)
            self.prompt_list.selection_clear(0, "end")
            self.prompt_list.selection_set(idx)
            self.prompt_list.see(idx)

    def _on_prompt_select(self, e=None):
        try:
            sel = self.prompt_list.curselection()
            if not sel:
                return
            key = self._prompt_keys[int(sel[0])]
        except Exception:
            return
        if key != self._current_prompt:
            self._stash_prompt_editor()
            self._select_prompt(key)

    def _select_prompt(self, key):
        self._current_prompt = key
        text = self._prompt_buffers.get(key, "")
        self.prompt_edit.delete("1.0", "end")
        self.prompt_edit.insert("1.0", text)
        self.prompt_edit.edit_modified(False)
        self._update_prompt_info()
        self._refresh_prompt_list()

    def _stash_prompt_editor(self):
        key = getattr(self, "_current_prompt", None)
        if not key or key not in self._prompt_keys:
            return
        try:
            text = self.prompt_edit.get("1.0", "end-1c")
        except Exception:
            return
        self._prompt_buffers[key] = text
        if text != self._prompt_saved.get(key, ""):
            self._prompt_dirty.add(key)
        else:
            self._prompt_dirty.discard(key)

    def _on_prompt_modified(self, e=None):
        try:
            if not self.prompt_edit.edit_modified():
                return
            self.prompt_edit.edit_modified(False)
        except Exception:
            return
        self._stash_prompt_editor()
        self._update_prompt_info()
        self._refresh_prompt_list()

    def _update_prompt_info(self):
        key = self._current_prompt
        if not key:
            return
        text = self._prompt_buffers.get(key, "")
        name = self._prompt_filename(key)
        dirty = " · 未保存" if key in self._prompt_dirty else ""
        if key == "_readme":
            meta = "所有可用占位符与文件说明"
            mtime = "—"
        else:
            meta = dict((k, d) for k, _, d in prompts.META).get(key, "")
            mtime = self._mtime_label(key)
        self.prompt_title.configure(text=name)
        self.prompt_info.configure(
            text="%s · %s · %d 字 · ≈%d tok%s" % (
                meta, mtime, len(text), prompts.estimate_tokens(text), dirty))
        if dirty:
            self.prompt_banner.configure(text="有未保存修改 · Ctrl+S 保存 · 实测会直接用当前编辑器里的版本",
                                         fg=self.ACC)
        else:
            self.prompt_banner.configure(text="已保存 · 外部修改会自动载入", fg=self.DIM)

    def _mtime_label(self, key):
        ns = self._prompt_mtime.get(key, 0)
        if not ns:
            return "未创建"
        import time
        return time.strftime("%m-%d %H:%M:%S", time.localtime(ns / 1e9))

    def _save_current_prompt(self):
        self._stash_prompt_editor()
        key = self._current_prompt
        if key:
            self._save_prompt_key(key)

    def _save_prompt_key(self, key):
        text = self._prompt_buffers.get(key, "")
        try:
            if key == "_readme":
                prompts.save_readme(text)
                self._prompt_mtime[key] = prompts.readme_mtime_ns()
            else:
                prompts.save_file(key, text)
                self._prompt_mtime[key] = prompts.mtime_ns(key)
            self._prompt_saved[key] = text
            self._prompt_dirty.discard(key)
            self._refresh_prompt_list()
            self._update_prompt_info()
            self.prompt_banner.configure(
                text="已保存 %s · 下一次抓拍立刻生效" % self._prompt_filename(key), fg="#8FC97A")
            return True
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.win)
            return False

    def _save_all_prompts_btn(self):
        self._save_all_prompts(show=True)

    def _save_all_prompts(self, show=False):
        self._stash_prompt_editor()
        ok = True
        for key in list(self._prompt_dirty):
            if not self._save_prompt_key(key):
                ok = False
        if show and ok:
            self.prompt_banner.configure(text="所有提示词已保存并应用", fg="#8FC97A")
            self.app._set_status("● 提示词已应用")
        self._refresh_prompt_list()
        return ok

    def _reload_prompts(self):
        self._stash_prompt_editor()
        if self._prompt_dirty:
            if not messagebox.askyesno("重新载入", "有未保存的修改，会被磁盘版本覆盖。继续？",
                                       parent=self.win):
                return
        prompts.reload_all()
        pm = prompts.load_all(force=True)
        for key in self._prompt_keys:
            if key == "_readme":
                self._prompt_buffers[key] = prompts.read_readme()
                self._prompt_mtime[key] = prompts.readme_mtime_ns()
            else:
                self._prompt_buffers[key] = pm.get(key, "")
                self._prompt_mtime[key] = prompts.mtime_ns(key)
            self._prompt_saved[key] = self._prompt_buffers[key]
        self._prompt_dirty.clear()
        self._select_prompt(self._current_prompt)
        self.prompt_banner.configure(text="已重新载入磁盘上的提示词", fg="#8FC97A")

    def _restore_prompt_default(self):
        key = self._current_prompt
        if not key:
            return
        if not messagebox.askyesno("恢复默认", "要用默认内容覆盖 %s 吗？" % self._prompt_filename(key),
                                   parent=self.win):
            return
        try:
            if key == "_readme":
                text = prompts.restore_readme()
                self._prompt_mtime[key] = prompts.readme_mtime_ns()
            else:
                text = prompts.restore_file(key)
                self._prompt_mtime[key] = prompts.mtime_ns(key)
            self._prompt_buffers[key] = text
            self._prompt_saved[key] = text
            self._prompt_dirty.discard(key)
            if key == self._current_prompt:
                self._select_prompt(key)
            self._refresh_prompt_list()
            self.prompt_banner.configure(text="已恢复默认：%s" % self._prompt_filename(key),
                                         fg="#8FC97A")
        except Exception as e:
            messagebox.showerror("恢复失败", str(e), parent=self.win)

    def _watch_prompts(self):
        if not self._alive:
            return
        try:
            changed = []
            for key in self._prompt_keys:
                mtime = prompts.readme_mtime_ns() if key == "_readme" else prompts.mtime_ns(key)
                if mtime != self._prompt_mtime.get(key):
                    changed.append((key, mtime))
            if changed:
                blocked = False
                for key, mtime in changed:
                    if key in self._prompt_dirty:
                        blocked = True
                        self._prompt_mtime[key] = mtime
                        continue
                    if key == "_readme":
                        text = prompts.read_readme()
                    else:
                        text = prompts.read_file(key)
                    self._prompt_buffers[key] = text
                    self._prompt_saved[key] = text
                    self._prompt_mtime[key] = mtime
                    if key == self._current_prompt:
                        self.prompt_edit.delete("1.0", "end")
                        self.prompt_edit.insert("1.0", text)
                        self.prompt_edit.edit_modified(False)
                if blocked:
                    self.prompt_banner.configure(
                        text="检测到外部修改，但当前文件有未保存内容，没有覆盖", fg=self.ACC)
                else:
                    self.prompt_banner.configure(
                        text="已自动载入外部修改 · %s" % self._mtime_label(self._current_prompt),
                        fg="#8FC97A")
                self._refresh_prompt_list()
                self._update_prompt_info()
        except Exception:
            pass
        try:
            self.win.after(2000, self._watch_prompts)
        except Exception:
            pass

    def _prompt_map(self):
        m = prompts.load_all()
        for key in self._prompt_keys:
            if key != "_readme":
                m[key] = self._prompt_buffers.get(key, m.get(key, ""))
        return m

    # ---------- 预览 / 实测 ----------

    def _fill_cfg(self, c, persona=True, hotkeys=False):
        """把界面上的值写进配置。_test_cfg 和 _apply_to_cfg 共用这一份映射。

        以前是两个方法各写一遍，四十来行逐字段重复——加一个设置项要记得改两处，
        漏一次就是"预览和真实保存不一致"。现在只有这一份。
        """
        c["vlm"].pop("api_key", None)   # key 只走环境变量 / keys.dat，不落 config.json
        pid = self._provider_id()
        c["vlm"]["provider"] = pid
        c["vlm"]["base_url"] = (self.var_base.get().strip()
                                or providers.get(pid).get("base_url") or "")
        c["vlm"]["model"] = (self.var_model.get().strip()
                             or providers.default_model(pid))
        c["vlm"]["context_k"] = self._int(self.var_ctx, 128, 4, 4096)
        c["vlm"]["reasoning_effort"] = THINK_CODES.get(self.var_think.get(), "none")
        c["vlm"]["detail"] = DETAIL_CODES.get(self.var_detail.get(), "high")
        c["vlm"]["thinking_style"] = THINK_STYLE_CODES.get(self.var_think_style.get(), "auto")
        c["vlm"]["max_tokens"] = self._int(self.var_max_tokens, 500, 32, 4096)
        c["vlm"]["timeout"] = self._float(self.var_timeout, 30, 5, 180)
        c["vlm"]["temperature"] = self._float(self.var_temperature, 0.6, 0, 1.5)

        c["memory"]["budget_k"] = self._int(self.var_mem_budget, 12, 0, c["vlm"]["context_k"])
        c["memory"]["digest_share_pct"] = self._int(self.var_digest_share, 40, 10, 70)
        c["memory"]["auto_compress"] = bool(self.var_auto_compress.get())
        c["memory"]["record_silent"] = bool(self.var_record_silent.get())
        c["memory"]["include_time"] = bool(self.var_include_time.get())
        c["memory"]["persist"] = bool(self.var_persist.get())

        c["behavior"]["silence"] = SILENCE_CODES.get(self.var_silence.get(), "normal")
        c["behavior"]["force_speak_after_skips"] = self._int(self.var_force, 4, 0, 99)
        c["behavior"]["max_line_chars"] = self._int(self.var_max_chars, 60, 0, 400)
        c["behavior"]["sentences_per_take"] = self._int(self.var_sentences, 3, 1, 6)
        c.setdefault("router", {})
        c["router"]["window_ticks"] = self._int(self.var_window, 24, 6, 120)
        c["router"]["thread_life_ticks"] = self._int(self.var_thread_life, 3, 2, 3)

        c["overlay"]["duration"] = self._float(self.var_dur, 5, 1, 20)
        c["overlay"]["story_duration"] = self._float(self.var_story_dur, 2.0, 1, 20)
        c["overlay"]["auto_duration"] = bool(self.var_auto_duration.get())
        c["overlay"]["font_size"] = self._int(self.var_fs, 0, 0, 64)
        c["overlay"]["x_pct"] = self._float(self.var_x, 25, 0, 90)
        c["overlay"]["y_pct"] = self._float(self.var_y, 66, 0, 95)
        c["overlay"]["stream"] = bool(self.var_stream.get())
        c["overlay"]["stream_mode"] = STREAM_MODE_CODES.get(self.var_stream_mode.get(), "accumulate")
        c["overlay"]["stream_cps"] = self._int(self.var_stream_cps, 20, 5, 60)
        c["overlay"]["stream_pause"] = self._float(self.var_stream_pause, 1.0, 0, 3)
        c["misc"]["start_auto"] = bool(self.var_start_auto.get())

        c["capture"]["window_title"] = self.var_win.get().strip()
        c["capture"]["scale"] = SCALE_VALUE.get(self.var_scale.get(), 1.0)
        c["capture"]["max_side"] = self._int(self.var_max_side, 2400, 320, 4096)
        c["capture"]["jpeg_quality"] = self._int(self.var_quality, 88, 40, 100)
        c["capture"]["interval"] = self._int(self.var_int, 5, 3, 120)
        c["capture"]["jitter"] = self._int(self.var_jit, 3, 0, 30)
        c["capture"]["story_interval"] = self._float(self.var_story_int, 2.5, 1, 30)
        c["capture"]["story_jitter"] = self._float(self.var_story_jit, 0.8, 0, 10)
        if self._region != "unset":
            if self._region:
                c["capture"]["region"] = self._region
                c["capture"]["mode"] = "region"
            else:
                c["capture"]["region"] = {"left": 0, "top": 0, "width": 0, "height": 0}
                c["capture"]["mode"] = "window"

        if hotkeys:
            c["hotkeys"]["shoot"] = (self._hot.get("shoot") or "F8").strip()
            c["hotkeys"]["pause"] = (self._hot.get("pause") or "F9").strip()
            c["hotkeys"]["prompts"] = (self._hot.get("prompts") or "ALT+P").strip()

        if persona:
            self._stash_persona()
            c["personas"] = [dict(x) for x in self.personas]
            c["active_persona"] = self.cur_p
            c["persona"] = dict(self.personas[self.cur_p])
        return c

    def _test_cfg(self, persona=True):
        return cfgmod._migrate(self._fill_cfg(copy.deepcopy(self.cfg), persona=persona))

    def _preview_image_tokens(self, cfg):
        c = cfg.get("capture") or {}
        region = c.get("region") or {}
        w = h = 0
        if c.get("mode") == "region" and int(region.get("width", 0) or 0) >= 32:
            w, h = int(region["width"]), int(region["height"])
        else:
            found = capture.find_window(c.get("window_title", ""))
            if found:
                w, h = found[2][2], found[2][3]
        if w <= 0 or h <= 0:
            return 0, (0, 0)
        s = float(c.get("scale", 1.0) or 1.0)
        w2, h2 = max(1, int(w * s)), max(1, int(h * s))
        side = int(c.get("max_side", 2400) or 0)
        if side and max(w2, h2) > side:
            k = side / float(max(w2, h2))
            w2, h2 = max(1, int(w2 * k)), max(1, int(h2 * k))
        return prompts.estimate_image_tokens(w2, h2, cfg["vlm"].get("detail", "high")), (w2, h2)

    def _current_decision(self):
        """按当前调度层状态拼一个"下一格"的 decision，供预览和实测共用。

        预览/实测以前不走 decision，于是线程、记账、信号这几段永远看不到，
        调提示词时等于在盲改。这里和 main._work 的口径保持一致：
        有活跃线程 → 续锚（线程是闲话就不附图）；没有 → 当一次普通开口。
        """
        rt = getattr(self.app, "router", None)
        thread = (rt.thread if rt else None)
        if thread and int(thread.get("remaining") or 0) > 0:
            branch, action = "follow", "续锚"
            story = (thread.get("branch") or "") == "lang"
        else:
            branch, action, story = "follow", "预览", False
        decision = {"branch": branch, "action": action, "events": [], "why": [],
                    "pot": 0, "wish": "", "thread": dict(thread) if thread else None}
        return rt, decision, story

    def _preview_prompt(self):
        self._stash_prompt_editor()
        self._stash_persona()
        c = self._test_cfg()
        pm = self._prompt_map()
        rt, decision, story = self._current_decision()
        since = ((rt.thread or {}).get("started_at") or None) if (story and rt) else None
        mem_ctx = self.app.mem.build_prompt_memory(c, history_since=since)
        shape = "" if story else prompts.pick_shape(pm.get("shapes") or "")
        branch = decision["branch"]
        action = decision["action"]
        prompt = brain.build_prompt(
            c, mem_ctx["digest"], force_speak=bool(self.var_test_force.get()),
            prompt_map=pm, shape=shape,
            recent_lines=self.app.mem.recent_lines(8, max_age_sec=600),
            decision=decision, router=rt, story=story)
        if not prompt:
            messagebox.showwarning("提示词是空的",
                                   "00_主提示词.txt 没有内容，或者占位符被删光了。",
                                   parent=self.win)
            return
        note = brain.current_note_for(
            c, pm, self.app.mem.recent_lines(8, max_age_sec=600), story=story)
        img_tok, size = self._preview_image_tokens(c)
        image_info = ("%d×%d · %s" % (size[0], size[1], c["vlm"].get("detail", "high"))
                      if size[0] else "（当前抓不到窗口，仅按文字估算）")
        transcript = brain.render_transcript(prompt, mem_ctx["history"], note, image_info)
        total = brain.estimate_prompt_tokens(
            prompt, mem_ctx["history"], note, size, c["vlm"].get("detail", "high"),
            image_count=0 if story else 1)
        ctx_k = c["vlm"]["context_k"]
        header = ("本轮随机句式：%s\n"
                  "调度：%s%s（%s）\n"
                  "估算：系统+聊天记录 ≈%d tok + 图片 ≈%d tok = ≈%.1fK / 上下文 %dK\n"
                  "下面是替换后的实际请求；最近的对话以真正的 user/assistant 消息发送。") % (
            shape or "（这次没抽到 / 句式池为空）",
            routermod.BRANCH_CN.get(branch, branch),
            "·故事格不附图" if story else "",
            action,
            max(0, total - img_tok), 0 if story else img_tok, total / 1024.0, ctx_k)
        self._show_text_dialog("拼好的请求", transcript, header)

    def _show_text_dialog(self, title, text, subtitle=""):
        top = tk.Toplevel(self.win)
        top.title(title)
        top.configure(bg=self.BG)
        try:
            top.transient(self.win)
        except Exception:
            pass
        top.attributes("-topmost", True)
        self._center(top, 880, 700)
        top.lift()
        top.after(30, lambda: (top.lift(), ov.force_topmost(top)))
        self._lab(top, title, color=self.ACC, size=11, bold=True).pack(anchor="w", padx=18, pady=(12, 2))
        if subtitle:
            self._lab(top, subtitle, color=self.DIM, size=9, justify="left",
                      wraplength=830).pack(anchor="w", padx=18, pady=(0, 6))
        box = tk.Frame(top, bg=self.BG)
        box.pack(fill="both", expand=True, padx=18, pady=(2, 8))
        t = tk.Text(box, bg=self.CARD, fg=self.FG, relief="flat", bd=0, padx=10, pady=8,
                    font=(self._F, 10), wrap="word", highlightthickness=1,
                    highlightbackground=self.LINE)
        t.insert("1.0", text)
        vs = tk.Scrollbar(box, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        t.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        bar = tk.Frame(top, bg=self.BG)
        bar.pack(fill="x", padx=18, pady=10)
        self._button(bar, "复制", lambda: self._copy_text(top, text)).pack(side="left")
        self._button(bar, "关闭", top.destroy, primary=True).pack(side="right")
        return top

    def _copy_text(self, top, text):
        try:
            top.clipboard_clear()
            top.clipboard_append(text)
            top.update()
        except Exception:
            pass

    def _set_test_running(self, on):
        self._test_running = on
        try:
            state = "disabled" if on else "normal"
            self.btn_test.configure(state=state)
            self.btn_test_file.configure(state=state)
        except Exception:
            pass

    def _make_test_ctx(self, label):
        """实测用的上下文。抓当前帧 / 选图测试原来各拼一份一模一样的字典，合并。"""
        c = self._test_cfg()
        pm = self._prompt_map()
        rt, decision, story = self._current_decision()
        since = ((rt.thread or {}).get("started_at") or None) if (story and rt) else None
        mem_ctx = self.app.mem.build_prompt_memory(c, history_since=since)
        return {
            "cfg": c, "pm": pm, "router": rt,
            "decision": decision, "story": story, "sig": None,
            "shape": "" if story else prompts.pick_shape(pm.get("shapes") or ""),
            "system_memory": mem_ctx["digest"],
            "history": mem_ctx["history"],
            "recent_lines": self.app.mem.recent_lines(8, max_age_sec=600),
            "mem_tokens": mem_ctx["tokens"],
            "history_turns": mem_ctx["history_turns"],
            "force": bool(self.var_test_force.get()),
            "label": label, "image_size": None, "gate": "",
        }

    def _test_current(self):
        if self._test_running:
            return
        self._stash_prompt_editor()
        self._stash_persona()
        self._test_ctx = self._make_test_ctx("当前画面")
        self._set_test_running(True)
        self.test_status.configure(text="正在抓帧…（先把自己的窗口让开）", fg=self.ACC)
        self.win.withdraw()
        try:
            self.app._hide_chrome()
        except Exception:
            pass
        self.win.after(320, self._capture_and_test)

    def _restore_after_capture(self):
        try:
            self.win.deiconify()
            self.win.lift()
            ov.force_topmost(self.win)
        except Exception:
            pass
        try:
            self.app._show_chrome()
        except Exception:
            pass

    def _capture_and_test(self):
        ctx = self._test_ctx
        if not ctx:
            self._set_test_running(False)
            return
        imgs = []
        stamps = []
        err = ""
        label = "当前画面"
        try:
            one, err, label = capture.grab_current(ctx["cfg"])
            if one is not None:
                imgs = [one]
                stamps = [time.time()]
        except Exception as e:
            err = str(e)
        finally:
            self._restore_after_capture()
        if not imgs:
            self._set_test_running(False)
            self.test_status.configure(text="抓不到画面：%s" % err, fg="#E08A7A")
            messagebox.showwarning("抓不到画面",
                                   "%s\n\n可以点「选图测试」，用一张现成的截图试提示词。"
                                   % (err or "未知原因"), parent=self.win)
            return
        ctx["label"] = label
        ctx["shot_ts"] = stamps
        self._prepare_and_run(imgs, ctx)

    def _test_from_file(self):
        if self._test_running:
            return
        path = filedialog.askopenfilename(
            parent=self.win, title="选一张图测试提示词",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.webp"), ("全部", "*.*")])
        if not path:
            return
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            messagebox.showerror("读不了这张图", str(e), parent=self.win)
            return
        self._stash_prompt_editor()
        self._stash_persona()
        self._test_ctx = self._make_test_ctx(os.path.basename(path))
        self._set_test_running(True)
        self.test_status.configure(text="正在用这张图测试…", fg=self.ACC)
        self._prepare_and_run([img], self._test_ctx)

    def _prepare_and_run(self, img, ctx):
        c = ctx["cfg"]["capture"]
        imgs = img if isinstance(img, list) else [img]
        try:
            frames = []
            size = (0, 0)
            for one in imgs:
                jpeg, size = capture.prepare(
                    one, c.get("max_side", 2400), c.get("jpeg_quality", 88),
                    c.get("scale", 1.0))
                frames.append(jpeg)
            ok, reason, _ = capture.frame_gate(imgs[-1], ctx["cfg"], None)
            ctx["frames"] = frames
            ctx["jpeg"] = frames[-1]
            ctx["image_size"] = size
            ctx["gate"] = "通过" if ok else reason
            ctx["source_size"] = imgs[-1].size
            # 信号块也一起算：读字/气泡/三环这几段在实测里以前永远是空的，
            # 调"读字"这类分支时根本看不到真实输入。
            try:
                ctx["sig"] = signals.analyze(imgs[-1])
            except Exception:
                ctx["sig"] = None
            stamps = ctx.get("shot_ts") or []
            labels = []
            if len(frames) > 1 and len(stamps) == len(frames):
                now = time.time()
                labels = ["现在" if i == len(frames) - 1
                          else ("%.1f秒前" % max(0.0, now - stamps[i]))
                          for i in range(len(frames))]
            ctx["frame_labels"] = labels
            try:
                imgs[-1].convert("RGB").save(
                    os.path.join(cfgmod.DATA, "测试帧.jpg"), quality=90)
            except Exception:
                pass
        except Exception as e:
            self._set_test_running(False)
            self.test_status.configure(text="预处理失败：%s" % e, fg="#E08A7A")
            return

        self.test_status.configure(
            text="正在调用模型…（%s · 发送 %d×%d ×%d帧 · 闸门：%s）" % (
                ctx["label"], size[0], size[1], len(frames), ctx["gate"]), fg=self.ACC)
        threading.Thread(target=self._run_test, args=(ctx,), daemon=True).start()

    def _run_test(self, ctx):
        key = cfgmod.api_key(ctx["cfg"])
        decision = ctx.get("decision") or {}
        rt = ctx.get("router")
        th = (rt.thread or {}) if rt else {}
        place_close = bool(
            th and not ctx.get("story")
            and int(th.get("remaining") or 0) <= 1
            and (th.get("branch") or "") in routermod.PLACE_BRANCHES)
        try:
            res = brain.ask(
                ctx.get("frames") or ctx.get("jpeg"), ctx["cfg"], key,
                system_memory=ctx.get("system_memory", ""),
                history=ctx.get("history") or [],
                force_speak=ctx["force"],
                prompt_map=ctx["pm"], shape=ctx["shape"],
                image_size=ctx["image_size"],
                recent_lines=ctx.get("recent_lines") or [],
                decision=decision, sig=ctx.get("sig"),
                router=ctx.get("router"), branch=decision.get("branch") or "",
                anchors=(ctx.get("router").anchors() if ctx.get("router") else None),
                story=bool(ctx.get("story")),
                preempt=(decision.get("action") == "抢占"),
                place_close=place_close,
                frame_labels=ctx.get("frame_labels") or None)
        except Exception as e:
            res = {"ok": False, "skip": True, "error": str(e),
                   "prompt_text": "", "usage": {}, "latency": 0}
        def done():
            if not self._alive:
                return
            self._set_test_running(False)
            self.test_status.configure(
                text="实测完成 · %.2fs" % (res.get("latency") or 0),
                fg="#8FC97A" if res.get("ok") else "#E08A7A")
            self._show_test_result(res, ctx)
        try:
            self.win.after(0, done)
        except Exception:
            pass

    def _show_test_result(self, res, ctx):
        top = tk.Toplevel(self.win)
        top.title("实测结果")
        top.configure(bg=self.BG)
        try:
            top.transient(self.win)
        except Exception:
            pass
        top.attributes("-topmost", True)
        self._center(top, 900, 720)
        top.lift()
        top.after(30, lambda: (top.lift(), ov.force_topmost(top)))

        ok = res.get("ok")
        usage = res.get("usage") or {}
        tok_in = int(usage.get("prompt_tokens") or 0)
        tok_out = int(usage.get("completion_tokens") or 0)
        tok_all = int(usage.get("total_tokens") or 0)
        est = int(res.get("prompt_tokens_est") or 0)
        ctx_k = ctx["cfg"]["vlm"].get("context_k", 0)
        story = bool(ctx.get("story"))
        sent = "不附图（故事格）" if story else "%s ×%d帧" % (
            ("%d×%d" % ctx["image_size"]) if ctx.get("image_size") else "—",
            int(res.get("frame_count") or 0))
        head = ("帧：%s · 原图 %s · 发送 %s · 闸门：%s\n"
                "耗时 %.2fs%s" % (
                    ctx.get("label", ""),
                    ("%d×%d" % ctx["source_size"]) if ctx.get("source_size") else "—",
                    sent,
                    ctx.get("gate") or "—",
                    res.get("latency") or 0,
                    (" · %s 输入 %s / 输出 %s / 共 %s" % (
                        "估算 tokens" if usage.get("estimated") else "实际 tokens",
                        format(tok_in, ","), format(tok_out, ","), format(tok_all, ",")))
                    if tok_all else " · 未返回 usage"))
        if est:
            head += "\n估算请求 ≈%d tok / 上下文 %sK（系统记忆+聊天记录 ≈%d tok · %d 轮对话）" % (
                est, ctx_k, ctx.get("mem_tokens", 0), ctx.get("history_turns", 0))
        if res.get("space"):
            head += "\n空间：%s" % res.get("space")
        if res.get("demo"):
            head += "\n演示模式：没有调用模型，这句话来自本地台词池。"
        if res.get("error"):
            head += "\n错误：" + str(res.get("error"))
        head += "\n实测只走一次模型，不会写进记忆。这里展示的是模型原始输出，播出层只去标签和整句外套的引号。"

        self._lab(top, "这一次实际发出去的东西", color=self.ACC, size=11, bold=True).pack(
            anchor="w", padx=18, pady=(12, 2))
        tk.Label(top, text=head, bg=self.BG, fg=self.DIM, font=(self._F, 9),
                 anchor="w", justify="left", wraplength=840).pack(
            anchor="w", padx=18, pady=(0, 8))

        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        def make_tab(name, content):
            fr = tk.Frame(nb, bg=self.CARD)
            t = tk.Text(fr, bg=self.CARD, fg=self.FG, relief="flat", bd=0, padx=10, pady=8,
                        font=(self._F, 10), wrap="word")
            t.insert("1.0", content or "")
            vs = tk.Scrollbar(fr, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=vs.set)
            t.pack(side="left", fill="both", expand=True)
            vs.pack(side="right", fill="y")
            nb.add(fr, text=name)
            return t

        result_text = []
        if ok:
            result_text.append("这一格开口：%s" % ("否" if res.get("skip") else "是"))
            if res.get("reason"):
                result_text.append("理由：%s" % res.get("reason"))
            result_text.append("情绪：%s" % (res.get("mood") or "—"))
            result_text.append("空间：%s" % (res.get("space") or "—"))
            result_text.append("引用：%s" % (res.get("ref") or "—"))
            result_text.append("步伐：%s" % (res.get("step") or "—"))
            result_text.append("心动：%s" % ("是" if res.get("heart") else "否"))
            result_text.append("记账：%s" % (res.get("account") or "—"))
            result_text.append("依据：%s" % (res.get("evidence") or "—"))
            ck = res.get("checks") or {}
            a1 = ck.get("a1") or {}
            a4 = ck.get("a4") or {}
            rd = ck.get("reading") or {}
            sm = ck.get("small") or {}
            st = ck.get("stock") or {}
            en = ck.get("entry") or {}
            ct = ck.get("count") or {}
            sp = ck.get("stop") or {}
            stp = ck.get("step") or {}
            pl = ck.get("place") or {}
            a1_how = a1.get("how") or "—"
            if ck.get("a1_soft"):
                a1_how += "（软过）"

            def _flag(check, soft_key):
                if not check:
                    return "—"
                return "过" if check.get("ok") else ("软过" if ck.get(soft_key) else "未过")

            result_text.append("判据：%s · A1=%s · A4=%s · 读字=%s · 小物=%s · 套话=%s · 进去=%s · 数量=%s · 停下=%s · 踩=%s · 收尾=%s · 重说=%s" % (
                "通过" if ck.get("ok") else "未过",
                a1_how,
                "在他" if a4.get("ok") else "在画面",
                _flag(rd, "reading_soft"),
                _flag(sm, "small_soft"),
                _flag(st, "stock_soft"),
                _flag(en, "entry_soft"),
                _flag(ct, "count_soft"),
                _flag(sp, "stop_soft"),
                _flag(stp, "step_soft"),
                _flag(pl, "place_soft"),
                "是" if res.get("retried") else "否"))
            lines = res.get("lines") or ([res.get("line")] if res.get("line") else [])
            result_text.append("台词（%d 句）：" % len(lines))
            for i, one in enumerate(lines, 1):
                result_text.append("%d. %s" % (i, one))
        else:
            result_text.append("没有拿到一句可用的话。")
            if res.get("raw"):
                result_text.append("\n原始返回：\n" + str(res.get("raw")))
        make_tab("结果", "\n".join(result_text))
        make_tab("完整请求", res.get("prompt_text") or "（没有拼出请求）")
        make_tab("模型原始返回", res.get("raw") or res.get("error") or "（无）")

        bar = tk.Frame(top, bg=self.BG)
        bar.pack(fill="x", padx=18, pady=10)
        line = (res.get("line") or "").strip()
        if ok and line:
            self._button(bar, "发到字幕", lambda: self._send_to_subtitle(line), primary=True).pack(
                side="left", padx=(0, 6))
        self._button(bar, "复制提示词",
                     lambda: self._copy_text(top, res.get("prompt_text") or "")).pack(side="left", padx=(0, 6))
        self._button(bar, "再测一次", lambda: (top.destroy(), self._test_current())).pack(
            side="left", padx=(0, 6))
        self._button(bar, "关闭", top.destroy).pack(side="right")

    def _send_to_subtitle(self, line):
        try:
            dur = self._float(self.var_dur, 5, 1, 20)
            if self.var_auto_duration.get() and len(line) > 24:
                dur = min(14.0, dur + (len(line) - 24) * 0.12)
            self.app.overlay.speak(line, self.var_name.get().strip() or "她", dur)
        except Exception:
            pass

    # ---------- 更多 ----------

    def _build_more_page(self):
        outer = self.pages["more"]
        canvas = tk.Canvas(outer, bg=self.BG, highlightthickness=0)
        vs = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.configure(yscrollcommand=vs.set)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        def wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        c = self.cfg
        f = self._block("字幕样式", inner)
        self.var_fs = tk.StringVar(value=str(c["overlay"].get("font_size", 0)))
        self.var_x = tk.StringVar(value=str(c["overlay"].get("x_pct", 25)))
        self.var_y = tk.StringVar(value=str(c["overlay"].get("y_pct", 66)))
        self.var_stream = tk.BooleanVar(value=bool(c["overlay"].get("stream", True)))
        self.var_stream_mode = tk.StringVar(
            value=STREAM_MODE_LABELS.get(c["overlay"].get("stream_mode", "replace"), "逐句替换"))
        self.var_stream_cps = tk.StringVar(value=str(c["overlay"].get("stream_cps", 20)))
        self.var_stream_pause = tk.StringVar(value=str(c["overlay"].get("stream_pause", 1.0)))
        self._pair(f, "字号 0=自动", self._spin(f, self.var_fs, 0, 64),
                   "左边距 %", self._spin(f, self.var_x, 0, 90))
        self._pair(f, "流式输出", self._check(f, self.var_stream, "校验后逐字播出"),
                   "显示方式", self._combo(f, self.var_stream_mode,
                                          list(STREAM_MODE_LABELS.values()), 12))
        self._pair(f, "流式速度（字/秒）", self._spin(f, self.var_stream_cps, 5, 60),
                   "句间停顿（秒）", self._spin(f, self.var_stream_pause, 0, 3, inc=0.05))
        self._line(f, "上边距 %", self._spin(f, self.var_y, 0, 95))
        self._button(f, "拖动定位 —— 点这里，然后把字幕拖到想要的位置", self.drag_subtitle).grid(
            row=self._next_row(f), column=0, columnspan=4, sticky="we", pady=(7, 2))
        self._hint(inner, "逐句替换：一句流式读完后消失，下一句再逐字出现；"
                          "整段累积：第一句保留，后面的往下接。字幕是独立浮层，不注入游戏。\n"
                          "注意：模型返回要先过 A1/A3/A4 三条判据，过了才开始逐字播出——"
                          "所以这里看到的是「校验后的流式」，不是边收边显示。")

        f = self._block("按键", inner)
        self._hot = dict(c.get("hotkeys") or {})
        self._cap = None
        self._hot_btns = {}
        self._hot_btns["shoot"] = self._hotkey_btn(f, "shoot")
        self._hot_btns["pause"] = self._hotkey_btn(f, "pause")
        self._hot_btns["prompts"] = self._hotkey_btn(f, "prompts")
        self._pair(f, "让她看看", self._hot_btns["shoot"], "暂停/继续", self._hot_btns["pause"])
        self._line(f, "打开提示词", self._hot_btns["prompts"])
        self._hint(inner, "点一下按钮，再直接按你想用的键（比如 5，或者 Ctrl+Alt+S）。"
                          "按完自动记录，再点一次可取消。")

        f = self._block("模型高级", inner)
        self.var_detail = tk.StringVar(
            value=DETAIL_LABELS.get(c["vlm"].get("detail", "high"), DETAIL_LABELS["high"]))
        self.var_think_style = tk.StringVar(
            value=THINK_STYLE_LABELS.get(c["vlm"].get("thinking_style", "auto"),
                                         THINK_STYLE_LABELS["auto"]))
        self.var_max_tokens = tk.StringVar(value=str(c["vlm"].get("max_tokens", 500)))
        self.var_timeout = tk.StringVar(value=str(c["vlm"].get("timeout", 30)))
        self.var_temperature = tk.StringVar(value=str(c["vlm"].get("temperature", 0.6)))
        self._pair(f, "视觉细节", self._combo(f, self.var_detail, list(DETAIL_LABELS.values()), 18),
                   "思考格式", self._combo(f, self.var_think_style, list(THINK_STYLE_LABELS.values()), 18))
        self._pair(f, "输出上限", self._spin(f, self.var_max_tokens, 32, 4096),
                   "超时（秒）", self._spin(f, self.var_timeout, 5, 180))
        self._line(f, "温度", self._spin(f, self.var_temperature, 0, 1.5, inc=0.05))
        self._hint(inner, "思考格式：自动会按服务商判断；遇到不认 thinking 的接口，"
                          "程序也会在 400 时自动剥掉再试。多句输出建议输出上限 ≥ 600；"
                          "如果思考把预算吃掉导致空回复，程序会自动关闭思考重试一次。")

        f = self._block("画面高级", inner)
        self.var_max_side = tk.StringVar(value=str(c["capture"].get("max_side", 2400)))
        self.var_quality = tk.StringVar(value=str(c["capture"].get("jpeg_quality", 88)))
        self.var_start_auto = tk.BooleanVar(value=bool(c["misc"].get("start_auto", False)))
        self._pair(f, "最长边", self._spin(f, self.var_max_side, 320, 4096),
                   "JPEG 质量", self._spin(f, self.var_quality, 40, 100))
        self._check(f, self.var_start_auto, "启动时自动开始看她").grid(
            row=self._next_row(f), column=0, columnspan=4, sticky="w", pady=(5, 2))
        self._hint(inner, "长边和 JPEG 质量直接影响图片 token。看不清小字时再调高。\n"
                          "v4 每格只发一张图（契约：没有上一张，也没有下一张）。"
                          "自动模式按「间隔」抓帧，抓完先过亮度/清晰度/重复三道闸门，"
                          "再交给调度层决定这一格要不要开口。")

        f = self._block("今天", inner)
        st = self.app.state
        self.stats_label = tk.Label(
            f, text="", bg=self.BG, fg="#8FC97A", font=(self._F, 9), anchor="w", justify="left")
        self.stats_label.grid(row=self._next_row(f), column=0, columnspan=4, sticky="we")
        self._refresh_stats()
        r = self._next_row(f)
        bf = tk.Frame(f, bg=self.BG)
        bf.grid(row=r, column=0, columnspan=4, sticky="w", pady=(7, 2))
        self._button(bf, "打开配置目录", lambda: self._open_path(cfgmod.DATA)).pack(side="left", padx=(0, 6))
        self._button(bf, "打开提示词目录", prompts.open_folder).pack(side="left", padx=(0, 6))
        self._button(bf, "打开记忆文件", self._open_memory_file).pack(side="left")
        self._hint(inner, "配置文件 %s　·　记忆 %s" % (cfgmod.CFG_PATH, cfgmod.MEMORY_PATH),
                   size=8, pady=(4, 12))

    def _refresh_stats(self):
        try:
            st = self.app.state
            self.stats_label.configure(
                text="今日 %d 次 · 输入 %s tok · 输出 %s tok · 会话 %d 次" % (
                    st.get("calls", 0), format(st.get("tok_in", 0), ","),
                    format(st.get("tok_out", 0), ","),
                    self.app.mem.sessions.get("count", 0)))
        except Exception:
            pass

    def _open_path(self, path):
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showinfo("打不开", str(e), parent=self.win)

    # ---------- 热键捕获 ----------

    def _hotkey_btn(self, parent, which):
        v = self._hot.get(which) or {"shoot": "F8", "pause": "F9", "prompts": "ALT+P"}.get(which, "")
        b = tk.Button(parent, text=hotkeys.hotkey_display(v), bg=self.CARD, fg=self.ACC,
                      activebackground="#2C2938", activeforeground=self.ACC,
                      relief="flat", bd=0, padx=8, pady=4, width=14, anchor="w",
                      font=(self._F, 10), cursor="hand2",
                      command=lambda: self._start_capture(which))
        b._value = v
        return b

    def _start_capture(self, which):
        if self._cap == which:
            self._stop_capture()
            return
        self._stop_capture()
        self._cap = which
        self._hot_btns[which].configure(text="按下按键…", bg="#3A2E12", fg="#FFD98A")
        try:
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass
        self.win.bind_all("<KeyPress>", self._on_capture)
        try:
            self._cap_mouse = hotkeys.MouseSideButtons(
                lambda b: self.win.after(0, lambda: self._apply_key(b)))
            self._cap_mouse.start()
        except Exception:
            self._cap_mouse = None

    def _apply_key(self, v):
        which = self._cap
        if which and v:
            btn = self._hot_btns[which]
            btn.configure(text=hotkeys.hotkey_display(v), bg=self.CARD, fg=self.ACC)
            btn._value = v
            self._hot[which] = v
        self._stop_capture()

    def _on_capture(self, ev):
        v = hotkeys.key_to_hotkey(ev)
        if not v:
            return "break"
        self._apply_key(v)
        return "break"

    def _stop_capture(self):
        if not self._cap:
            return
        try:
            btn = self._hot_btns[self._cap]
            btn.configure(text=hotkeys.hotkey_display(btn._value), bg=self.CARD, fg=self.ACC)
        except Exception:
            pass
        self._cap = None
        try:
            if self._cap_mouse:
                self._cap_mouse.stop()
                self._cap_mouse = None
        except Exception:
            pass
        try:
            self.win.unbind_all("<KeyPress>")
        except Exception:
            pass

    # ---------- 框选 / 窗口 ----------

    def _region_text(self):
        c = self.cfg.get("capture", {})
        r = c.get("region") or {}
        if c.get("mode") == "region" and int(r.get("width", 0) or 0) >= 32:
            return "区域 %d×%d @ %d,%d" % (r["width"], r["height"], r["left"], r["top"])
        return "现在截整个窗口"

    def pick_region(self):
        self.win.withdraw()
        self.app.root.after(260, lambda: ov.RegionPicker(
            self.app.root, self._picked, self._pick_cancel))

    def _picked(self, region):
        self._region = region
        self.var_region_info.set("区域 %d×%d @ %d,%d" % (
            region["width"], region["height"], region["left"], region["top"]))
        # 框完立刻写盘：不点保存就关窗口，选区也不丢
        c = self.cfg
        c["capture"]["region"] = region
        c["capture"]["mode"] = "region"
        cfgmod.save(c)
        self._update_scale_hint()
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        ov.force_topmost(self.win)

    def _pick_cancel(self):
        self.win.deiconify()
        self.win.lift()
        ov.force_topmost(self.win)

    def use_window(self):
        self._region = None
        self.var_region_info.set("改用整个窗口")
        # 选窗口也立刻写盘：和框选一样，不点保存就关程序也不丢。
        title = self.var_win.get().strip()
        if title:
            c = self.cfg
            c["capture"]["window_title"] = title
            c["capture"]["mode"] = "window"
            c["capture"]["region"] = {"left": 0, "top": 0, "width": 0, "height": 0}
            try:
                cfgmod.save(c)
            except Exception:
                pass

    def refresh_windows(self):
        try:
            wins = capture.list_windows()
        except Exception:
            wins = []
        titles = [t for _, t, _ in wins
                  if "游戏搭子" not in t and "她看见的世界" not in t and "HerWorld" not in t]
        try:
            self.combo["values"] = titles
        except Exception:
            pass
        if titles and not self.var_win.get():
            self.var_win.set(titles[0])

    # ---------- 字幕拖动 / 日志 ----------

    def drag_subtitle(self):
        self.win.withdraw()
        self.app.overlay.begin_drag(
            "这条街的灯，像有人提前开好的。",
            self.var_name.get().strip() or "她",
            self._on_dragged)

    def _on_dragged(self, xp, yp):
        self.var_x.set(str(xp))
        self.var_y.set(str(yp))
        c = self.cfg
        c["overlay"]["x_pct"] = xp
        c["overlay"]["y_pct"] = yp
        cfgmod.save(c)
        self.app.overlay.cfg = c
        self.win.deiconify()
        self.win.lift()
        ov.force_topmost(self.win)

    def open_log(self):
        import subprocess
        p = os.path.join(str(cfgmod.DATA), "日志.txt")
        try:
            if not os.path.exists(p):
                open(p, "w", encoding="utf-8").close()
            subprocess.Popen(["notepad.exe", p])
        except Exception:
            pass

    # ---------- 保存 ----------

    def _int(self, var, default, lo=None, hi=None):
        try:
            n = int(float(var.get()))
        except Exception:
            return default
        if lo is not None:
            n = max(lo, n)
        if hi is not None:
            n = min(hi, n)
        return n

    def _float(self, var, default, lo=None, hi=None):
        try:
            n = float(var.get())
        except Exception:
            return default
        if lo is not None:
            n = max(lo, n)
        if hi is not None:
            n = min(hi, n)
        return n

    def _apply_to_cfg(self):
        return cfgmod.save(self._fill_cfg(self.cfg, persona=True, hotkeys=True))

    def _save_typed_key(self):
        """输入框里已经填了 key、但没点「保存到本机」就点保存/关闭时，也一并存下。"""
        try:
            key = self.var_key.get().strip()
            if not key:
                return
            ok, _msg = keystore.put(self._provider_id(), key)
            if ok:
                self.var_key.set("")
                self._apply_conn_to_cfg()
                self.app.key = cfgmod.api_key(self._conn_cfg())
                self._refresh_key_ui()
                self._update_key_hint()
        except Exception:
            pass

    def save(self, close=True):
        self._save_typed_key()
        if not self._save_all_prompts(show=False):
            if close:
                messagebox.showerror("保存失败", "提示词文件写不进去，先别关。", parent=self.win)
                return
        try:
            c = self._apply_to_cfg()
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.win)
            return

        self.app.key = cfgmod.api_key(c)
        self.app.overlay.cfg = c
        try:
            self.app.mem.persist = bool(c["memory"].get("persist", True))
            self.app.mem.trim(c)
        except Exception:
            pass
        try:
            self.app.reload_hotkeys()
        except Exception:
            pass
        try:
            self.app.restart_timer()
        except Exception:
            pass
        try:
            self._refresh_stats()
            self._refresh_memory_page()
            self._refresh_persona_list()
            self._update_key_hint()
        except Exception:
            pass

        if close:
            self._alive = False
            try:
                self.app._settings_closed()
            except Exception:
                pass
            self._stop_capture()
            try:
                self.win.unbind_all("<MouseWheel>")
            except Exception:
                pass
            self.win.destroy()
            self.app._set_status("● 设置已保存")
        else:
            self.save_status.configure(text="已保存 · 下一次抓拍生效", fg="#8FC97A")
            self.app._set_status("● 设置已保存")

    def _cancel(self):
        self._alive = False
        self._stop_capture()
        try:
            self.app._settings_closed()
        except Exception:
            pass
        try:
            self.win.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def _close_win(self):
        # 直接关窗口 = 保存。提示词和配置都是本地的，不该因为忘了点按钮就丢。
        try:
            self.save(close=True)
        except Exception:
            try:
                self._alive = False
                self.win.destroy()
            except Exception:
                pass
