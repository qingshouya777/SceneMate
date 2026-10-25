# -*- coding: utf-8 -*-
"""游戏搭子 · v4

5 秒一格：截图 → 信号层 → 调度层 →（该开口才）叫模型 → 过 A1/A3 → 上屏。
提示词全部在 prompts/ 里，随时可改；契约是 12_契约.txt。

启动: python main.py
自检: python main.py --selftest
只看提示词不调模型: python main.py --selftest --no-call
"""

import ctypes
import datetime
import json
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk

_home = (os.environ.get("HERWORLD_HOME") or "").strip()
if _home and os.path.isdir(_home):
    BASE = os.path.abspath(_home)
elif getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import brain  # noqa: E402
import branding  # noqa: E402
import capture  # noqa: E402
import config as cfgmod  # noqa: E402
import contract  # noqa: E402
import hotkeys  # noqa: E402
import memory  # noqa: E402
import overlay as ov  # noqa: E402
import prompts  # noqa: E402
import router as routermod  # noqa: E402
import signals  # noqa: E402
from textutil import stance_text  # noqa: E402

DATA = str(getattr(cfgmod, "DATA", BASE))
LOG_PATH = os.path.join(DATA, "日志.txt")
PLAN_PATH = os.path.join(DATA, "调度.txt")
STATE_PATH = os.path.join(DATA, "state.json")


