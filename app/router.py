# -*- coding: utf-8 -*-
"""调度层：这一格到底开不开口、开口是接着谁说。

相对 v3 路由表的五处改造（都是实测逼出来的）：

1. **阈值改成滑动窗口基线（C1）**。原来按「一组素材」自标定，等于承认阈值没有物理
   单位；真实运行是连续流，没有「一组」可以归一化。现在以最近 24 tick（2 分钟）
   为滚动基线算相对量，同时保留绝对地板；并且每 tick 输出一份「本段动度报告」，
   沉默可以归因（是没动、还是有动但都不够强）。
2. **线程优先于事件（C2）**。原来事件优先，每 tick 被新画面拐走，话题活不过两句。
   现在有活跃线程且没说满就先续锚；只有打断等级 >= 3 的事件（气泡/大字/进出室内/
   他停下）允许抢占，且必须记进 decision["preempt"]。
3. **线程是有结构的（C3）**：锚 / 她的立场 / 还剩几格 / 可打断等级；推进 = 意图变化
   （升起→犹豫→改口→落空→认掉），不是物理状态延续。
4. **pending 由调度层记账（C4/A2）**：请求类到龄自兑现或作废；问类 age>=2 转自问，
   不再期待回答；只有「他还欠我一句」这种记账型才许长挂。不清算的话，五格之后她
   手里全是自己发的债，每一句都变成讨债。
5. **回访检测（C5）**：signature 早就在算，只是没做最近邻。间隔 > 60s 且距离小于
   阈值 → revisit。这是唯一不需要识别能力就能说「又绕回来了」的信号。

另一个约定：**认亲只在相邻一帧内成立**。欲望分支（主体连涨）只许比 tick-1 和 tick，
隔一轮以上一律不算——不定义这条，跨帧比较就没有合法地基。
"""

import math
import random
import time

from textutil import live_text

# ---------- 可调常量 ----------

WINDOW_TICKS = 24          # 滚动基线窗口 = 2 分钟 @5s
POT_THRESHOLD = 3          # 连续 3 格没开口 → 势能满，允许开场
THREAD_LIFE = 3            # 普通话题寿命：开头 + 1 段中间 + 收尾（最多 3 格）
STORY_MID_MIN = 2          # 故事线程：中间段数下限
STORY_MID_MAX = 4          # 故事线程：中间段数上限（总长 = 中间 + 开头 + 结束 = 4~6 格）
THREAD_BREAK = 3           # 线程可被打断的最低事件等级
PENDING_MAX = 6            # 队列上限，超了先清最老的问类
ACCOUNT_TTL = 8            # 「他还欠我一句」这类记账型最多挂 8 格
LANG_EVERY = 5             # 连着几次都在说画面之后，强制她说一次自己的事
SIGN_MEMO_TICKS = 8        # 同一块字多久算"说过"（游戏 HUD 是静止的，3 格太短，会每 4 格重念一次）
SHOP_EVERY = 20            # 「想过去看看」最短间隔（格）= 100 秒。偶尔一次，不催他
SHOP_CHANCE = 0.35         # 到了间隔也不一定说，掷一次骰子
REVISIT_GAP_TICKS = 12     # 回访间隔下限 = 60s
REVISIT_DIST = 9.0         # 26 帧灰度签名差小于这个数算同一个地方

# 事件等级：3 以上可以抢线程
PRIO = {
    # 气泡只是路人的话，跟你没关系：优先级压到最低，也不再允许它打断线程。
    "bubble": 1,
    "sign": 4,
    "transit": 3,
    "pause": 3,
    "desire": 2,
    "revisit": 2,
    "aesthetic": 1,
}

# 这些是"看一个地方/一家店"的线程：节奏固定为 请求去看 → 看一会 → 走。
PLACE_BRANCHES = ("sign", "aesthetic", "desire", "revisit", "transit")
PLACE_LEAVE3 = 0.7             # 看一个地方：第三格就走的概率（否则第四格走）

# 线程推进的五个步（意图的变化，不是物理状态的延续）
STAGES = ["升起", "犹豫", "改口", "落空", "认掉"]

