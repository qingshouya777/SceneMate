# -*- coding: utf-8 -*-
"""提示词层：所有提示词都是 prompts/ 下的普通文本文件。

v4 相对 v3 只改了清单，不改机制：
- 新增 12_契约 / 65_分支 / 55_线程 / 58_记账 / 68_接住 / 72_信号；
- 删掉 07_目的 / 08_地标（v4 没有"目的"和"跨轮认亲"，那两套是 v3 正面临时补丁的产物）。
"""

import os
import random
import re
import time

import config as cfgmod

FILES = {
    "main": "00_主提示词.txt",
    "contract": "12_契约.txt",
    "call": "05_称呼.txt",
    "call_state": "06_称呼状态.txt",
    "aesthetic": "20_风格.txt",
    "life": "25_她的生活.txt",
    "extra": "30_补充.txt",
    "silence": "35_沉默倾向.txt",
    "shapes": "40_句式池.txt",
    "memory_rules": "50_记忆说明.txt",
    "thread": "55_线程.txt",
    "pending": "58_记账.txt",
    "output_format": "60_输出格式.txt",
    "branches": "65_分支.txt",
    "handoff": "68_接住.txt",
    "compress": "70_记忆压缩.txt",
    "signals": "72_信号.txt",
    "force": "80_强制开口.txt",
    "current_frame": "95_当前一帧.txt",
}

META = [
    ("main", "整体骨架", "各段放在哪里"),
    ("contract", "契约", "她有什么、没有什么；先验；两条线"),
    ("call", "称呼", "怎么叫他，多久叫一次"),
    ("call_state", "称呼状态", "最近叫过/没叫过时分别加什么"),
    ("aesthetic", "说话的样子", "短句、别金句、别对仗"),
    ("life", "她是谁", "性格背景，不许当眼前事实"),
    ("extra", "补充", "临时加的要求，留空就不加"),
    ("silence", "话量", "rare / normal / often 三档"),
    ("shapes", "起手式", "一行一条，随机抽一条"),
    ("memory_rules", "记忆说明", "怎么用说过的话"),
    ("thread", "线程", "当前挂在嘴边的那件事"),
    ("pending", "记账", "请求/问/欠账 的清算队列"),
    ("output_format", "输出格式", "三行：说 / 态 / 据"),
    ("branches", "分支", "=== 分段，按事件类型取一段"),
    ("handoff", "接住上一句", "A1 的提示部分"),
    ("compress", "记忆压缩", "长期记忆快满时怎么合并"),
    ("signals", "信号", "把画面信号翻成她身上的感觉"),
    ("force", "强制开口", "连续沉默后逼她说话"),
    ("current_frame", "当前一帧", "最后一条 user 消息里的提示"),
]

README = "_占位符说明.txt"

_cache = {}
_ensured_dirs = set()


