# -*- coding: utf-8 -*-
"""记忆：按 token 预算装配，不按"记几句"。

v4 相对 v3 的改动（对应契约 D2/D3）：
- 每条记录多五个字段：**ring / branch / anchor / thread / intent / pending**。
  v3 只有 scene/line/mood/focus/silent，所以"她当时在三环的哪一环、挂在哪条线程上"
  全部丢失，跨轮只剩下一句光秃秃的台词。
- `build_history()` **保留**：它把她说过的话变成真正的 user/assistant 消息，这条是对的。
- 新增 `reference_lines()`：把最近几句按"可以接的话"单独摆出来，供 A1 用；
  这和"防重复计数"是两件事，v3 把它们混在 recent_lines 一个口子上，所以只能拿来
  追加禁令，没法拿来当可引用的上文。
- 线程和记账队列也存进 memory.json（"router" 段），重启不丢。
"""

import datetime
import json
import os
import re
import threading
import time

import config as cfgmod
import prompts
from textutil import live_text

ISO = "%Y-%m-%dT%H:%M:%S"

# 记忆里不写内部英文 id（sign/bubble…），也不把它当"地点"。
_BRANCH_CN = {"sign": "读字", "bubble": "有人说话", "transit": "光变了", "pause": "他停了",
              "desire": "有东西靠近", "revisit": "走过的地方", "aesthetic": "这一片",
              "follow": "接着说", "lang": "她自己起的头"}


def now_iso(ts=None):
    return datetime.datetime.fromtimestamp(ts or time.time()).strftime(ISO)


def parse_iso(s):
    try:
        return datetime.datetime.strptime(str(s), ISO).timestamp()
    except Exception:
        return 0.0