BRANCH_CN = {
    "bubble": "气泡", "sign": "读字", "transit": "过渡", "pause": "读他",
    "desire": "欲望", "revisit": "回访", "aesthetic": "审美",
    "follow": "接话", "lang": "闲话",
}


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(p * (len(s) - 1))))
    return float(s[i])


def _median(vals):
    return _pct(vals, 0.5)


class Router:
    """一格 = 一次 tick。调用 tick(sig) 拿到 decision，再决定要不要叫模型。"""

    def __init__(self, cfg=None):
        cfg = cfg or {}
        r = (cfg.get("router") or {})
        self.window_ticks = int(r.get("window_ticks", WINDOW_TICKS) or WINDOW_TICKS)
        self.thread_life = int(r.get("thread_life_ticks", THREAD_LIFE) or THREAD_LIFE)
        self.flow_floor = r.get("flow_floor") or None
        self.calibrated = bool(self.flow_floor)

        self.win = []                 # 最近 N 个信号
        self.hist = []                # [(tick, sig)]，回访用
        self.thread = None
        self.pending = []
        self.tick_no = 0
        self.pot = 0
        self.since_lang = 0
        self.shop_last = -999
        self.bubble_memo = {}         # 位置 key -> 最近一次看到的 tick
        self.sign_memo = {}           # 同上，读字用

    # ---------- 基线 ----------

    def thresholds(self):
        """滚动基线 + 绝对地板。窗口不足时用保守默认值。"""
        rows = self.win[-self.window_ticks:]
        dm = [abs(r["mean"] - p["mean"]) for p, r in zip(rows, rows[1:])
              if r.get("mean") is not None and p.get("mean") is not None]
        op = [abs(r.get("openness") or 0) for r in rows]
        ed = [r.get("edge") or 0 for r in rows]
        ce = [abs((r.get("ctr_edge") or 0) - (p.get("ctr_edge") or 0))
              for p, r in zip(rows, rows[1:])]
        n = len(rows)
        # 样本少的时候分位数没意义，用绝对地板顶着
        if n >= 6:
            dm_stab = max(6.0, _pct(dm, 0.55))
            open_flip = max(12.0, _pct(op, 0.60))
            edge_aesth = max(7.5, _pct(ed, 0.60))
            abs_open = max(10.0, _pct(op, 0.50))
            grow = max(1.5, _pct(ce, 0.70))
        else:
            dm_stab, open_flip, edge_aesth, abs_open, grow = 18.0, 55.0, 12.0, 25.0, 4.0
        return {
            "dm_stab": round(dm_stab, 2), "open_flip": round(open_flip, 2),
            "edge_aesth": round(edge_aesth, 2), "abs_open": round(abs_open, 2),
            "grow_edge": round(grow, 2), "text": 1.0,
            "flow_pause": (float(self.flow_floor) if self.calibrated else None),
            "n_base": n,
        }

    def activity_report(self):
        """本段动度报告：沉默要能归因，不能和「高冷」长得一样。"""
        rows = self.win[-self.window_ticks:]
        if not rows:
            return {"n": 0}
        means = [r.get("mean") or 0 for r in rows]
        opens = [r.get("openness") or 0 for r in rows]
        flows = [r.get("flow") for r in rows if r.get("flow") is not None]
        return {
            "n": len(rows),
            "mean": [round(min(means), 1), round(max(means), 1)],
            "open": [round(min(opens), 1), round(max(opens), 1)],
            "flow_med": round(_median(flows), 1) if flows else None,
            "text_hits": sum(1 for r in rows if (r.get("text") or {}).get("hit")),
            "calibrated": self.calibrated,
        }

    # ---------- 事件 ----------

    def _bubble_novelty(self, sig):
        """气泡和黑底白字招牌在像素上同构，只能靠时间分辨：气泡会消失，招牌不会。

        同一位置最近两格都在 → 那是招牌，不是气泡。
        """
        b = sig.get("bubble") or {}
        if not b.get("hit") or (b.get("score") or 0) < 1.5:
            return False
        x, y, w, h = b["rect"]
        key = "%d_%d" % (int(round(x / 0.04)), int(round(y / 0.03)))
        seen_before = self.bubble_memo.get(key)
        self.bubble_memo[key] = self.tick_no
        # 只留最近 8 格的位置记录
        self.bubble_memo = {k: v for k, v in self.bubble_memo.items()
                            if self.tick_no - v <= 8}
        return seen_before is None or (self.tick_no - seen_before) > 2

    def _revisit(self, sig):
        cur = sig.get("sig")
        if not cur:
            return None
        best = None
        for tick, old in self.hist:
            if self.tick_no - tick < REVISIT_GAP_TICKS:
                continue
            d = _sig_diff(cur, old)
            if d <= REVISIT_DIST and (best is None or d < best[1]):
                best = (tick, d)
        if best:
            return {"tick": best[0], "dist": round(best[1], 2),
                    "sec": int((self.tick_no - best[0]) * 5)}
        return None

    def _sign_novelty(self, sig, thr):
        """招牌一直都在，不算新闻；**新出现在视野里的一块字**才值得开口。

        不做这一层，一条商业街每格都有字 → 每格都触发读字 → 她每 5 秒念一块招牌，
        正好是 v3 那个"导游腔"复发。所以按位置记最近 3 格，同一块字不重复报。
        """
        t = sig.get("text") or {}
        if not t.get("hit") or (t.get("score") or 0) < thr.get("text", 1.0):
            return False
        x, y = t["rect"][0], t["rect"][1]
        key = "%d_%d" % (int(round(x / 0.05)), int(round(y / 0.05)))
        seen = self.sign_memo.get(key)
        self.sign_memo[key] = self.tick_no
        self.sign_memo = {k: v for k, v in self.sign_memo.items()
                          if self.tick_no - v <= SIGN_MEMO_TICKS}
        return seen is None or (self.tick_no - seen) > SIGN_MEMO_TICKS

    def _preempt_allowed(self, ev, sig):
        """能不能打断正在进行的线程。

        区分「开场等级」和「抢断等级」：读字很会开场，但一条商业街每格都有新招牌，
        让它随便抢，话题照样活不过两句——只是换了个人来拐她。所以：
        - 过渡（进出室内、亮度换级）/ 他停了 → 可以抢；
        - 读字只有**特别强**的那一块（笔画占比 ≥ 6）才允许抢，普通招牌排队等下一轮；
        - 气泡（路人的话）不抢——它不是冲她说的，凭什么打断她。
        """
        if not self.thread:
            return None
        # 故事线程保护到收尾：开头/中间/结束是一次完整的叙述，
        # 中途被一块路过的招牌换掉，就永远走不到"最后一格"。
        # 只有「他停了」（已标定的物理信号）可以打断故事。
        if (self.thread.get("branch") or "") == "lang":
            return "pause" if "pause" in ev else None
        # 看一个地方也要走完 请求 → 看 → 走：中途被一块新招牌/一次亮度换级抢走，
        # 线程会重新从"开头"开始，于是每一格都在约"再看一会"，永远走不到收尾。
        # 只有「他停了」可以打断这条弧线。
        if (self.thread.get("branch") or "") in PLACE_BRANCHES:
            return "pause" if "pause" in ev else None
        # 刚开的线程至少说两句：气泡/他停下可以抢，其余等一格。
        # 不加这条，一个话题常常只说了一句就被路过的招牌换掉——"每格都是新开始"就是这么来的。
        age = self.tick_no - int(self.thread.get("born") or self.tick_no)
        if age < 2 and "pause" not in ev:
            return None
        lvl = int(self.thread.get("break_level", THREAD_BREAK) or THREAD_BREAK)
        best, best_prio = None, 0
        for e in ev:
            if e == "sign":
                score = (sig.get("text") or {}).get("score") or 0
                if score < 6.0:
                    continue
                p = lvl
            else:
                p = PRIO.get(e, 0)
            if p >= lvl and p > best_prio:
                best, best_prio = e, p
        return best

    def detect(self, sig, thr):
        ev, why = [], []
        rows = self.win
        prev = rows[-1] if rows else None
        prev2 = rows[-2] if len(rows) >= 2 else None

        # 气泡（时间分辨过的新出现）
        if self._bubble_novelty(sig):
            b = sig["bubble"]
            ev.append("bubble")
            why.append("小暗条 %.2f×%.2f 新出现（上两格没有）" % (b["rect"][2], b["rect"][3]))

        # 读字（同样是"新出现的一块字"，不是"画面里有字"）
        if self._sign_novelty(sig, thr):
            t = sig["text"]
            ev.append("sign")
            why.append("新的一块字 %.1f @%.2f,%.2f" % (t["score"], t["rect"][0], t["rect"][1]))

        # 过渡：亮度换级且站稳（实时版「下一帧确认」= 上一帧跳完、这一帧没再跳）
        if prev is not None and prev2 is not None:
            jump = abs(prev.get("mean", 0) - prev2.get("mean", 0))
            hold = abs(sig.get("mean", 0) - prev.get("mean", 0))
            if jump >= thr["dm_stab"] and hold < max(6.0, thr["dm_stab"] * 0.6):
                ev.append("transit")
                why.append("亮度换级 %+.1f 且站稳" % (sig.get("mean", 0) - prev2.get("mean", 0)))
        # 开阔度翻向
        if prev is not None:
            a, b = prev.get("openness") or 0, sig.get("openness") or 0
            if abs(b) >= thr["open_flip"] and (a > 0) != (b > 0):
                ev.append("transit")
                why.append("开阔度翻向 %+.1f→%+.1f" % (a, b))

        # 欲望：只许比相邻一帧（认亲边界）
        if prev is not None:
            d1 = (sig.get("ctr_edge") or 0) - (prev.get("ctr_edge") or 0)
            d0 = ((prev.get("ctr_edge") or 0) - (prev2.get("ctr_edge") or 0)
                  if prev2 is not None else None)
            if d0 is not None and d1 >= thr["grow_edge"] and d0 >= thr["grow_edge"]:
                ev.append("desire")
                why.append("相邻两帧中心连涨 %+.1f/%+.1f" % (d0, d1))

        # 审美：细节 + 没字 + 开阔反差
        has_text = bool((sig.get("text") or {}).get("hit"))
        detail = ((sig.get("edge") or 0) >= thr["edge_aesth"]
                  or (sig.get("bot_std") or 0) >= 18.0)
        if detail and not has_text and abs(sig.get("openness") or 0) >= thr["abs_open"]:
            ev.append("aesthetic")
            why.append("细节 edge %.1f 地面 %.1f + open %.1f" % (
                sig.get("edge") or 0, sig.get("bot_std") or 0, sig.get("openness") or 0))

        # 他停了：只有标定过、且连续两格都低于地板才敢说
        if self.calibrated and sig.get("flow") is not None:
            floor = float(self.flow_floor)
            f1 = sig.get("flow")
            f0 = prev.get("flow") if prev else None
            if f1 is not None and f0 is not None and f1 <= floor and f0 <= floor:
                ev.append("pause")
                why.append("连续两格 flow≤%.1f（标定地板）" % floor)

        # 回访
        rv = self._revisit(sig)
        if rv:
            ev.append("revisit")
            why.append("和 %d 格前（%.0f 秒）的签名差 %.1f" % (rv["tick"], rv["sec"], rv["dist"]))

        return ev, why, rv

    # ---------- pending 清算（A2） ----------

    def pending_tick(self):
        """请求类到龄自兑现或作废；问类 age>=2 转自问；记账型最多挂 8 格。"""
        out = []
        keep = []
        for p in self.pending:
            p["age"] = self.tick_no - p["born"]
            kind = p.get("kind")
            if kind == "request" and p["age"] >= 2:
                out.append("请求到龄·自兑现或作废：%s" % p["text"])
                continue
            if kind == "question" and p["age"] >= 2:
                # 问转自问只许转给"问出这句话的那条线程"。
                # 不比对线程，旧话题的问题会被挂到新话题上——实测"叉烧真有那么厉害"
                # 就是这样被带进煲仔饭故事，第四格突然拐去叉烧。
                tb = p.get("thread_born")
                same_thread = (self.thread is not None and tb is not None
                               and int(self.thread.get("born") or -1) == int(tb))
                if same_thread:
                    self.thread.setdefault("self_ask", []).append(p["text"])
                    out.append("问转自问：%s" % p["text"])
                else:
                    out.append("问转自问·旧话题放下：%s" % p["text"])
                continue
            if kind == "account" and p["age"] >= ACCOUNT_TTL:
                out.append("记账作废：%s" % p["text"])
                continue
            keep.append(p)
        # 队列超限先清最老的问类
        while len(keep) > PENDING_MAX:
            qs = [i for i, p in enumerate(keep) if p.get("kind") == "question"]
            keep.pop(qs[0] if qs else 0)
        self.pending = keep
        return out

    def apply_resolution(self, res):
        """模型这一格给出的记账：+问/+求/+账/清。"""
        notes = []
        for kind, text in (res.get("pending_add") or []):
            if kind not in ("question", "request", "account"):
                kind = "question"
            if any(p.get("text") == text for p in self.pending):
                continue
            self.pending.append({"kind": kind, "text": text,
                                 "born": self.tick_no, "age": 0,
                                 "thread_born": (self.thread or {}).get("born")})
            notes.append("记账+%s" % text)
        for text in (res.get("pending_clear") or []):
            before = len(self.pending)
            if text in ("全部", "all", "*"):
                self.pending = []
            else:
                self.pending = [p for p in self.pending if text not in p.get("text", "")]
            if len(self.pending) != before:
                notes.append("记账清=%s" % text)
        return notes

    # ---------- 线程 ----------

    def _open_thread(self, branch, sig, rv=None):
        anchor = ""
        t = sig.get("text") or {}
        if branch == "sign" and t.get("rect"):
            # 她还不知道那写的是什么，锚只能是「刚才那块字」，不能是坐标
            anchor = "刚才那块字"
        elif branch == "bubble" and (sig.get("bubble") or {}).get("rect"):
            anchor = "刚才有人说话"
        elif branch == "revisit" and rv:
            anchor = "刚才走过的地方"
        elif branch == "transit":
            anchor = "光换了一档"
        elif branch == "pause":
            anchor = "他不动了"
        elif branch == "desire":
            anchor = "有东西压过来"
        elif branch == "aesthetic":
            anchor = "这一片细节"
        elif branch == "lang":
            anchor = "她刚讲的那件事"
        else:
            anchor = "刚才那句"
        # 一段事的长度：开头 1 格 + 中间 N 段 + 结束 1 格。
        # 普通话题用「线程寿命」；故事线程中间随机 2~4 段（总长 4~6 格），
        # 让每次故事的展开长度不一样，而不是每次都正好四格。
        if branch == "lang":
            total = STORY_MID_MIN + random.randint(0, STORY_MID_MAX - STORY_MID_MIN) + 2
            total = max(4, min(8, total))
        elif branch in PLACE_BRANCHES:
            # 看一个地方：七成第三格就走，三成多看一格、第四格一定走。
            # 以前固定 3 格时，第三格若没收到"该走了"，就会一直约"再看一会"。
            total = 3 if random.random() < PLACE_LEAVE3 else 4
        else:
            # 普通话题（一个招牌/一阵光/一次回访）只给 3 格：开头、1 段中间、收尾。
            # 以前所有线程都是 4 格，同一个风铃/灯串能连着说 4 格，明显过久。
            total = max(2, min(3, int(self.thread_life or THREAD_LIFE)))
        self.thread = {
            "anchor": anchor, "branch": branch, "stance": "",
            "stage": 0, "remaining": total,
            "break_level": THREAD_BREAK, "born": self.tick_no, "total": total,
            "self_ask": [],
            # 故事模式用它把"这一段故事之前"的聊天记录切掉，避免把上一段招牌/气泡
            # 的话题带进故事。普通线程不用。
            # 用整秒：记忆里的 ts 是秒级，带小数会把"同一秒写下的开头"切掉。
            "started_at": int(time.time()),
        }
        return self.thread

    def thread_tick(self):
        if not self.thread:
            return
        self.thread["remaining"] = int(self.thread.get("remaining") or 0) - 1
        if self.thread["remaining"] <= 0:
            self.thread = None

    def thread_advance(self, step):
        """线程推进 = 意图的变化：升起→犹豫→改口→落空→认掉。

        模型在【态】里填的是一个字（升/犹/改/落/认），这里两种写法都收。
        """
        if not self.thread or not step:
            return
        step = str(step).strip()
        alias = {"升": "升起", "犹": "犹豫", "改": "改口", "落": "落空", "认": "认掉"}
        step = alias.get(step, step)
        if step in STAGES:
            self.thread["stage"] = STAGES.index(step)
        # 「认掉」= 这条话说完了，线程当场收掉；其余不动寿命（寿命由 remaining 管，
        # 不然每次开口都续命，线程就永远死不掉，等于没有线程）
        if step == "认掉":
            self.thread["remaining"] = 0

    # ---------- 主入口 ----------

    def tick(self, sig):
        self.tick_no += 1
        # remaining==1 是上一格刚说完"最后一格"，这一格不该再续锚。
        # 原来只判 <=0，于是说完收尾还会多出一格没有线程的 follow。
        if self.thread and int(self.thread.get("remaining") or 0) <= 1:
            self.thread = None
        thr = self.thresholds()
        ev, why, rv = self.detect(sig, thr)
        cleared = self.pending_tick()

        # 当前最该说的事件
        top = max(ev, key=lambda e: PRIO.get(e, 0)) if ev else None
        top_prio = PRIO.get(top, 0) if top else 0

        decision = {
            "tick": self.tick_no, "events": ev, "why": why, "top": top,
            "branch": None, "action": "静默", "preempt": "", "cleared": cleared,
            "thresholds": thr, "activity": self.activity_report(),
            "thread": dict(self.thread) if self.thread else None,
            "pending": [dict(p) for p in self.pending],
        }

        if self.thread and self.thread["remaining"] > 0:
            grab = self._preempt_allowed(ev, sig)
            if grab:
                decision["branch"] = grab
                decision["action"] = "抢占"
                decision["preempt"] = "线程「%s」被 %s 抢占" % (
                    self.thread.get("anchor"), BRANCH_CN.get(grab, grab))
                self._open_thread(grab, sig, rv)
            else:
                decision["branch"] = "follow"
                decision["action"] = "续锚"
                self.thread_tick()
        elif top_prio > 0 or self.pot >= POT_THRESHOLD:
            branch = top or "lang"
            decision["branch"] = branch
            decision["action"] = "开"
            self._open_thread(branch, sig, rv)
            self.pot = 0
        else:
            decision["action"] = "留白"
            self.pot += 1

        # 她起话头的节奏：连着 LANG_EVERY 次都在讲画面上的东西，这一格换她讲自己的事。
        # 不加这条，「读字/气泡/过渡」会吃满所有开口机会，她一辈子没机会讲故事
        # （实测 19 格里 lang 出现 0 次）。
        forced_lang = False
        if decision["action"] in ("开", "抢占") and "bubble" not in ev \
                and self.since_lang >= LANG_EVERY:
            if decision["action"] == "抢占":
                decision["preempt"] = (decision.get("preempt") or "") + " → 让位给闲话"
            decision["branch"] = "lang"
            decision["action"] = "开"
            decision["why"] = (decision.get("why") or []) + [
                "连着 %d 次都在说画面，这一格让她自己起话头" % self.since_lang]
            self._open_thread("lang", sig, rv)
            self.since_lang = 0
            forced_lang = True

        if decision["action"] == "续锚":
            self.pot = 0
        # since_lang 数的是"连着几次都在说画面"。
        # 故事格本身、以及故事线程的续锚，都不是在说画面——不能计入，
        # 否则一条四格的故事会自己攒出三次，下一轮立刻又强制讲故事（"故事过多"就是这么来的）。
        story_like = (decision.get("branch") == "lang") or (
            decision.get("action") == "续锚" and (self.thread or {}).get("branch") == "lang")
        if story_like:
            self.since_lang = 0
        elif decision["action"] in ("开", "抢占", "续锚") and not forced_lang:
            self.since_lang += 1

        # 「想过去看看」：不新增事件、不催他，只是偶尔顺口提一句。
        # 只说"过去"，不说"进去"——进不进去由玩家决定，她只表达想往那边走。
        # 只在**第一轮**（开/抢占，新话题的由头）加成：续锚格再说"想过去看看"，
        # 就会变成第二轮还在请求去看，和"看一会"打架。
        decision["wish"] = ""
        if not story_like and decision.get("action") in ("开", "抢占") \
                and (decision.get("branch") or "") in PLACE_BRANCHES \
                and self.tick_no - self.shop_last >= SHOP_EVERY \
                and random.random() < SHOP_CHANCE:
            decision["wish"] = "过去看看"
            self.shop_last = self.tick_no
            decision["why"] = (decision.get("why") or []) + ["顺口提一句想过去"]

        decision["pot"] = self.pot
        # 起话头/抢占之后线程换了，快照要重拍，否则日志里显示的是上一格的锚
        decision["thread"] = dict(self.thread) if self.thread else None
        self.win.append(sig)
        if len(self.win) > max(self.window_ticks * 2, 48):
            self.win = self.win[-self.window_ticks * 2:]
        self.hist.append((self.tick_no, sig.get("sig")))
        if len(self.hist) > 240:
            self.hist = self.hist[-240:]
        return decision

    # ---------- 对外：给提示词的文本 ----------

    def thread_block(self):
        """挂在她嘴边的那件事。锚是"事件"，stance 是她刚说到哪儿。

        只给锚（"刚才那块字"）不够——模型不知道她已经说了什么，就会另起一句；
        只给 stance 又会被某一句的措辞带跑。两个都给，它才知道"接着说"是接什么。
        """
        t = self.thread
        if not t:
            return ""
        branch = t.get("branch") or ""
        place = branch in PLACE_BRANCHES
        stage = STAGES[min(t.get("stage", 0), len(STAGES) - 1)]
        left = max(0, int(t.get("remaining") or 0))
        total = max(3, min(8, int(t.get("total") or self.thread_life or THREAD_LIFE)))
        # 写成"卡片"而不是"一句话"：原来那句"你现在挂在嘴边的是：刚才有人说话"，
        # 实测模型会直接把"刚才有人说话"当成她的台词念出来。
        out = ["【这一格接着说什么】（下面是提示，不是台词，不要念出来）"]
        out.append("- 起因：%s" % (t.get("anchor") or "刚才那句"))
        stance = live_text(t.get("stance") or "").strip()
        if stance:
            out.append("- 你已经说到：「%s」" % stance)
        if left == total:
            phase = "开头（还剩 %d 格）" % left
        elif left <= 1:
            phase = "结束（还剩 %d 格）" % left
        else:
            mid_no = max(1, total - left)
            mid_total = max(1, total - 2)
            phase = "中间第 %d 段（中间共 %d 段，还剩 %d 格）" % (mid_no, mid_total, left)
        out.append("- 走到：%s · %s" % (stage, phase))
        if left <= 1:
            # 最后一格：不让它再展开，收住就行。
            # 没有这句的时候，故事要么半截断掉，要么硬拖到没劲——因为它不知道自己是第几段。
            if branch == "lang":
                out.append("**这是这件事的最后一格**：用一句「后来」「所以我」「打那以后」把它收住，"
                           "收完就放下，别再起新的事；也别在收尾这句问他问题，"
                           "问句会把话头拖回这个故事里。"
                           "收尾之后可以顺手把话头交回眼前（走吧 / 你牵我走）；"
                           "只把话头交回来，别把故事里的东西和动作带到眼前。")
            elif place:
                out.append("这是最后一格：看够了，该走了。用一句离开收尾"
                           "（看够了，你带我走 / 不看了，我们走吧 / 你牵我往前走），"
                           "别再约「再看一会」。")
            else:
                out.append("这是这件事的最后一格：说一句就收，别再展开，也别另起一件。")
        elif left == total:
            if total <= 2:
                out.append("这是**开头**，也是这一轮唯一一次展开：把这件事说清楚，下一格就收。")
            elif place:
                out.append("这是**开头**：把由头说清楚；想过去看看就说一句，"
                           "别在这一格就开始「看一会」。")
            else:
                out.append("这是**开头**：把这件事的由头说清楚（什么时候、在哪、手里拿着什么），"
                           "别急着讲完，也别在这一格就收。")
        elif place:
            if left <= 2:
                out.append("这是中间最后一小段：看一会儿就够，补一个具体细节；下一格就该走了。")
            else:
                out.append("这是中间：看一会儿就够，补一个具体细节；别一直看，也别约下一轮再看。")
        else:
            out.append("这是中间：接着上一段往下推，加一个具体的细节，不要换到画面里别的东西上，"
                       "也别重新开头。")
        return "\n".join(out).replace("**", "")

    def pending_block(self):
        if not self.pending:
            return ""
        rows = []
        for p in self.pending:
            tag = {"question": "还没答的", "request": "还没做的",
                   "account": "他还欠你一个"}.get(p.get("kind"), "记着")
            rows.append("- %s：%s（%d 格前）" % (
                tag, live_text(p.get("text") or ""), p.get("age", 0)))
        return "\n".join(rows)

    def anchors(self):
        t = self.thread or {}
        out = [t.get("anchor")] if t.get("anchor") else []
        return [x for x in out if x]

    def restore(self, data):
        """从 memory.json 恢复线程/记账（重启不丢）。"""
        data = data or {}
        t = data.get("thread")
        if isinstance(t, dict) and t.get("anchor"):
            # 一段事的长度：落过盘的 total 要接着用，否则重启后"第几段"会凭空重置。
            total = max(3, min(8, int(t.get("total") or self.thread_life or THREAD_LIFE)))
            # 旧版本普通话题是 4 格；新规则普通话题最多 3 格。
            # 看一个地方的线程允许 4 格（第三格没走成时多看一格），别把它压回 3。
            branch = t.get("branch") or ""
            if branch != "lang":
                total = min(total, 4 if branch in PLACE_BRANCHES else 3)
            left = t.get("remaining")
            left = total if left is None else max(0, min(total, int(left)))
            # 只剩 0~1 格 = 收尾那句已经说过了，不要再把它捡回来。
            if left > 1:
                self.thread = {
                    "anchor": str(t.get("anchor"))[:40], "branch": t.get("branch") or "follow",
                    "stance": str(t.get("stance") or "")[:80], "stage": int(t.get("stage") or 0),
                    "remaining": left,
                    "break_level": THREAD_BREAK, "born": self.tick_no, "total": total,
                    "self_ask": list(t.get("self_ask") or [])[:4],
                    "started_at": float(t.get("started_at") or 0),
                }
        self.pending = []
        for p in (data.get("pending") or [])[:PENDING_MAX]:
            if isinstance(p, dict) and p.get("text"):
                self.pending.append({"kind": p.get("kind") or "question",
                                     "text": str(p["text"])[:40],
                                     "born": self.tick_no - int(p.get("age") or 0),
                                     "age": int(p.get("age") or 0),
                                     # born 是上一轮进程的 tick，恢复后不再比对；
                                     # 旧问题不会被挂到新线程上。
                                     "thread_born": None})

    def reset(self):
        """把运行时状态清干净（清空记忆时一起用）。

        只清 `memory.json` 不够：正在跑的 Router 里还挂着 thread（话题锚 + 她已经说到哪）、
        pending（欠的账），下一格它会把这些又写回记忆——于是看起来"清了记忆还在聊刚才那件事"。
        """
        self.win = []
        self.hist = []
        self.thread = None
        self.pending = []
        self.pot = 0
        self.since_lang = 0
        self.shop_last = -999
        self.tick_no = 0
        self.sign_memo = {}
        self.bubble_memo = {}

    def snapshot(self):
        # remaining<=0 表示这一条已经"认掉/说满"：不要再落盘，
        # 否则下次启动 restore 会把它当成一条还活着的话题重新捡起来。
        t = self.thread if (self.thread and int(self.thread.get("remaining") or 0) > 0) else None
        return {
            "thread": ({"anchor": t.get("anchor"), "branch": t.get("branch"),
                        "stance": t.get("stance"), "stage": t.get("stage"),
                        "remaining": t.get("remaining"), "total": t.get("total"),
                        "self_ask": t.get("self_ask") or [],
                        "started_at": t.get("started_at") or 0} if t else None),
            "pending": [{"kind": p.get("kind"), "text": p.get("text"),
                         "age": p.get("age", 0)} for p in self.pending],
            "tick_no": self.tick_no,
        }


def _sig_diff(a, b):
    if not a or not b:
        return 999.0
    n = min(len(a), len(b))
    if n <= 0:
        return 999.0
    return sum(abs(int(a[i]) - int(b[i])) for i in range(n)) / float(n)


def describe(decision):
    """一行日志：动作 / 分支 / 事件 / 依据。"""
    return "t%d %s·%s | %s | 势能%d" % (
        decision.get("tick", 0), decision.get("action", "?"),
        BRANCH_CN.get(decision.get("branch") or "", "—"),
        decision.get("why") and "; ".join(decision["why"]) or "无强信号",
        decision.get("pot", 0))