def _read(path):
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    if data[:2] == bytes([0xFF, 0xFE]) or data[:2] == bytes([0xFE, 0xFF]):
        return data.decode("utf-16", "replace")
    if data[:3] == bytes([0xEF, 0xBB, 0xBF]):
        return data.decode("utf-8-sig", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", "replace")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text or "", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _default_path(key):
    name = FILES.get(key)
    if not name:
        return None
    return cfgmod.DEFAULT_PROMPT_DIR / name


def _path(key):
    name = FILES.get(key)
    if not name:
        return None
    return cfgmod.PROMPT_DIR / name


def ensure_files(cfg=None):
    cfgmod.PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    legacy = (cfg or {}).get("prompt") or {}
    for key, name in FILES.items():
        target = cfgmod.PROMPT_DIR / name
        if target.exists():
            continue
        text = None
        if key in ("aesthetic", "extra"):
            v = (legacy.get(key) or "").strip()
            if v:
                text = v + "\n"
        if text is None:
            src = _default_path(key)
            text = _read(src) if src and src.exists() else ""
        _write(target, text)

    readme = cfgmod.PROMPT_DIR / README
    if not readme.exists():
        src = cfgmod.DEFAULT_PROMPT_DIR / README
        if src.exists():
            _write(readme, _read(src))
    _ensured_dirs.add(str(cfgmod.PROMPT_DIR))
    return cfgmod.PROMPT_DIR


def _ensure_once():
    if str(cfgmod.PROMPT_DIR) in _ensured_dirs:
        return
    if not cfgmod.PROMPT_DIR.exists() or not (cfgmod.PROMPT_DIR / FILES["main"]).exists():
        try:
            ensure_files(None)
        except Exception:
            pass


def load_all(force=False):
    _ensure_once()
    out = {}
    for key, name in FILES.items():
        path = cfgmod.PROMPT_DIR / name
        try:
            mtime = path.stat().st_mtime_ns
        except Exception:
            mtime = 0
        hit = _cache.get(key)
        if force or not hit or hit.get("mtime") != mtime or hit.get("file") != name:
            text = _read(path) if mtime else ""
            hit = {"file": name, "mtime": mtime, "text": text}
            _cache[key] = hit
        out[key] = hit["text"]
    return out


def reload_all():
    _cache.clear()
    return load_all(force=True)


def read_file(key):
    path = _path(key)
    return _read(path) if path and path.exists() else ""


def read_readme():
    path = cfgmod.PROMPT_DIR / README
    return _read(path) if path.exists() else ""


def readme_path():
    return cfgmod.PROMPT_DIR / README


def readme_mtime_ns():
    try:
        return readme_path().stat().st_mtime_ns
    except Exception:
        return 0


def save_readme(text):
    path = readme_path()
    _write(path, text)
    return path


def restore_readme():
    src = cfgmod.DEFAULT_PROMPT_DIR / README
    text = _read(src) if src.exists() else ""
    save_readme(text)
    return text


def save_file(key, text):
    path = _path(key)
    if path is None:
        raise KeyError(key)
    _write(path, text)
    try:
        _cache[key] = {"file": path.name, "mtime": path.stat().st_mtime_ns, "text": text}
    except Exception:
        _cache.pop(key, None)
    return path


def restore_file(key):
    path = _path(key)
    src = _default_path(key)
    if path is None:
        raise KeyError(key)
    text = _read(src) if src and src.exists() else ""
    save_file(key, text)
    return text


def mtime_ns(key):
    path = _path(key)
    try:
        return path.stat().st_mtime_ns if path else 0
    except Exception:
        return 0


def mtime_text(key):
    ns = mtime_ns(key)
    if not ns:
        return "未创建"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ns / 1e9))


def file_info():
    rows = []
    pm = load_all()
    for key, name in FILES.items():
        text = pm.get(key, "")
        rows.append({
            "key": key, "file": name, "path": str(_path(key) or ""),
            "exists": bool(mtime_ns(key)), "mtime": mtime_ns(key),
            "mtime_text": mtime_text(key), "chars": len(text),
            "tokens": estimate_tokens(text),
        })
    return rows


def parse_sections(text):
    """解析 === name 分段。没有分段标记时，整段算 normal。"""
    if not text or not text.strip():
        return {}
    sections = {}
    cur = None
    buf = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        # 注意：这里不能把 "#" 开头的行当注释丢掉。
        # 契约正文里的 "## 你有的 / ## 一条先验" 就是靠这些行分段的，
        # 过滤掉之后整段契约会变成一串没有标题的列表（之前一直是这个状态）。
        m = re.match(r"^===\s*([A-Za-z_]+)\s*$", s)
        if m:
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur = m.group(1).lower()
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    else:
        sections["normal"] = "\n".join(buf).strip()
    return sections


def pick_shape(text):
    if not text:
        return ""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return random.choice(lines) if lines else ""


def render(template, values):
    """只认 {{name}} 形式的占位符；不认识的直接去掉。"""
    out = template or ""
    values = values or {}
    for _ in range(4):
        changed = False
        for k, v in values.items():
            token = "{{%s}}" % k
            if token in out:
                out = out.replace(token, "" if v is None else str(v))
                changed = True
        if not changed:
            break
    out = re.sub(r"\{\{[A-Za-z0-9_]+\}\}", "", out)
    out = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", out)
    return out.strip()


def estimate_tokens(text):
    """中文按 0.62 token/字，其余按 0.32。够用来做预算。"""
    if not text:
        return 0
    cjk = len(re.findall(
        r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]",
        text))
    other = max(0, len(text) - cjk)
    return int(cjk * 0.62 + other * 0.32 + 2)


def estimate_image_tokens(w, h, detail="high"):
    try:
        w, h = int(w), int(h)
    except Exception:
        return 0
    if w <= 0 or h <= 0:
        return 0
    if str(detail).lower() == "low":
        return 190
    import math
    k = min(1.0, 2048.0 / max(w, h))
    w2, h2 = max(1, int(w * k)), max(1, int(h * k))
    tiles = math.ceil(w2 / 512.0) * math.ceil(h2 / 512.0)
    return int(85 + 170 * tiles)


def open_folder():
    try:
        cfgmod.PROMPT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(cfgmod.PROMPT_DIR))
    except Exception:
        pass
