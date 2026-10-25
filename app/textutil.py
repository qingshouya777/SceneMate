# -*- coding: utf-8 -*-
"""文本工具：去标点、二元组、相似度。

这三个函数原来在 `memory.py` 和 `contract.py` 里各有一份，实现一样（只差变量名）。
放在这里，两边共用一个定义——改规则的时候不会只改到一半。

只依赖 re，没有任何项目内依赖，谁都能导。
"""

import re

_PUNCT = re.compile(r"[\s，。！？、；：“”‘’\"'.,!?;:~…—\-_/\\()（）\[\]【】《》<>]+")


def norm(s):
    """去掉标点和空白，转小写。用来做"是不是同一句"的比较。"""
    return _PUNCT.sub("", (s or "").lower())


def bigrams(s):
    """二元组集合。中文不分词，用它近似"有没有共用实词"。"""
    s = norm(s)
    if not s:
        return set()
    if len(s) < 2:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a, b):
    """0~1。完全相同=1；一方包含另一方=0.92；否则按二元组 Jaccard。"""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return 0.92
    A, B = bigrams(na), bigrams(nb)
    if not A or not B:
        return 0.0
    return len(A & B) / float(len(A | B))


def stance_text(lines, limit=80):
    """线程的「你已经说到」：优先取最后一句，别在句子中间硬切。

    原来 main 里是 `"".join(lines)[:40]`，真实故事里会变成
    「…地上全是水，鞋边都溅」——模型看到半句，续写很容易跟着半句走。
    lines 通常是已经切好的句子列表（句末标点已被去掉），所以直接取最后一项；
    传字符串时再按标点找最后一句。
    """
    if isinstance(lines, (list, tuple)):
        items = [str(x or "").strip() for x in lines if str(x or "").strip()]
        text = items[-1] if items else ""
    else:
        text = str(lines or "").strip()
    if len(text) <= limit:
        return text
    parts = [p.strip() for p in re.split(r"[。！？…\n]", text) if p.strip()]
    if parts and len(parts[-1]) <= limit:
        return parts[-1]
    return "…" + text[-limit:]


# 旧记忆里的"进去看看"会作为历史消息继续喂给模型，让它接着模仿。
# 读取时统一改成"过去"：只动"想进店/带我进"这类意向句，不动"放进去/走进去"。
_WISH_FIXES = (
    ("带我进去", "带我过去"),
    ("陪我进去", "陪我过去"),
    ("你进去看看", "你过去看看"),
    ("进去看看", "过去看看"),
    ("进去吧", "过去吧"),
    ("进去站", "过去站"),
    ("进去坐", "过去坐"),
    ("进店", "过去"),
)

_QUOTED = re.compile(r"([「『“\"][^」』”\"]{1,24}[」』”\"])")


def _replace_outside_quotes(text, fixes):
    parts = _QUOTED.split(str(text or ""))
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:          # 引号里的招牌/气泡原文
            out.append(seg)
            continue
        for a, b in fixes:
            seg = seg.replace(a, b)
        out.append(seg)
    return "".join(out)


def wish_text(text):
    """把历史里的"进去"意向句改成"过去"，引号里的原文不动。"""
    return _replace_outside_quotes(text, _WISH_FIXES)


# 历史里的"停住"说法会继续示范给模型；读取时换成仍在走的身体感觉。
_MOVE_FIXES = (
    ("腿有点挪不动了", "腿有点沉"),
    ("腿都挪不动了", "腿有点沉"),
    ("腿挪不动了", "腿有点沉"),
    ("就挪不动脚了", "脚步就慢了半拍"),
    ("挪不动脚了", "脚步慢了半拍"),
    ("挪不动脚", "脚步慢了半拍"),
    ("挪不动了", "脚步慢了半拍"),
    ("走不动了", "腿有点沉"),
    ("走不了", "腿有点沉"),
    ("迈不开", "脚步慢了半拍"),
    ("迈不动", "脚步慢了半拍"),
    ("不想走了", "想走慢一点"),
)


def move_text(text):
    """把历史里的"走不动/挪不动"改成"腿沉/慢半拍"，引号里的原文不动。"""
    return _replace_outside_quotes(text, _MOVE_FIXES)


# 历史里的"踩到/踩着垫子"会继续示范给模型；读取时整句删掉，
# 只保留平衡感的句子（脚下一软、滑了一下、没踩稳）。
_STEP_SENT_RE = re.compile(
    r"[^。！？!?…\n]*(?:踩到|踩在|踩着|踩上|一踩|踩了|踩下去|踩出|"
    r"(?:脚底|脚下)[^。！？!?…\n]{0,12}(?:垫子|毯子|花瓣|石子|石头|水坑|"
    r"草地|地砖|木板|沙子|泥土|换成|变成|软得|硬得))"
    r"[^。！？!?…\n]*[。！？!?…]?"
)


def step_text(text):
    """删掉历史里靠脚感推出来的句子，引号里的招牌原文不动。"""
    parts = _QUOTED.split(str(text or ""))
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            out.append(seg)
            continue
        out.append(_STEP_SENT_RE.sub("", seg))
    return "".join(out).strip()


# 手心黏/湿是同一类万用身体句，历史里也别再示范；"手心出汗"保留。
_STOCK_SENT_RE = re.compile(
    r"[^。！？!?…\n]*(?:手心黏|手还黏|手心湿|手心还湿|手心有点黏|手心有点湿)"
    r"[^。！？!?…\n]*[。！？!?…]?"
)


def stock_text(text):
    parts = _QUOTED.split(str(text or ""))
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            out.append(seg)
            continue
        out.append(_STOCK_SENT_RE.sub("", seg))
    return "".join(out).strip()


# 旧记忆里的"再看一眼/陪我看会儿"会继续示范，让她每一格都在约下一眼。
# 只删"再来一次"的句子；第一轮的"就看看"和带回忆标记的句子保留。
_PLACE_LOOK_SENT_RE = re.compile(
    r"[^。！？!?…\n]*(?:"
    r"(?:再|多)(?:看|瞅|瞄|逛|待)|"
    r"陪(?:我|你)?看(?:一|两)?(?:会|眼|下)|"
    r"看(?:一|两)?(?:会|眼)(?:儿)?(?:再|就|才)|"
    r"看两眼|瞄一眼"
    r")[^。！？!?…\n]*[。！？!?…]?"
)
_PLACE_RECALL_HINTS = ("以前", "那次", "小时候", "上次", "那天", "那年",
                       "当时", "记得", "后来")


def _drop_place_look(m):
    s = m.group(0)
    if any(k in s for k in _PLACE_RECALL_HINTS):
        return s
    return ""


def place_text(text):
    """删掉历史里"再看一眼就走 / 陪我看会儿"这类句子，引号里的原文不动。"""
    parts = _QUOTED.split(str(text or ""))
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            out.append(seg)
            continue
        out.append(_PLACE_LOOK_SENT_RE.sub(_drop_place_look, seg))
    return "".join(out).strip()


def live_text(text):
    """历史进提示词前的统一清洗：进去→过去、走不动→腿沉、踩到什么/手心湿/再看→删句。"""
    return place_text(stock_text(step_text(move_text(wish_text(text)))))