class StreamDisplay:
    """把已通过校验的正文按句切开，逐字播出（D7：播出时钟和调用时钟分开）。"""

    def __init__(self, app, q, name, done_event, story=False):
        self.app = app
        self.q = q
        self.name = name
        self.done = done_event
        o = (app.cfg.get("overlay") or {})
        self.mode = str(o.get("stream_mode") or "accumulate")
        self.cps = max(5.0, float(o.get("stream_cps", 20) or 20))
        self.pause = max(0.0, float(o.get("stream_pause", 0.8) or 0.8))
        # 故事格用单独的字幕停留：故事一句接一句，不需要普通格那样停满 5 秒。
        self.hold = max(1.0, float(
            (o.get("story_duration", 2.0) if story else o.get("duration", 5)) or
            (2.0 if story else 5)))
        self.max_sentences = int((app.cfg.get("behavior") or {}).get("sentences_per_take", 3) or 0)
        self.queued = 0
        self.raw = ""
        self.pending = []
        self.current = ""
        self.typed = ""
        self.paragraph = ""
        self.paused = False
        self.pause_until = 0.0
        self.stream_done = False
        self.hold_until = 0.0
        self.finished = False
        self.acc = 0.0
        self.last_tick = time.time()
        self.last_render = ""
        self.affection = 0       # 这一格的粉字提示（0 = 不显示）
        self.mood = ""           # 正面情绪才显示（开心/害羞/感动）

    def start(self):
        self._tick()

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        try:
            self.done.set()
        except Exception:
            pass

    def _clear_and_finish(self):
        try:
            self.app.overlay.clear()
        except Exception:
            pass
        self._finish()

    def _drain(self):
        while True:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                return
            if kind == "delta":
                self.raw += payload or ""
            elif kind == "end":
                self.stream_done = True
                self._extract()

    def _clean(self, text):
        """只做最小的清理：去标签、去引号、去首尾标点。

        这里原来还做两件事，都删了：
        - `clean_call_everywhere`：她叫了"招财宝"就把这三个字从句子里抠掉。
          那正好抠掉契约最看重的"落点在人"——"招财宝，你听见没"会变成"你听见没"，
          再短一点整句就没了。"别老叫"是提示词的事（05/06_称呼），不该在播出前动刀。
        - `is_sample_copy`：句子像人设示例就整句丢掉。她越学示例的语气越容易被判抄袭，
          等于惩罚"说得像她"。人设示例默认是空的，这个机制一直没起作用，删掉。
        """
        s = brain.clean_text((text or "").strip())
        return s.strip("，,。.、：: 　")

    def _extract(self):
        while self.raw:
            m = re.search(r"[。！？!?…\n]", self.raw)
            if not m:
                break
            piece = self.raw[:m.end()]
            self.raw = self.raw[m.end():].lstrip("”’\"' \r\n")
            for one in brain.split_sentences(piece, self.app.cfg):
                if self.max_sentences and self.queued >= self.max_sentences:
                    return
                one = self._clean(one)
                if one:
                    self.pending.append(one)
                    self.queued += 1

    def _display_text(self):
        if self.mode == "replace":
            return self.typed.strip()
        parts = []
        if self.paragraph.strip():
            parts.append(self.paragraph.strip())
        if self.typed.strip():
            parts.append(self.typed.strip())
        return chr(10).join(parts)

    def _render(self):
        text = self._display_text()
        if not text or text == self.last_render:
            return
        self.last_render = text
        try:
            self.app.overlay.stream_update(text, self.name, affection=self.affection,
                                           mood=self.mood)
        except Exception:
            pass

    def _schedule(self):
        if self.finished:
            return
        try:
            self.app.root.after(35, self._tick)
        except Exception:
            pass

    def _tick(self):
        if self.finished:
            return
        now = time.time()
        self._drain()
        if self.finished:
            return
        if self.hold_until:
            if now >= self.hold_until:
                try:
                    self.app.overlay.stream_finish(self._display_text(), self.name, 0,
                                                   affection=self.affection, mood=self.mood)
                except Exception:
                    pass
                self._finish()
                return
            self.last_tick = now
            self._schedule()
            return

        if not self.current and not self.paused and self.pending:
            self.current = self.pending.pop(0)
            self.typed = ""
            self.acc = 0.0
            self.last_tick = now

        if self.current:
            if self.paused:
                if now < self.pause_until:
                    self.last_tick = now
                    self._schedule()
                    return
                if self.pending:
                    if self.mode != "replace" and self.current.strip():
                        self.paragraph = (self.paragraph +
                                          (chr(10) if self.paragraph.strip() else "") +
                                          self.current.strip()).strip()
                    self.current = self.pending.pop(0)
                    self.typed = ""
                    self.acc = 0.0
                    self.paused = False
                    self.last_tick = now
                elif self.stream_done:
                    self.hold_until = now + self.hold
                    self._render()
                    self.last_tick = now
                    self._schedule()
                    return
                else:
                    self.last_tick = now
                    self._schedule()
                    return
            if len(self.typed) < len(self.current):
                elapsed = max(0.0, now - self.last_tick)
                self.last_tick = now
                self.acc += self.cps * elapsed
                n = int(self.acc)
                if n > 0:
                    self.acc -= n
                    n = min(n, 4, len(self.current) - len(self.typed))
                    self.typed += self.current[len(self.typed):len(self.typed) + n]
                    self._render()
                if len(self.typed) >= len(self.current):
                    self.paused = True
                    self.pause_until = time.time() + self.pause
                    self._render()
                self._schedule()
                return
            self.paused = True
            self.pause_until = time.time() + self.pause
            self._render()
            self._schedule()
            return

        if self.stream_done:
            if self.paragraph.strip():
                self.hold_until = now + self.hold
                self._render()
                self.last_tick = now
                self._schedule()
                return
            self._clear_and_finish()
            return
        self.last_tick = now
        self._schedule()


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.key = cfgmod.api_key(cfg)
        self.state = self._load_state()
        self.busy = False
        self.auto_on = False
        self.timer = None
        self.last_manual = 0.0
        self.settings_win = None
        self._resume_auto_after_settings = False
        self.prev_sig = None
        self.skip_streak = 0
        self.session_aff = 0
        self._story_active = False
        self._story_just_ended = False

        self.mem = memory.Memory(cfgmod.MEMORY_PATH, cfg)
        self.mem.start_session(cfg)

        base = signals.read_flow_baseline()
        self.flow_floor = (base or {}).get("floor")
        if not (cfg.get("router") or {}).get("flow_floor") and self.flow_floor:
            cfg.setdefault("router", {})["flow_floor"] = self.flow_floor
        self.router = routermod.Router(cfg)
        # 隔了半小时以上再打开 = 新的一次，不该接着上次的话题和欠账。
        # 记忆（说过的话）仍然保留；这里只丢掉"正在聊的那件事"。
        # 必须同时落盘：否则这次没开口就退出，下次 5 分钟后打开又会把旧线程捡回来。
        router_state = self.mem.load_router()
        if self.mem.session_gap and self.mem.session_gap > 1800:
            router_state = {}
            self.mem.save_router({})
        self.router.restore(router_state)

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(branding.APP_NAME)
        branding.apply(self.root)

        self.overlay = ov.SubtitleOverlay(self.root, cfg)
        self.control = None
        if cfg.get("misc", {}).get("control_bar", True):
            self.control = ov.ControlBar(
                self.root, cfg,
                on_shoot=lambda: self.request(manual=True),
                on_toggle=self.toggle_auto,
                on_settings=lambda: self.open_settings("main"),
                on_quit=self.quit,
                on_pick=self.pick_region,
                on_prompts=lambda: self.open_settings("prompts"),
            )
            self.control.set_state("○ 暂停", auto_on=False)

        self.hk = hotkeys.HotkeyThread(cfg.get("hotkeys", {}), self._on_hotkey)
        self.hk.start()
        self.mouse = hotkeys.MouseSideButtons(self._on_mouse)
        self.mouse.start()

        c0 = self.cfg.get("capture", {}) or {}
        r0 = c0.get("region") or {}
        has_target = bool(c0.get("window_title")) or (
            c0.get("mode") == "region" and int(r0.get("width", 0) or 0) >= 32)
        if has_target and cfg.get("misc", {}).get("start_auto", False):
            self.root.after(600, self.set_auto_on)
        else:
            self.root.after(500, lambda: self._set_status("○ 待机 · 点「开始」或按热键"))
        if not has_target:
            self.root.after(700, lambda: self.open_settings("main"))

    # ---------- 状态 ----------

    def _load_state(self):
        """只存用量。原来那套 affinity（好感度）从来没被读过，日志清一色 ±0，删了。"""
        d = {"day": "", "calls": 0, "tok_in": 0, "tok_out": 0, "affection": 0}
        try:
            raw = json.loads(open(STATE_PATH, encoding="utf-8").read())
            if isinstance(raw, dict):
                d.update({k: raw.get(k, d[k]) for k in d})
        except Exception:
            pass
        return d

    def _save_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
        except Exception:
            pass

    def _bump_usage(self, usage):
        today = datetime.date.today().isoformat()
        if self.state.get("day") != today:
            self.state.update({"day": today, "calls": 0, "tok_in": 0, "tok_out": 0})
        u = usage or {}
        self.state["calls"] += 1
        self.state["tok_in"] += int(u.get("prompt_tokens") or 0)
        self.state["tok_out"] += int(u.get("completion_tokens") or 0)
        self._save_state()

    def _log(self, scene, mood, note=""):
        ts = datetime.datetime.now().strftime("%m.%d %H:%M")
        line = "%s · %s · %s%s" % (ts, scene or "—", mood or "—",
                                   (" · " + note) if note else "")
        _append_line(LOG_PATH, line)

    def _plan(self, decision, extra=""):
        """调度留痕：每 tick 一行，沉默也要能归因。"""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        act = decision.get("activity") or {}
        th = decision.get("thread") or {}
        tinfo = (" 线:%s(%s/%s)" % (th.get("anchor") or "-",
                                   th.get("remaining") or 0, th.get("total") or 0)) if th else ""
        line = "%s t%-3d %-4s %-4s | %s%s |%s 势能%d | 动度 %s" % (
            ts, decision.get("tick", 0), decision.get("action", "?"),
            routermod.BRANCH_CN.get(decision.get("branch") or "", "—"),
            "; ".join(decision.get("why") or []) or "无强信号",
            (" | " + extra) if extra else "",
            tinfo,
            decision.get("pot", 0),
            ("mean %.0f~%.0f open %.0f~%.0f" % (act["mean"][0], act["mean"][1],
                                                act["open"][0], act["open"][1])
             if act.get("mean") else "—"))
        _append_line(PLAN_PATH, line)
        return line

    def _set_status(self, text):
        if self.control:
            try:
                self.control.set_state(text, auto_on=self.auto_on)
            except Exception:
                pass

    def _status_async(self, text):
        self._ui(lambda: self._set_status(text))

    def _ui(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    # ---------- 触发 ----------

    def toggle_auto(self):
        if self.auto_on:
            self.set_auto_off()
        else:
            self.set_auto_on()

    def set_auto_on(self):
        w = self.settings_win
        if w is not None and getattr(w, "_alive", False):
            self._set_status("○ 先关掉设置再开始")
            return
        self.auto_on = True
        # 选区是旧的/游戏没开时，抓到的会是桌面。给一句明示，别让人以为是"没输出"。
        tip = ""
        c = self.cfg.get("capture") or {}
        title = (c.get("window_title") or "").strip()
        if title and not capture.find_window(title):
            tip = " · 没找到「%s」，现在按选区抓" % title
        self._set_status(("● 看着她" if self.key else "● 演示 · 未读到 key") + tip)
        if tip:
            self._log("—", "开始", "没找到游戏窗口，抓到的是选区里的其他画面")
        self.schedule()

    def set_auto_off(self):
        # 点暂停的这一刻，把这一段攒的好感结一次账（落盘 + 记一行）。
        got = int(getattr(self, "session_aff", 0) or 0)
        if got:
            total = int(self.state.get("affection") or 0)
            self._save_state()
            self._log("—", "好感", "这一段 +%d · 累计 %d" % (got, total))
            self.session_aff = 0
        self.auto_on = False
        if self.timer:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
            self.timer = None
        self._set_status("○ 暂停")

    def restart_timer(self):
        if not self.auto_on:
            return
        if self.timer:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
            self.timer = None
        self.schedule()

    def _in_story(self):
        t = self.router.thread or {}
        return (t.get("branch") == "lang" and int(t.get("remaining") or 0) > 0)

    def schedule(self):
        if not self.auto_on:
            return
        c = self.cfg.get("capture", {})
        story_pace = self._in_story()
        if story_pace:
            base = float(c.get("story_interval", 2.5) or 2.5)
            jit = float(c.get("story_jitter", 0.8) or 0.8)
            floor = 1.0
        else:
            base = float(c.get("interval", 5) or 5)
            jit = float(c.get("jitter", 3) or 3)
            floor = 3.0
        delay = max(floor, base + random.uniform(-jit, jit))
        self.timer = self.root.after(int(delay * 1000), self._auto_tick)

    def _auto_tick(self):
        if self.auto_on:
            self.request(manual=False)
        self.schedule()

    def _on_mouse(self, button):
        mapping = {str(v).upper(): k for k, v in (self.cfg.get("hotkeys") or {}).items() if v}
        act = mapping.get(str(button).upper())
        if act:
            self._on_hotkey(act)

    def reload_hotkeys(self):
        for obj in (getattr(self, "hk", None), getattr(self, "mouse", None)):
            try:
                obj.stop()
            except Exception:
                pass
        time.sleep(0.25)
        self.hk = hotkeys.HotkeyThread(self.cfg.get("hotkeys", {}), self._on_hotkey)
        self.hk.start()
        self.mouse = hotkeys.MouseSideButtons(self._on_mouse)
        self.mouse.start()
        time.sleep(0.25)
        return dict(self.hk.ok)

    def _on_hotkey(self, name):
        if name == "shoot":
            self.request(manual=True)
        elif name == "pause":
            self.root.after(0, self.toggle_auto)
        elif name == "clear":
            self.root.after(0, self.overlay.clear)
        elif name == "prompts":
            self.root.after(0, lambda: self.open_settings("prompts"))

    def request(self, manual=False):
        if self.busy:
            return
        if manual:
            now = time.time()
            gap = float(self.cfg.get("behavior", {}).get("manual_min_gap", 3))
            if now - self.last_manual < gap:
                return
            self.last_manual = now
        self.busy = True
        threading.Thread(target=self._work, args=(manual,), daemon=True).start()

    # ---------- 抓帧 ----------

    def _resolve_target(self, manual, c):
        region = c.get("region") or {}
        use_region = (c.get("mode", "region") == "region"
                      and int(region.get("width", 0) or 0) >= 32)
        if use_region:
            cx = int(region["left"]) + int(region["width"]) // 2
            cy = int(region["top"]) + int(region["height"]) // 2
            if not manual and not capture.point_is_foreground(cx, cy):
                return None, "region_bg", "选区"
            return {"kind": "region", "region": region}, "", "选区"
        title = c.get("window_title", "")
        found = capture.find_window(title)
        if not found:
            return None, "no_window", title
        hwnd, real_title, _ = found
        if not manual and not capture.is_foreground(hwnd):
            return None, "window_bg", real_title
        return {"kind": "window", "hwnd": hwnd, "title": real_title}, "", real_title

    def _grab_target(self, target):
        if target["kind"] == "region":
            return capture.grab_region(target["region"])
        return capture.grab(target["hwnd"], target["title"])

    # ---------- 主流程 ----------

    def _work(self, manual):
        try:
            if not manual and getattr(self.overlay, "_visible", False):
                return
            c = self.cfg.get("capture", {})
            target, why, _label = self._resolve_target(manual, c)
            if target is None:
                if why == "no_window":
                    self._status_async("没找到窗口，去设置里框选一块区域")
                    self._log("—", "无窗口")
                else:
                    self._status_async("● 待机 · " + (
                        "那块区域不在前台" if why == "region_bg" else "游戏不在前台"))
                return

            hide = target["kind"] == "region" or (
                target["kind"] == "window" and not capture.WGC_OK)
            if hide:
                self._ui(self._hide_chrome)
                time.sleep(0.18)
            img, err = None, ""
            try:
                img, err = self._grab_target(target)
            finally:
                if hide:
                    self._ui(self._show_chrome)
            if img is None:
                self._status_async("截不到画面")
                self._log("截帧失败", "—", (err or "")[:40])
                return

            cur_sig = signals.signature(img)      # 一张图只算一次签名，闸门和信号层共用
            ok, reason, _ = capture.frame_gate(img, self.cfg, self.prev_sig, sig=cur_sig)
            if not ok and ("黑屏" in reason or "模糊" in reason):
                # 这一帧根本没送出去（太黑/太糊），不是她做了什么。
                # 文案必须写成"没看"，不能写成像是她的动作。
                self._status_async("这一格没看 · " + reason.split("（")[0])
                self._log("—", "画面不清", reason + "（没送模型）")
                return
            static = (not ok) and ("几乎一样" in reason)

            # 信号层（B）：一帧只算一次，flow 用上一格的签名
            sig = signals.analyze(img, self.prev_sig, self.flow_floor, cur_sig=cur_sig)
            self.prev_sig = sig["sig"]

            # 调度层（C）
            decision = self.router.tick(sig)
            if static and decision.get("action") == "开" \
                    and decision.get("branch") in ("lang", "aesthetic", "follow"):
                decision["action"] = "留白"
                decision["why"] = (decision.get("why") or []) + ["画面没动，不值得新开一个话题"]
            self._plan(decision)

            # 故事模式：这一格是她自己起的头，或者正在把那个头讲完。
            # 故事模式下不给她看画面（图都不发），否则一句话就被画面拽回去。
            story = (decision.get("branch") == "lang") or (
                decision.get("action") == "续锚"
                and (self.router.thread or {}).get("branch") == "lang")
            if story:
                self._story_active = True
                self._story_just_ended = False
            elif self._story_active:
                # 上一格还在故事里，这一格已经不在：记一个"故事出口"，
                # 等到下一次真的开口时再用（留白格也保留这个标记）。
                self._story_active = False
                self._story_just_ended = True

            if decision["action"] == "留白":
                self._status_async("○ 留白 · " + (
                    routermod.BRANCH_CN.get(decision.get("branch") or "", "") or "没动"))
                if sig:
                    self.mem.add_silent(
                        self.cfg, "", "", "", "留白",
                        meta={"ring": sig["ring"]["label"], "branch": "",
                              "anchor": "", "story": bool(story)})
                return

            use_stream = bool(self.cfg.get("overlay", {}).get("stream", True))
            story_exit = bool(self._story_just_ended)
            if story_exit:
                # 故事里攒下的问/求是叙述的一部分，不带到眼前。
                # 这一格模型新加的请求会在下面正常入队（比如"带我去水边"）。
                self.router.pending = []
            th = self.router.thread or {}
            # 看一个地方的最后一格：请求去看 → 看一会 → 走。
            place_close = bool(th and not story and not story_exit
                               and int(th.get("remaining") or 0) <= 1
                               and (th.get("branch") or "") in routermod.PLACE_BRANCHES)
            if story_exit:
                # 第一格回到眼前：不给故事历史、不给"接住上一句"（validate 里跳过 A1），
                # 否则上一句"所以现在拎东西，我都把袋口攥紧"会被它接到现在，攥出手汗。
                prev_lines = []
                history_since = time.time() + 5
            elif story:
                # 故事一开始就切掉之前的聊天记录：上一段招牌/气泡的话题不能变成故事素材。
                prev_lines = self.mem.reference_lines(
                    3, max_age_sec=1800, include_story=True)
                history_since = (self.router.thread or {}).get("started_at") or None
            else:
                prev_lines = self.mem.reference_lines(3, max_age_sec=1800)
                history_since = None
            mem_ctx = self.mem.build_prompt_memory(self.cfg, history_since=history_since)
            pm = prompts.load_all()
            jpeg, size = capture.prepare(
                img, c.get("max_side", 2400), c.get("jpeg_quality", 88), c.get("scale", 1.0))
            note = brain.current_note_for(self.cfg, pm, prev_lines,
                                          story=story, story_exit=story_exit)

            force_after = int((self.cfg.get("behavior") or {}).get(
                "force_speak_after_skips", 4) or 0)
            force = bool(force_after and self.skip_streak >= force_after)

            def build_system(nudge):
                return brain.build_prompt(
                    self.cfg, system_memory=mem_ctx["digest"], prompt_map=pm,
                    retry_note=nudge, recent_lines=prev_lines, decision=decision,
                    sig=sig, router=self.router, force_speak=force,
                    story=story, story_exit=story_exit)

            disp = None
            q = None
            done = None
            if use_stream:
                q = queue.Queue()
                done = threading.Event()
                nm = self._name() + ("·演示" if not self.key else "")
                disp = StreamDisplay(self, q, nm, done, story=story)
                self._ui(disp.start)

            self._ui(lambda: self.overlay.show_thinking(self._name()))
            res = brain.speak(
                self.cfg, self.key, build_system, mem_ctx["history"], note, jpeg,
                prev_lines=prev_lines, image_size=size,
                branch=decision.get("branch") or "",
                anchors=self.router.anchors(), with_image=not story, story=story,
                force_speak=force, story_exit=story_exit, place_close=place_close,
                preempt=(decision.get("action") == "抢占"),
                on_delta=(lambda text: q.put(("delta", text))) if use_stream else None)

            self._bump_usage(res.get("usage"))

            checks = res.get("checks") or {}
            a1 = checks.get("a1") or {}
            notes = ["%.1fs" % (res.get("latency") or 0)]
            tok = (res.get("usage") or {}).get("total_tokens")
            if tok:
                notes.append("%dtok" % tok)
            elif res.get("demo"):
                notes.append("演示")
            notes.append("mem≈%.1fK" % (mem_ctx["tokens"] / 1024.0))
            notes.append("引:%s" % (a1.get("how") or "—"))
            if checks.get("a1_soft"):
                notes.append("A1软过")
            if checks.get("untagged_soft"):
                notes.append("格式缺")
            if checks.get("small_soft"):
                notes.append("小物软过")
            if checks.get("stock_soft"):
                notes.append("套话软过")
            if checks.get("count_soft"):
                notes.append("数量软过")
            if place_close and not (checks.get("place") or {}).get("ok", True):
                notes.append("收尾未过")
            if (decision.get("branch") or "") == "sign":
                rd = checks.get("reading") or {}
                notes.append("读字:%s" % ("过" if rd.get("ok") else "软过"))
            if checks.get("ok") is False:
                notes.append("未过判据")
            if res.get("retried"):
                notes.append("重说")

            if not res.get("ok") or res.get("skip") or not res.get("lines"):
                reason = (res.get("reason") or res.get("error") or "没说")[:24]
                self.skip_streak += 1
                self._status_async("○ 没说 · " + reason)
                self._log(routermod.BRANCH_CN.get(decision.get("branch") or "", "—"),
                          "沉默", reason + " " + " ".join(notes))
                # 把失败那次的原始返回记一行，否则线上只能看到"沉默"，看不出是模型没写引用
                raw = " ".join((res.get("raw") or "").split())[:220]
                if raw:
                    _append_line(PLAN_PATH, "    ↳ 原始返回：" + raw)
                self.mem.add_silent(self.cfg, "", "", "", reason,
                                    meta={"ring": sig["ring"]["label"],
                                          "branch": decision.get("branch") or "",
                                          "story": bool(story)})
                if disp:
                    q.put(("end", {}))
                    done.wait(2)
                else:
                    self._ui(self.overlay.clear)
                return

            self.skip_streak = 0
            if story_exit:
                # 只有真的说出一句，才算走出了故事；失败/沉默时保留标记，下一格继续"回到眼前"。
                self._story_just_ended = False
            line = res.get("line", "")
            mood = res.get("mood", "平静")

            # 情绪节流：连着两格都开心，这一格回到平静。
            # 模型很爱给"开心"（它会显示在字幕上、还加好感），实测 65 格里 21 格是开心。
            # 人不会一直高兴；连着第三格还笑，观众就觉得假。
            recent = self.mem.recent_moods(2)
            if mood == "开心" and len(recent) == 2 and all(m == "开心" for m in recent):
                mood = "平静"

            # 好感：她这一格"心动"了，就随机 +1..5（粉字提示在字幕下面）。
            # 判定交给模型（【态】心动=是/否），数额由代码掷骰子——她不能自己定价。
            aff = 0
            if res.get("heart") and contract.heart_counts(line, mood):
                aff = _roll_affection()
                self.state["affection"] = int(self.state.get("affection") or 0) + aff
                self.session_aff = getattr(self, "session_aff", 0) + aff
                self._save_state()
                notes.append("好感+%d" % aff)
            lines = res.get("lines") or ([line] if line else [])
            space = res.get("space") or ""

            # 线程推进 + 记账结算（A2 在 router 里）
            step = res.get("step") or ""
            ref = (res.get("ref") or "").strip()
            self.router.thread_advance(step)
            if self.router.thread:
                # 不拿引用词当话题锚：模型填的"引用"经常是动词（"认出来"），
                # 拿它当锚，下一格提示词就变成"你挂在嘴边的是：认出来"——等于没有话题。
                # 锚保持事件给的那个（"刚才那块字"），她说到哪儿由 stance 记。
                self.router.thread["stance"] = stance_text(lines)
            if story:
                # 故事里的"你吃香菜不"是叙述的一部分，不是眼前要兑现的账。
                # 记进 pending 的话，故事讲完它还会冒出来，把香菜带回水边。
                acc_notes = []
            else:
                acc_notes = self.router.apply_resolution(res)
            if acc_notes:
                notes.extend(acc_notes[:2])

            self.mem.save_router(self.router.snapshot())
            self.mem.add_entry(
                self.cfg, "", line, mood, space, meta={
                    "ring": sig["ring"]["label"],
                    "branch": decision.get("branch") or "",
                    "anchor": (self.router.thread or {}).get("anchor") or "",
                    "thread": step,
                    "intent": res.get("evidence") or "",
                    "pending": ";".join([p["text"] for p in self.router.pending][:2]),
                    "story": bool(story),
                })

            if use_stream and disp:
                disp.affection = aff
                disp.mood = mood
            ov_cfg = self.cfg.get("overlay", {})
            dur = float(ov_cfg.get("story_duration" if story else "duration",
                                   2.0 if story else 5.0) or (2.0 if story else 5.0))
            if use_stream and disp:
                q.put(("end", {}))
                cps = max(5.0, float(ov_cfg.get("stream_cps", 20) or 20))
                pause = max(0.0, float(ov_cfg.get("stream_pause", 0.8) or 0.8))
                est = (sum(len(x) for x in lines) / cps
                       + max(0, len(lines) - 1) * pause + dur + 4)
                done.wait(min(180.0, est + 10))
            else:
                if self.cfg.get("overlay", {}).get("auto_duration", True) and len(line) > 24:
                    dur = min(14.0, dur + (len(line) - 24) * 0.12)
                nm = self._name() + ("·演示" if res.get("demo") else "")
                self._ui(lambda: self.overlay.speak(line, nm, dur, affection=aff,
                                                    mood=mood))

            self._save_state()
            self._log(routermod.BRANCH_CN.get(decision.get("branch") or "", "—"),
                      mood, " ".join(notes))
            self._status_async("● 看着她" if self.key else "● 演示 · 未读到 key")

            if self.mem.needs_compress(self.cfg):
                self._maybe_compress(pm)
        except Exception as e:
            self._log("异常", "—", str(e)[:60])
            self._ui(self._show_chrome)
        finally:
            self.busy = False

    def _maybe_compress(self, pm):
        def done(info):
            if not info.get("ok"):
                return
            msg = "记忆整理 %d 条 → %d 字%s" % (
                info.get("folded", 0), info.get("digest_chars", 0),
                "（机械）" if info.get("fallback") else "")
            self._ui(lambda: self._log("—", "记忆", msg))

        try:
            self.mem.compress_async(self.cfg, self.key, pm, on_done=done, force=False)
        except Exception:
            pass

    def _name(self):
        return self.cfg.get("persona", {}).get("name", "她")

    def _hide_chrome(self):
        try:
            self.overlay.hide()
            if self.control:
                self.control.set_visible(False)
        except Exception:
            pass

    def _show_chrome(self):
        try:
            if self.control:
                self.control.set_visible(True)
        except Exception:
            pass

    # ---------- 框选 ----------

    def pick_region(self):
        self.set_auto_off()
        self._hide_chrome()

        def go():
            ov.RegionPicker(self.root, self._region_done, self._region_cancel)

        self.root.after(380, go)

    def _region_done(self, region):
        c = self.cfg["capture"]
        c["mode"] = "region"
        c["region"] = region
        cfgmod.save(self.cfg)
        self._show_chrome()
        self._set_status("● 选区 %d×%d" % (region["width"], region["height"]))
        self.root.after(1800, self.set_auto_on)

    def _region_cancel(self):
        self._show_chrome()
        self._set_status("○ 取消选区")
        self.root.after(900, self.set_auto_on)

    # ---------- 设置 ----------

    def open_settings(self, page="main"):
        w = self.settings_win
        if w is not None and getattr(w, "_alive", False):
            try:
                w.show_page(page)
                w.win.deiconify()
                w.win.lift()
                ov.force_topmost(w.win)
                return w
            except Exception:
                self.settings_win = None
        if self.auto_on:
            self._resume_auto_after_settings = True
            self.set_auto_off()
        else:
            self._resume_auto_after_settings = False
        import settings_ui
        self.settings_win = settings_ui.SettingsWindow(self, page)
        return self.settings_win

    def _settings_closed(self):
        self.settings_win = None
        if self._resume_auto_after_settings:
            self._resume_auto_after_settings = False
            try:
                self.root.after(180, self.set_auto_on)
            except Exception:
                pass

    def quit(self):
        # 设置窗口还开着时直接退出，也把界面上的值落盘。
        # 否则用户改了设置、没点「保存」就关程序，下次启动又回到默认。
        w = getattr(self, "settings_win", None)
        if w is not None and getattr(w, "_alive", False):
            try:
                w.save(close=False)
            except Exception:
                pass
        try:
            cfgmod.save(self.cfg)
        except Exception:
            pass
        try:
            self.mem.save_router(self.router.snapshot())
            self.mem.end_session()
            self.mem.save()
        except Exception:
            pass
        for obj in (getattr(self, "hk", None), getattr(self, "mouse", None)):
            try:
                obj.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass


# 好感一次加多少：多用 1~3，4~5 很少见。
# 权重约 92% 落在 1~3，4~5 只有 8%。
AFF_WEIGHTS = (40, 32, 20, 6, 2)       # 对应 1/2/3/4/5：1~3 占 92%，4~5 只剩 8%


def _roll_affection():
    return random.choices((1, 2, 3, 4, 5), weights=AFF_WEIGHTS, k=1)[0]


def _append_line(path, line, limit=1_600_000):
    """日志超过 1.6MB 就滚一份 .1，别让它无限长（5 秒一格，一天能写两万行）。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > limit:
            os.replace(path, path + ".1")
    except Exception:
        pass
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _safe_console(value):
    try:
        enc = sys.stdout.encoding or "utf-8"
        return str(value).encode(enc, "replace").decode(enc, "replace")
    except Exception:
        return str(value)


def selftest():
    cfg = cfgmod.load()
    prompts.ensure_files(cfg)
    print("程序目录:", BASE)
    print("数据目录:", DATA)
    print("配置文件:", cfgmod.CFG_PATH)
    print("提示词目录:", cfgmod.PROMPT_DIR)
    print("记忆文件:", cfgmod.MEMORY_PATH)
    print("=" * 64)
    print("自检")
    print("=" * 64)
    print("WGC 后端:", "可用" if capture.WGC_OK else "不可用（会退回 mss）")
    print("mss 后端:", "可用" if capture.MSS_OK else "不可用")
    print("中文字体:", ov.FONT_PATH or "没找到！会显示方框")
    print("API Key:", cfgmod.api_key_hint(cfg))
    try:
        import providers
        pname = providers.get(cfgmod.provider_id(cfg)).get("name", "")
    except Exception:
        pname = cfgmod.provider_id(cfg)
    print("接入:", "%s · %s · %s" % (pname, cfg["vlm"].get("model") or "",
                                    cfg["vlm"].get("base_url") or ""))
    print("视觉:", "detail=%s（low 会让读字和三环失效）" % cfg["vlm"].get("detail"))
    print("flow 地板:", signals.read_flow_baseline() or "未标定（这一版不许判断「他停了」）")
    st = memory.Memory(cfgmod.MEMORY_PATH, cfg)
    ms = st.stats(cfg)
    print("记忆预算: 请求 %d K / 实际 %.1f K" % (
        ms["requested_tokens"] // 1024, ms["budget_tokens"] / 1024.0))
    print("记忆现状: 原文 %d 条（未整理 %d）· 摘要 %d 字 · 当前约 %.1f K（%d%%）" % (
        ms["entries"], ms["unfolded"], ms["digest_chars"],
        ms["used_tokens"] / 1024.0, ms["fill_pct"]))
    mem_ctx = st.build_prompt_memory(cfg)
    print("\n—— 系统记忆 ——\n" + (mem_ctx["digest"] or "（空）")[:1200])
    print("\n—— 聊天记录（%d 轮）——" % mem_ctx["history_turns"])
    for m in mem_ctx["history"][-8:]:
        print("  [%s] %s" % (m.get("role"), (m.get("content") or "")[:90]))

    print("\n—— 提示词文件 ——")
    pm = prompts.load_all(force=True)
    for row in prompts.file_info():
        print("  %-22s %-19s %5d 字  ≈%5d tok" % (
            row["file"], row["mtime_text"], row["chars"], row["tokens"]))
    print("  主提示词是否为空:", "是！" if not (pm.get("main") or "").strip() else "否")

    img = None
    wins = capture.list_windows()
    print("\n可见窗口 %d 个:" % len(wins))
    for hwnd, title, rect in wins[:10]:
        print("  %8d  %-42s %dx%d" % (hwnd, _safe_console(title[:42]), rect[2], rect[3]))
    cc = cfg["capture"]
    rg = cc.get("region") or {}
    if cc.get("mode") == "region" and int(rg.get("width", 0) or 0) >= 32:
        print("\n抓帧测试 → 区域 %d×%d @ %d,%d" % (
            rg["width"], rg["height"], rg["left"], rg["top"]))
        img, err = capture.grab_region(rg)
        if img is None:
            print("  失败:", err)
    if img is not None:
        print("  成功 %dx%d" % img.size)
        sig = signals.analyze(img)
        print("  信号: ring=%s(%.2f) text=%s(%.1f) bubble=%s(%.1f) mean=%.1f open=%.1f" % (
            sig["ring"]["label"], sig["ring"]["conf"], sig["text"]["hit"],
            sig["text"]["score"], sig["bubble"]["hit"], sig["bubble"]["score"],
            sig["mean"], sig["openness"]))
        rt = routermod.Router(cfg)
        d = rt.tick(sig)
        print("  调度: %s" % routermod.describe(d))
        jpeg, size = capture.prepare(
            img, cc["max_side"], cc["jpeg_quality"], cc.get("scale", 1.0))
        prev = st.reference_lines(3)
        prompt = brain.build_prompt(cfg, system_memory=mem_ctx["digest"], prompt_map=pm,
                                    recent_lines=prev, decision=d, sig=sig, router=rt)
        note = brain.current_note_for(cfg, pm, prev)
        est = brain.estimate_prompt_tokens(prompt, mem_ctx["history"], note, size,
                                           cfg["vlm"].get("detail", "high"))
        print("\n—— 请求预览（≈%d tok + 图片）——\n%s" % (
            est, brain.render_transcript(prompt, mem_ctx["history"], note,
                                         "%d×%d" % size)[:6000]))
        if "--no-call" in sys.argv:
            print("\n--no-call：不调用模型。")
            return
        print("\n调用模型 …")
        res = brain.speak(cfg, cfgmod.api_key(cfg),
                          lambda nudge: brain.build_prompt(
                              cfg, system_memory=mem_ctx["digest"], prompt_map=pm,
                              retry_note=nudge, recent_lines=prev, decision=d,
                              sig=sig, router=rt),
                          mem_ctx["history"], note, jpeg, prev_lines=prev, image_size=size)
        shown = {k: v for k, v in res.items() if k not in ("prompt_text", "raw")}
        print("  " + json.dumps(shown, ensure_ascii=False, default=str)[:900])
        try:
            img.save(os.path.join(DATA, "_selftest.jpg"), quality=90)
            print("\n最后一帧:", os.path.join(DATA, "_selftest.jpg"))
        except Exception:
            pass


def _install_crash_log():
    import traceback as _tb

    def hook(t, v, tb):
        try:
            with open(os.path.join(DATA, "错误.txt"), "a", encoding="utf-8") as f:
                f.write("\n=== %s ===\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                f.write("".join(_tb.format_exception(t, v, tb)))
        except Exception:
            pass
        try:
            sys.__excepthook__(t, v, tb)
        except Exception:
            pass

    sys.excepthook = hook
    return hook


_SINGLE_HANDLE = None


def _single_instance():
    """同一个 Windows 会话只允许开一个：多开会重复截屏、重复说话、抢同一份记忆。"""
    global _SINGLE_HANDLE
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.CreateMutexW(None, False, "Local\\GameCompanion_SingleInstance")
        if not h:
            return True
        if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
            ctypes.windll.user32.MessageBoxW(
                0, "游戏搭子已经在运行了。", "游戏搭子", 0x40)
            return False
        _SINGLE_HANDLE = h
        return True
    except Exception:
        return True


def main():
    if "--selftest" in sys.argv and getattr(sys, "frozen", False):
        try:
            f = open(os.path.join(DATA, "_selftest.txt"), "w", encoding="utf-8")
            sys.stdout = f
            sys.stderr = f
        except Exception:
            pass

    # 先抢单实例，再开日志：第二个实例不应该把第一个实例的日志截断。
    if "--selftest" not in sys.argv and not _single_instance():
        return

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.path.join(DATA, "启动日志.txt"), "w",
                                        encoding="utf-8", buffering=1))
            except Exception:
                pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    hook = _install_crash_log()
    cfg = cfgmod.load()
    try:
        prompts.ensure_files(cfg)
    except Exception as e:
        print("[boot] 提示词目录初始化失败:", e)

    if "--selftest" in sys.argv:
        selftest()
        return

    print("[boot] creating app, BASE=", BASE)
    app = App(cfg)
    print("[boot] app ready, entering mainloop")

    if "--doctor" in sys.argv:
        def dump():
            print("screen %dx%d" % (app.root.winfo_screenwidth(), app.root.winfo_screenheight()))
            for w in app.root.winfo_children():
                st = ""
                try:
                    st = w.state()
                except Exception:
                    pass
                print("WIN %-10s geo=%-18s mapped=%s viewable=%s state=%s alpha=%s" % (
                    w.winfo_class(), w.winfo_geometry(), w.winfo_ismapped(),
                    w.winfo_viewable(), st,
                    w.attributes("-alpha") if w.winfo_class() == "Toplevel" else "-"))
            app.quit()
        app.root.after(3000, dump)

    try:
        app.root.report_callback_exception = hook
    except Exception:
        pass
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        app.quit()


if __name__ == "__main__":
    main()