def human_gap(seconds):
    s = max(0, int(seconds))
    if s < 45:
        return "刚刚"
    if s < 3600:
        return "%d 分钟" % (s // 60)
    if s < 86400:
        return "%d 小时" % (s // 3600)
    if s < 86400 * 30:
        return "%d 天" % (s // 86400)
    return "%d 个月" % (s // (86400 * 30))


def mark_story_entries(entries):
    """把「她自己起头 + 后面的续锚」标成故事。

    旧记忆没有 story 字段，只能从 branch 推断：lang 是开头，紧跟的 follow
    都是同一段故事，遇到别的 branch（招牌/水/过渡…）就结束。
    故事记录留在长期记忆里，但不再作为眼前的聊天记录反复喂给模型。
    """
    in_story = False
    for e in entries or []:
        branch = str(e.get("branch") or "").strip()
        if e.get("silent"):
            if branch == "lang":
                in_story = True
            e["story"] = bool(in_story or e.get("story"))
            continue
        if branch == "lang":
            in_story = True
        elif branch != "follow":
            in_story = False
        e["story"] = bool(in_story)
    return entries


def time_label(ts, now=None):
    now = now or time.time()
    s = max(0, int(now - ts))
    if s < 45:
        return "刚刚"
    if s < 3600:
        return "%d 分钟前" % (s // 60)
    if s < 86400:
        return "%d 小时前" % (s // 3600)
    if s < 86400 * 30:
        return "%d 天前" % (s // 86400)
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%m月%d日")
    except Exception:
        return "以前"


_CALL_TAIL = "，,。.、：:！!？? 　"


def clean_call_from_line(line, call):
    s = (line or "").strip()
    call = (call or "").strip()
    if len(call) < 2 or not s:
        return line
    out = s
    for pat in (call + "，", call + ",", call + "：", call + ":", call + " "):
        out = out.replace(pat, "")
    if out.startswith(call):
        out = out[len(call):].lstrip(_CALL_TAIL)
    out = out.strip()
    return out if len(out) >= 2 else line


class Memory:
    def __init__(self, path, cfg):
        self.path = path
        self.lock = threading.RLock()
        self.compress_lock = threading.Lock()
        self.entries = []
        self.digest = ""
        self.digest_upto = 0
        self.sessions = {"count": 0, "started": "", "last_active": ""}
        self.session_gap = None
        self.session_started = 0.0
        self.persist = True
        self._last_silent = 0.0
        self._include_time = True
        self.generation = 0
        self.router_state = {}
        self.load(cfg)

    # ----- 读写 -----

    def load(self, cfg):
        self.persist = bool((cfg.get("memory") or {}).get("persist", True))
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            try:
                bad = self.path.with_name("memory.bad.%s.json" % time.strftime("%Y%m%d-%H%M%S"))
                os.replace(str(self.path), str(bad))
            except Exception:
                pass
            return

        entries = []
        for e in (raw.get("entries") or []):
            if not isinstance(e, dict):
                continue
            ts = str(e.get("ts") or "")
            if not parse_iso(ts):
                ts = now_iso()
            entries.append({
                "ts": ts,
                "scene": str(e.get("scene") or "")[:160],
                "line": str(e.get("line") or "")[:1200],
                "mood": str(e.get("mood") or "")[:20],
                "focus": str(e.get("focus") or "")[:160],
                "silent": bool(e.get("silent", False)),
                # v4 新增
                "ring": str(e.get("ring") or "")[:20],
                "branch": str(e.get("branch") or "")[:20],
                "anchor": str(e.get("anchor") or "")[:40],
                "thread": str(e.get("thread") or "")[:40],
                "intent": str(e.get("intent") or "")[:40],
                "pending": str(e.get("pending") or "")[:120],
                # 故事格和她自己起话头的格子：只给故事线程接着讲，
                # 不再作为"眼前聊天记录"喂给普通格。
                "story": bool(e.get("story", False)),
                # 沉默条目会合并连续多格，用 n 记住这段里到底沉默了几格
                "n": max(1, int(e.get("n") or 1)),
            })
        mark_story_entries(entries)
        self.entries = entries
        self.digest = str(raw.get("digest") or "")
        self.digest_upto = max(0, min(int(raw.get("digest_upto") or 0), len(self.entries)))
        s = raw.get("sessions") or {}
        if isinstance(s, dict):
            self.sessions = {
                "count": max(0, int(s.get("count") or 0)),
                "started": str(s.get("started") or ""),
                "last_active": str(s.get("last_active") or ""),
            }
        self.router_state = raw.get("router") if isinstance(raw.get("router"), dict) else {}
        # 自愈：记忆已经清空，却还留着一个"正在聊的话题"，那是清空时没清干净留下的。
        # 没有台词记录支撑的话题不该活下来——一启动就把它丢掉，免得上一次清了一半。
        if not self.entries and (self.router_state.get("thread") or self.router_state.get("pending")):
            self.router_state = {}
        for e in reversed(self.entries):
            if e.get("silent"):
                self._last_silent = parse_iso(e.get("ts"))
                break
        self._trim_locked(cfg)

    def _payload_locked(self):
        return {
            "version": 4,
            "digest": self.digest,
            "digest_upto": self.digest_upto,
            "entries": self.entries,
            "sessions": self.sessions,
            "router": self.router_state,
        }

    def _save_locked(self):
        if not self.persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._payload_locked(), ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(self.path))
        except Exception:
            pass

    def save(self):
        with self.lock:
            self._save_locked()

    def trim(self, cfg):
        with self.lock:
            self._trim_locked(cfg)
            self._save_locked()

    def _trim_locked(self, cfg):
        max_n = int((cfg.get("memory") or {}).get("max_entries", 4000) or 4000)
        if len(self.entries) <= max_n:
            return
        self.generation += 1
        drop = len(self.entries) - max_n
        if self.digest_upto >= drop:
            self.entries = self.entries[drop:]
            self.digest_upto -= drop
        else:
            self.entries = self.entries[self.digest_upto:]
            self.digest_upto = 0
            if len(self.entries) > max_n * 2:
                self.entries = self.entries[-max_n:]
        self.digest_upto = max(0, min(self.digest_upto, len(self.entries)))

    # ----- 会话 -----

    def start_session(self, cfg):
        now = time.time()
        last = parse_iso(self.sessions.get("last_active"))
        gap = None
        if not self.sessions.get("count"):
            self.sessions["count"] = 1
            self.sessions["started"] = now_iso(now)
        elif last and now - last > 1800:
            self.sessions["count"] += 1
            self.sessions["started"] = now_iso(now)
            gap = now - last
        self.sessions["last_active"] = now_iso(now)
        self.session_started = parse_iso(self.sessions.get("started")) or now
        self.session_gap = gap
        with self.lock:
            self._save_locked()
        return self.sessions

    def touch(self):
        with self.lock:
            self.sessions["last_active"] = now_iso()
            self._save_locked()

    def end_session(self):
        self.touch()

    # ----- 写入 -----

    def add_entry(self, cfg, scene, line, mood="", focus="", silent=False, ts=None, meta=None):
        scene = (scene or "").strip()
        line = (line or "").strip()
        # 沉默条目现在没有 scene 也没有 line（v4 不再给她编地点），
        # 但"那一格她没开口"本身就是要记的事；不能因为两样都空就丢掉。
        if not scene and not line and not silent:
            return None
        ts = ts or time.time()
        meta = meta or {}
        with self.lock:
            entry = {
                "ts": now_iso(ts),
                "scene": scene[:160],
                "line": line[:1200],
                "mood": (mood or "")[:20],
                "focus": (focus or "")[:160],
                "silent": bool(silent),
                "ring": str(meta.get("ring") or "")[:20],
                "branch": str(meta.get("branch") or "")[:20],
                "anchor": str(meta.get("anchor") or "")[:40],
                "thread": str(meta.get("thread") or "")[:40],
                "intent": str(meta.get("intent") or "")[:40],
                "pending": str(meta.get("pending") or "")[:120],
                "story": bool(meta.get("story", False)),
                "n": max(1, int(meta.get("n") or 1)),
            }
            self.entries.append(entry)
            self.sessions["last_active"] = now_iso(ts)
            if silent:
                self._last_silent = ts
            self._trim_locked(cfg)
            self._save_locked()
            return entry

    def add_silent(self, cfg, scene="", mood="", focus="", reason="", meta=None):
        mcfg = cfg.get("memory") or {}
        if not mcfg.get("record_silent", True):
            return None
        scene = (scene or "").strip()
        # scene 允许为空：v4 没有地点信息，硬塞一个"眼前"只会让她以为一直站在原地
        now = time.time()
        gap = float(mcfg.get("silent_gap_sec", 90) or 90)
        with self.lock:
            last = self.entries[-1] if self.entries else None
            if last and last.get("silent") and now - (parse_iso(last.get("ts")) or 0) < gap:
                last.update({"ts": now_iso(now), "scene": scene[:160],
                             "mood": (mood or "")[:20], "focus": (focus or "")[:160],
                             "n": max(1, int(last.get("n") or 1)) + 1})
                if meta:
                    for k in ("ring", "branch", "anchor"):
                        if meta.get(k):
                            last[k] = str(meta[k])[:40]
                    if meta.get("story"):
                        last["story"] = True
                self.sessions["last_active"] = now_iso(now)
                self._last_silent = now
                self._save_locked()
                return last
            return self.add_entry(cfg, scene, "", mood, focus, silent=True, ts=now, meta=meta)

    def clear(self):
        with self.lock:
            self.generation += 1
            self.entries = []
            self.digest = ""
            self.digest_upto = 0
            self.router_state = {}
            self._last_silent = 0.0
            now = time.time()
            self.sessions = {"count": 1, "started": now_iso(now),
                             "last_active": now_iso(now)}
            self.session_gap = None
            self.session_started = now
            self._save_locked()

    def set_digest(self, text):
        with self.lock:
            self.generation += 1
            self.digest = (text or "").strip()
            # 手动清空摘要 = 把之前折叠掉的原文放回可见范围；
            # 否则 digest_upto 之后的记录会被同时排除在摘要和聊天记录之外。
            if not self.digest:
                self.digest_upto = 0
            self._save_locked()
        return self.digest

    # ----- 调度层状态（线程 / 记账） -----

    def save_router(self, snapshot):
        with self.lock:
            self.router_state = snapshot or {}
            self._save_locked()

    def load_router(self):
        with self.lock:
            return dict(self.router_state or {})

    # ----- 预算 / 装配 -----

    def requested_tokens(self, cfg):
        return int((cfg.get("memory") or {}).get("budget_k", 12) or 0) * 1024

    def budget_tokens(self, cfg):
        requested = self.requested_tokens(cfg)
        ctx = cfgmod.context_k(cfg) * 1024
        return max(0, min(requested, int(ctx * 0.6)))

    def _fit_tokens(self, text, limit):
        if not text or limit <= 0:
            return ""
        if prompts.estimate_tokens(text) <= limit:
            return text
        out = text
        while out and prompts.estimate_tokens(out) > limit:
            cut = max(1, int(len(out) * 0.08))
            out = out[cut:].lstrip("，。；、 \n")
        return ("…" if out and out != text else "") + out

    def _fit_chars(self, text, limit):
        if not text or limit <= 0 or len(text) <= limit:
            return text
        parts = re.split(r"(?<=[。！？\n])", text)
        out = ""
        for p in reversed(parts):
            if len(out) + len(p) > limit:
                break
            out = p + out
        return ("…" if out else "") + out[-max(1, limit):]

    def _entry_line(self, e, now, compact=False, include_time=None):
        if include_time is None:
            include_time = self._include_time
        scene = (e.get("scene") or "").strip()
        line = live_text(e.get("line") or "").strip()
        mood = (e.get("mood") or "").strip()
        branch = (e.get("branch") or "").strip()
        name = scene or _BRANCH_CN.get(branch, "") or "刚才"
        if compact:
            tail = ("（%s）" % mood) if mood and mood != "平静" else ""
            if line:
                return "- %s：%s%s" % (name, line, tail)
            return "- %s（没说话）" % name
        head = "- "
        if include_time:
            head += time_label(parse_iso(e.get("ts")), now) + " · "
        head += name
        if line:
            head += "：「%s」" % line
            if mood and mood != "平静":
                head += "（%s）" % mood
        else:
            head += "（没说话）"
        return head

    def build_digest_block(self, cfg, budget=None, now=None):
        now = now or time.time()
        include_time = bool((cfg.get("memory") or {}).get("include_time", True))
        budget = budget if budget is not None else self.budget_tokens(cfg)
        if budget <= 0:
            return ""
        with self.lock:
            entries = list(self.entries)
            digest = (self.digest or "").strip()
            upto = self.digest_upto
            gap = self.session_gap
            started = self.session_started
            sessions = dict(self.sessions)

        mcfg = cfg.get("memory") or {}
        share = int(mcfg.get("digest_share_pct", 40) or 40)
        header = "【你记得的】"
        body = []
        n = len(entries)
        if n == 1 and not entries[0].get("silent"):
            body.append("你们刚开始一起走。")
        # 有聊天记录时不再报"一起看过 N 段画面"——那是系统视角，
        # 模型读到只会觉得"这是新的一段"，正好和"她要接着聊"相反。

        if digest:
            digest = self._fit_tokens(digest, int(budget * share / 100.0))
            body.append("（更早）\n" + digest)

        silent_n = sum(max(1, int(e.get("n") or 1)) for e in entries[upto:] if e.get("silent"))
        if include_time and silent_n:
            body.append("（这一段里你有 %d 格没开口。）" % silent_n)

        if gap and include_time and (now - (started or now)) < 900:
            body.append("（你们上一次一起看画面，是在 %s前。）" % human_gap(gap))

        if not body:
            return ""
        return header + "\n" + "\n\n".join(body)

    def _history_user_text(self, e, now, include_time=True, prev_ts=None, story=False):
        if story:
            # 故事模式没有图；不能再说"你看见一张画面"。
            return "（接着讲）" if prev_ts else "（你开始讲这件事）"
        when = time_label(parse_iso(e.get("ts")), now) if include_time else ""
        scene = (e.get("scene") or "").strip()
        mood = (e.get("mood") or "").strip()
        ts = parse_iso(e.get("ts"))
        if prev_ts and ts:
            gap = ts - prev_ts
            rel = "接着" if gap < 45 else ("%s后" % human_gap(gap))
        else:
            rel = ("%s，你看见一张画面" % when) if when else "刚才"
        text = "（%s%s" % (rel + ("，" if scene else ""), ("你看见：" + scene) if scene else "")
        if mood and mood not in ("平静", "沉默"):
            text += "，你当时有点" + mood
        return text + "）"

    def build_history(self, cfg, max_tokens=None, now=None, since_ts=None):
        """把最近说过的话变成真正的 user / assistant 聊天记录（D3：保留）。

        since_ts 只给故事模式用：从这条故事的开口那一刻开始算，
        把上一段招牌/气泡的聊天记录切掉，免得模型把旧话题讲成新故事。
        """
        now = now or time.time()
        include_time = bool((cfg.get("memory") or {}).get("include_time", True))
        budget = self.budget_tokens(cfg) if max_tokens is None else max(0, int(max_tokens))
        if budget <= 0:
            return []
        with self.lock:
            entries = list(self.entries[self.digest_upto:])
        if since_ts:
            entries = [e for e in entries
                       if (parse_iso(e.get("ts")) or 0) >= float(since_ts)]
        # 故事格（since_ts 非空）要接着讲，普通格不把故事内容当聊天记录。
        # 不隔离的话，故事结束后的下一格又会在历史里看到香菜/袋口，
        # 顺着上一段回忆往下说，故事就永远收不干净。
        story_mode = bool(since_ts)
        # 先按预算从后往前装，装完再**正序**算相对时间
        # （反着算会把"接着"贴到第一条、把"4 分钟前"贴到最后一条，正好反了）
        picked = []
        remaining = budget
        for e in reversed(entries):
            if not story_mode and e.get("story"):
                continue
            line = live_text(e.get("line") or "").strip()
            if not line:
                continue
            cost = (prompts.estimate_tokens(line) + 24)
            if cost > remaining:
                break
            picked.append((e, line))
            remaining -= cost
        picked.reverse()

        out = []
        prev_ts = None
        for e, line in picked:
            user = self._history_user_text(e, now, include_time, prev_ts=prev_ts,
                                           story=story_mode)
            prev_ts = parse_iso(e.get("ts")) or prev_ts
            out.append({"role": "user", "content": user})
            out.append({"role": "assistant", "content": line})
        return out

    def build_prompt_memory(self, cfg, now=None, history_since=None):
        now = now or time.time()
        budget = self.budget_tokens(cfg)
        if budget <= 0:
            return {"digest": "", "history": [], "tokens": 0,
                    "has_memory": False, "history_turns": 0}
        digest = self.build_digest_block(cfg, budget=budget, now=now)
        used = prompts.estimate_tokens(digest)
        history = self.build_history(cfg, max_tokens=max(0, budget - used - 24),
                                     now=now, since_ts=history_since)
        hist_tokens = sum(prompts.estimate_tokens(m.get("content") or "") + 8
                          for m in history) + (16 if history else 0)
        return {"digest": digest, "history": history, "tokens": used + hist_tokens,
                "has_memory": bool(digest or history),
                "history_turns": len(history) // 2}

    # ----- 可引用的上文（D3） -----

    def reference_lines(self, limit=3, max_age_sec=600, include_story=False):
        """最近她自己说过的几句，按"后说的在前"，供 A1 接住。

        普通格只看眼前说过的话：故事里的袋口/香菜不能当"上一句"来接。
        """
        out = []
        now = time.time()
        with self.lock:
            for e in reversed(self.entries):
                ts = parse_iso(e.get("ts"))
                if max_age_sec and ts and now - ts > max_age_sec:
                    break
                if not include_story and e.get("story"):
                    continue
                line = live_text(e.get("line") or "").strip()
                if line:
                    out.append(line)
                    if len(out) >= limit:
                        break
        return out

    def recent_lines(self, limit=40, max_age_sec=0, include_story=False):
        out = []
        now = time.time()
        with self.lock:
            for e in reversed(self.entries):
                if max_age_sec:
                    ts = parse_iso(e.get("ts"))
                    if ts and now - ts > max_age_sec:
                        break
                if not include_story and e.get("story"):
                    continue
                line = live_text(e.get("line") or "").strip()
                if line:
                    out.append(line)
                    if len(out) >= limit:
                        break
        return out

    def recent_moods(self, limit=2):
        """最近几句**真开口**的情绪，按后说的在前。

        沉默条目没有台词也没有情绪，不能算进"连着两格开心"的节流里——
        用 entries[-2:] 直接取，中间夹一格沉默就永远节流不到。
        """
        out = []
        with self.lock:
            for e in reversed(self.entries):
                if not (e.get("line") or "").strip():
                    continue
                out.append(e.get("mood") or "")
                if len(out) >= limit:
                    break
        return out

    def strip_call_history(self, call):
        call = (call or "").strip()
        if len(call) < 2:
            return 0
        with self.lock:
            n = 0
            for e in self.entries:
                old = e.get("line") or ""
                new = clean_call_from_line(old, call)
                if new != old:
                    e["line"] = new
                    n += 1
            # 长期摘要里也可能带着旧称呼；只清原文不清摘要，
            # 她下次还是会在“你记得的”里看到旧名字。
            old = self.digest or ""
            new = clean_call_from_line(old, call)
            if new != old:
                self.digest = new
                if not self.digest:
                    self.digest_upto = 0
                n += 1
            if n:
                self.generation += 1
                self._save_locked()
        return n

    # ----- 统计 / 展示 -----

    def transcript(self, cfg, now=None):
        pm = self.build_prompt_memory(cfg, now=now)
        parts = []
        if pm["digest"]:
            parts.append("[系统记忆]\n" + pm["digest"])
        if pm["history"]:
            lines = ["[聊天记录]"]
            for m in pm["history"]:
                tag = "她" if m.get("role") == "assistant" else "你看"
                lines.append("[%s] %s" % (tag, m.get("content") or ""))
            parts.append("\n".join(lines))
        rs = self.router_state or {}
        if rs.get("thread") or rs.get("pending"):
            lines = ["[调度层状态]"]
            t = rs.get("thread") or {}
            if t:
                lines.append("- 线程：%s（%s，还剩 %s 格，第 %s 步）" % (
                    t.get("anchor"), t.get("branch"), t.get("remaining"), t.get("stage")))
            for p in (rs.get("pending") or []):
                lines.append("- 记账：%s %s（%s 格）" % (p.get("kind"), p.get("text"), p.get("age")))
            parts.append("\n".join(lines))
        return "\n\n".join(parts) if parts else "（现在还没有记忆。）"

    def stats(self, cfg):
        pm = self.build_prompt_memory(cfg)
        budget = self.budget_tokens(cfg)
        requested = self.requested_tokens(cfg)
        used = pm["tokens"]
        with self.lock:
            n = len(self.entries)
            unfolded = max(0, n - self.digest_upto)
            digest_chars = len(self.digest or "")
            last_line = ""
            for e in reversed(self.entries):
                if e.get("line"):
                    last_line = e["line"]
                    break
        return {
            "entries": n, "unfolded": unfolded, "digest_chars": digest_chars,
            "used_tokens": used, "budget_tokens": budget, "requested_tokens": requested,
            "fill_pct": int(100.0 * used / budget) if budget else 0,
            "last_line": last_line, "sessions": self.sessions.get("count", 0),
            "history_turns": pm.get("history_turns", 0),
            "path": str(self.path),
        }

    # ----- 压缩 -----

    def needs_compress(self, cfg):
        mcfg = cfg.get("memory") or {}
        if not mcfg.get("auto_compress", True):
            return False
        budget = self.budget_tokens(cfg)
        if budget <= 0:
            return False
        with self.lock:
            unfolded = list(self.entries[self.digest_upto:])
            digest = self.digest or ""
        if len(unfolded) < 8:
            return False
        used = prompts.estimate_tokens(digest)
        for e in unfolded:
            used += prompts.estimate_tokens(self._entry_line(e, time.time(), compact=True)) + 1
        return used >= int(budget * float(mcfg.get("compress_trigger_pct", 85)) / 100.0)

    def _fold_plan(self, cfg, force=False):
        with self.lock:
            unfolded = list(self.entries[self.digest_upto:])
        if not unfolded:
            return []
        budget = self.budget_tokens(cfg)
        mcfg = cfg.get("memory") or {}
        share = int(mcfg.get("digest_share_pct", 40) or 40)
        keep_budget = int(budget * (100 - share) / 100.0 * 0.8)
        if force:
            keep_budget = min(keep_budget, sum(
                prompts.estimate_tokens(self._entry_line(e, time.time(), compact=True)) + 1
                for e in unfolded[-3:]))
        acc, split = 0, 0
        for i in range(len(unfolded) - 1, -1, -1):
            t = prompts.estimate_tokens(self._entry_line(unfolded[i], time.time(), compact=True)) + 1
            if acc + t > keep_budget:
                split = i + 1
                break
            acc += t
        fold = unfolded[:split]
        if not fold:
            return []
        chunk_limit = int(mcfg.get("compress_chunk_tokens", 6000) or 6000)
        out, used = [], 0
        for e in fold:
            t = prompts.estimate_tokens(self._entry_line(e, time.time(), compact=True)) + 1
            if out and used + t > chunk_limit:
                break
            out.append(e)
            used += t
        return out

    def _mechanical_digest(self, digest, fold, max_chars):
        rows = [self._entry_line(e, time.time(), compact=True) for e in fold]
        text = ("；".join([digest] + rows)) if digest else "；".join(rows)
        text = text.replace("- ", "")
        if len(text) > max_chars:
            text = "…" + text[-max_chars:]
        return text

    def _compress_inner(self, cfg, api_key, prompt_map=None, force=False):
        pm = prompt_map or prompts.load_all()
        with self.lock:
            gen = self.generation
        fold = self._fold_plan(cfg, force=force)
        if not fold:
            return {"ok": False, "skipped": True, "message": "记忆还短，不用整理"}
        budget = self.budget_tokens(cfg)
        mcfg = cfg.get("memory") or {}
        share = int(mcfg.get("digest_share_pct", 40) or 40)
        max_chars = min(int(mcfg.get("compress_max_chars", 2500) or 2500),
                        max(300, int(budget * share / 100.0 / 0.62)))
        with self.lock:
            digest = self.digest or ""
        entries_text = "\n".join(self._entry_line(e, time.time(), compact=True) for e in fold)
        template = (pm.get("compress") or "").strip()
        fallback_reason, usage, result_text = "", {}, ""

        if not template:
            fallback_reason = "70_记忆压缩.txt 是空的"
        elif api_key:
            import brain
            prompt = prompts.render(template, {
                "digest": digest or "（还没有长期记忆）",
                "entries": entries_text,
                "name": (cfg.get("persona") or {}).get("name") or "她",
                "max_chars": max_chars,
            })
            try:
                res = brain.complete_text(
                    prompt, cfg, api_key,
                    max_tokens=min(2000, max(256, int(max_chars * 0.9))),
                    timeout=min(90, float((cfg.get("vlm") or {}).get("timeout", 25)) * 2))
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            usage = res.get("usage") or {}
            if res.get("ok") and (res.get("text") or "").strip():
                result_text = res["text"].strip()
            else:
                fallback_reason = res.get("error") or "模型没有返回内容"
        else:
            fallback_reason = "没有 API Key"

        if result_text:
            result_text = re.sub(r"^```.*?\n|```$", "", result_text, flags=re.S).strip()
            result_text = result_text.strip("「」“”\"'")
            result_text = self._fit_chars(result_text, max_chars)
            fallback = False
        else:
            result_text = self._mechanical_digest(digest, fold, max_chars)
            fallback = True

        with self.lock:
            if gen != self.generation:
                return {"ok": False, "error": "记忆在整理过程中被修改，已放弃这次结果"}
            self.digest = result_text
            self.digest_upto = min(len(self.entries), self.digest_upto + len(fold))
            self.sessions["last_active"] = now_iso()
            self._save_locked()

        return {"ok": True, "folded": len(fold), "fallback": fallback,
                "error": fallback_reason, "digest_chars": len(result_text),
                "usage": usage}

    def compress(self, cfg, api_key, prompt_map=None, force=False):
        if not self.compress_lock.acquire(False):
            return {"ok": False, "error": "正在整理记忆"}
        try:
            return self._compress_inner(cfg, api_key, prompt_map, force)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            self.compress_lock.release()

    def compress_async(self, cfg, api_key, prompt_map=None, on_done=None, force=False):
        if not self.compress_lock.acquire(False):
            return False

        def run():
            try:
                info = self._compress_inner(cfg, api_key, prompt_map, force)
            except Exception as e:
                info = {"ok": False, "error": str(e)}
            finally:
                self.compress_lock.release()
            if on_done:
                try:
                    on_done(info)
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()
        return True
