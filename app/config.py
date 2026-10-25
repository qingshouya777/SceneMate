# -*- coding: utf-8 -*-
"""配置读写。首次运行自动生成 config.json。

v4 的默认值相对 v3 改了四处（对应契约 D4/D5/D7/D8）：
- `vlm.detail`：low → **high**。三环代理和读字都依赖看清，这是所有视觉改动的前置硬条件。
- `vlm.temperature`：0.85 → **0.6**。0.85 在多重约束下只会随机踩雷。
- `behavior.sentences_per_take`：2 → **3**，交给显示层排开播（不是当格说完）。
- `vlm.api_key`：**不再落盘**。只认环境变量 `DEEPSEEK_API_KEY`；旧的明文 key 会被就地删掉，
  并在 config.json 里留一个 `api_key_purged` 标记提醒去轮换。
"""

import json
import os
import hashlib
import shutil
import sys
from pathlib import Path

_home = (os.environ.get("HERWORLD_HOME") or "").strip()
if _home and os.path.isdir(_home):
    BASE = Path(_home).resolve()
elif getattr(sys, "frozen", False):
    BASE = Path(os.path.dirname(os.path.abspath(sys.executable)))
else:
    BASE = Path(__file__).resolve().parent

# 配置/记忆/密钥放用户数据目录，换解压目录或覆盖程序都不会丢。
# 不想用默认目录时，启动前设置 HERWORLD_DATA 即可。
def _usable_data_dir(path):
    try:
        p = Path(path).resolve()
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return p
    except Exception:
        return None


_data = (os.environ.get("HERWORLD_DATA") or "").strip()
if _data:
    # 便携包可能被放在低权限目录，写不了 LOCALAPPDATA；
    # 这时退回程序自己的 data 目录，至少同目录重启不会丢。
    DATA = _usable_data_dir(_data) or _usable_data_dir(BASE / "data") or BASE
else:
    DATA = BASE


def _migrate_data():
    """第一次切到数据目录时，把程序目录里的旧配置/记忆/key 搬过去。"""
    if DATA == BASE:
        return
    # 每个目标目录单独记一次迁移，避免用户删掉数据后又被旧目录自动灌回来；
    # 换电脑/换数据目录时目标变了，仍会从旧目录再迁一次。
    tag = hashlib.md5(str(DATA).lower().encode("utf-8")).hexdigest()[:12]
    marker = DATA / ".data_migrated"
    base_marker = BASE / (".data_migrated_" + tag)
    if marker.exists() or base_marker.exists():
        return
    sources = [BASE]
    alt = BASE / "data"
    if alt != DATA:
        sources.append(alt)
    for srcbase in sources:
        for name in ("config.json", "config.bak.json", "memory.json",
                     "state.json", "keys.dat", "flow_baseline.json"):
            src = srcbase / name
            dst = DATA / name
            try:
                if src.is_file() and not dst.exists():
                    shutil.copy2(str(src), str(dst))
            except Exception:
                pass
    for m in (marker, base_marker):
        try:
            m.write_text("1", encoding="utf-8")
        except Exception:
            pass


_migrate_data()


def _find_default_prompt_dir():
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass) / "prompt_defaults")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        cands.append(exe_dir / "prompt_defaults")
        cands.append(exe_dir / "_internal" / "prompt_defaults")
    script_dir = Path(__file__).resolve().parent
    cands.append(script_dir / "prompt_defaults")
    cands.append(BASE / "prompt_defaults")
    for c in cands:
        try:
            if c.is_dir():
                return c
        except Exception:
            pass
    return cands[0]


CFG_PATH = DATA / "config.json"
MEMORY_PATH = DATA / "memory.json"
PROMPT_DIR = BASE / "prompts"
DEFAULT_PROMPT_DIR = _find_default_prompt_dir()

DEFAULT = {
    "version": 4,
    "vlm": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key": "",
        "api_key_env": "DEEPSEEK_API_KEY",
        "context_k": 128,
        "detail": "high",
        "thinking_style": "auto",
        "reasoning_effort": "none",
        "temperature": 0.6,
        "max_tokens": 500,
        "timeout": 30,
    },
    "capture": {
        "mode": "region",
        "window_title": "",
        "region": {"left": 0, "top": 0, "width": 0, "height": 0},
        "interval": 5,
        "jitter": 3,
        "story_interval": 2.5,
        "story_jitter": 0.8,
        "max_side": 2400,
        "scale": 0.75,
        "jpeg_quality": 88,
        "min_mean": 14,
        "dup_threshold": 2.2,
        "blur_threshold": 9.0,
    },
    "overlay": {
        "x_pct": 25,
        "y_pct": 66,
        "font_size": 0,
        "max_width_pct": 54,
        "duration": 5.0,
        "story_duration": 2.0,
        "auto_duration": True,
        "show_name": True,
        "stream": True,
        "stream_mode": "accumulate",
        "stream_cps": 20,
        "stream_pause": 1.0,
    },
    "personas": [],
    "active_persona": 0,
    "persona": {
        "name": "浔",
        "call_player": "招财宝",
        "relation": "和你牵着手逛街的约会对象",
        "tone": "短句，偶尔嘴硬，不说甜言蜜语，不撒娇",
    },
    "prompt": {"aesthetic": "", "extra": ""},
    "memory": {
        "budget_k": 12,
        "digest_share_pct": 40,
        "auto_compress": True,
        "compress_trigger_pct": 85,
        "compress_max_chars": 2500,
        "compress_chunk_tokens": 6000,
        "persist": True,
        "include_time": True,
        "record_silent": True,
        "silent_gap_sec": 90,
        "max_entries": 4000,
    },
    "behavior": {
        "force_speak_after_skips": 4,
        "manual_min_gap": 3,
        "silence": "normal",
        "max_line_chars": 60,
        "sentences_per_take": 3,
    },
    "router": {
        "window_ticks": 24,
        "thread_life_ticks": 3,
        "flow_floor": None,
    },
    "hotkeys": {
        "shoot": "F8",
        "pause": "F9",
        "clear": "F10",
        "prompts": "ALT+P",
    },
    "misc": {
        "demo": False,
        "start_auto": False,
        "control_bar": True,
    },
}


def _merge(base, patch):
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _as_int(v, default, lo=None, hi=None):
    try:
        n = int(float(v))
    except Exception:
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _as_float(v, default, lo=None, hi=None):
    try:
        n = float(v)
    except Exception:
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def context_k(cfg):
    return _as_int((cfg.get("vlm") or {}).get("context_k"), 128, 4, 4096)


def memory_budget_k(cfg):
    return _as_int((cfg.get("memory") or {}).get("budget_k"), 12, 0, context_k(cfg))


def _migrate(cfg):
    if not isinstance(cfg, dict):
        cfg = json.loads(json.dumps(DEFAULT))
    try:
        import providers as provmod
    except Exception:
        provmod = None

    cfg.setdefault("vlm", {})
    v = cfg["vlm"]
    if not str(v.get("provider") or "").strip():
        v["provider"] = (provmod.guess_from_base_url(v.get("base_url"))
                         if provmod else "deepseek")
    v["context_k"] = context_k(cfg)
    v["timeout"] = _as_float(v.get("timeout"), 30, 5, 180)
    v["detail"] = str(v.get("detail") or "high").lower()
    if v["detail"] not in ("low", "high"):
        v["detail"] = "high"
    v["thinking_style"] = str(v.get("thinking_style") or "auto").lower()
    if v["thinking_style"] not in ("auto", "deepseek", "standard", "none"):
        v["thinking_style"] = "auto"
    v["temperature"] = _as_float(v.get("temperature"), 0.6, 0.0, 1.5)
    # D8：明文 key 不落盘。旧配置里的就地删掉，留个标记让人去轮换。
    old_key = str(v.get("api_key") or "").strip()
    if old_key:
        v["api_key_purged"] = True
        v["api_key_purged_hint"] = "旧版把 key 明文写在 config.json 里，v4 已删除；请到平台轮换这把 key，再把新的填在「设置 - 连接」页或环境变量里。"
    # 连空字段都不留：config.json 里出现 api_key 这三个字，看的人就会以为 key 存在这儿
    v.pop("api_key", None)

    cfg.setdefault("capture", {})
    cap = cfg["capture"]
    cap["interval"] = _as_int(cap.get("interval"), 5, 3, 120)
    cap["jitter"] = _as_int(cap.get("jitter"), 3, 0, 30)
    # 故事线程单独节奏：4~6 格的中间段落不必和普通格一样等 5 秒。
    cap["story_interval"] = _as_float(cap.get("story_interval"), 2.5, 1.0, 30.0)
    cap["story_jitter"] = _as_float(cap.get("story_jitter"), 0.8, 0.0, 10.0)
    cap["max_side"] = _as_int(cap.get("max_side"), 2400, 320, 4096)
    cap["jpeg_quality"] = _as_int(cap.get("jpeg_quality"), 88, 40, 100)

    cfg.setdefault("overlay", {})
    o = cfg["overlay"]
    o["auto_duration"] = bool(o.get("auto_duration", True))
    o["story_duration"] = _as_float(o.get("story_duration"), 2.0, 1.0, 20.0)
    o["stream"] = bool(o.get("stream", True))
    o["stream_mode"] = str(o.get("stream_mode") or "accumulate").lower()
    if o["stream_mode"] not in ("accumulate", "replace"):
        o["stream_mode"] = "accumulate"
    o["stream_cps"] = _as_int(o.get("stream_cps"), 20, 5, 60)
    o["stream_pause"] = _as_float(o.get("stream_pause"), 1.0, 0, 3)

    cfg.setdefault("memory", {})
    m = cfg["memory"]
    m["budget_k"] = memory_budget_k(cfg)
    m["digest_share_pct"] = _as_int(m.get("digest_share_pct"), 40, 10, 70)
    m["compress_trigger_pct"] = _as_int(m.get("compress_trigger_pct"), 85, 30, 100)
    m["compress_max_chars"] = _as_int(m.get("compress_max_chars"), 2500, 300, 12000)
    m["compress_chunk_tokens"] = _as_int(m.get("compress_chunk_tokens"), 6000, 1000, 32000)
    m["max_entries"] = _as_int(m.get("max_entries"), 4000, 100, 200000)
    m["silent_gap_sec"] = _as_int(m.get("silent_gap_sec"), 90, 10, 3600)
    for k in ("auto_compress", "persist", "include_time", "record_silent"):
        m[k] = bool(m.get(k, True))

    cfg.setdefault("behavior", {})
    b = cfg["behavior"]
    b["silence"] = str(b.get("silence") or "normal").lower()
    if b["silence"] not in ("rare", "normal", "often"):
        b["silence"] = "normal"
    b["max_line_chars"] = _as_int(b.get("max_line_chars"), 60, 0, 400)
    b["sentences_per_take"] = _as_int(b.get("sentences_per_take"), 3, 1, 6)
    b["force_speak_after_skips"] = _as_int(b.get("force_speak_after_skips"), 4, 0, 99)
    b["manual_min_gap"] = _as_float(b.get("manual_min_gap"), 3, 0, 60)
    # v3 的目的/地标/去重开关在 v4 没有对应物，就地清掉
    for k in ("goal_ttl_min", "goal_cooldown_sec", "landmark_cooldown_sec",
              "retry_on_repeat", "repeat_threshold", "dedupe_window"):
        b.pop(k, None)

    cfg.setdefault("router", {})
    r = cfg["router"]
    r["window_ticks"] = _as_int(r.get("window_ticks"), 24, 6, 120)
    # 普通话题 2~3 格；故事线程另外随机 4~6 格，不受这个值控制。
    r["thread_life_ticks"] = _as_int(r.get("thread_life_ticks"), 3, 2, 3)
    try:
        r["flow_floor"] = float(r["flow_floor"]) if r.get("flow_floor") else None
    except Exception:
        r["flow_floor"] = None

    cfg.setdefault("hotkeys", {})
    cfg["hotkeys"].setdefault("shoot", "F8")
    cfg["hotkeys"].setdefault("pause", "F9")
    cfg["hotkeys"].setdefault("clear", "F10")
    cfg["hotkeys"].setdefault("prompts", "ALT+P")

    cfg.setdefault("misc", {})
    cfg["misc"].setdefault("control_bar", True)

    ps = cfg.get("personas")
    if not isinstance(ps, list) or not ps:
        ps = [dict(cfg.get("persona") or {})]
    ps = [x if isinstance(x, dict) else {} for x in ps]
    cfg["personas"] = ps
    i = _as_int(cfg.get("active_persona"), 0, 0, len(ps) - 1)
    cfg["active_persona"] = i
    for per in ps:
        per.pop("samples", None)      # 从来没进过提示词，删掉免得看着像能用
    base = dict(DEFAULT.get("persona") or {})
    base.update(ps[i] or {})
    cfg["persona"] = base

    cfg["version"] = 4
    return cfg


def load():
    if not CFG_PATH.exists():
        cfg = _migrate(json.loads(json.dumps(DEFAULT)))
        save(cfg)
        return cfg
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = CFG_PATH.read_text(encoding=enc)
        except Exception:
            continue
        try:
            raw = json.loads(text)
            break
        except Exception:
            continue
    if raw is None:
        try:
            shutil.copyfile(str(CFG_PATH), str(CFG_PATH.with_suffix(".bad.json")))
        except Exception:
            pass
        cfg = _migrate(json.loads(json.dumps(DEFAULT)))
        save(cfg)
        return cfg
    return _migrate(_merge(DEFAULT, raw))


def save(cfg):
    cfg = _migrate(cfg)
    try:
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        if CFG_PATH.exists():
            shutil.copyfile(str(CFG_PATH), str(CFG_PATH.with_suffix(".bak.json")))
    except Exception:
        pass
    tmp = CFG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(CFG_PATH))
    return cfg


def provider_id(cfg):
    v = cfg.get("vlm") or {}
    try:
        import providers as provmod
        return (v.get("provider") or provmod.guess_from_base_url(v.get("base_url"))
                or provmod.DEFAULT_ID)
    except Exception:
        return v.get("provider") or "deepseek"


def api_key_env(cfg):
    """这个接入方的环境变量名。

    以接入方预设为准（换接入方时环境变量名要跟着换，不能留在上家的名字上），
    只有预设里没写才退回配置里存的值。
    """
    try:
        import providers as provmod
        env = (provmod.get(provider_id(cfg)).get("key_env") or "").strip()
        if env:
            return env
    except Exception:
        pass
    return ((cfg.get("vlm") or {}).get("api_key_env") or "DEEPSEEK_API_KEY").strip()


def api_key(cfg):
    """key 从哪来：先看环境变量，再看本机加密存储（keys.dat）。

    **永远不读 config.json**——那里面是明文，会被备份和打包带走。
    """
    env = api_key_env(cfg)
    val = (os.environ.get(env) or "").strip() if env else ""
    if val:
        return val
    try:
        import keystore
        return (keystore.get(provider_id(cfg)) or "").strip()
    except Exception:
        return ""


def api_key_source(cfg):
    """("env"|"store"|"none", 说明)。界面和自检都用它。"""
    env = api_key_env(cfg)
    if env and (os.environ.get(env) or "").strip():
        return "env", "环境变量 %s" % env
    try:
        import keystore
        if keystore.which(provider_id(cfg)):
            return "store", "本机加密存储（keys.dat，DPAPI）"
    except Exception:
        pass
    return "none", "还没配"


def api_key_hint(cfg):
    src, how = api_key_source(cfg)
    if src == "env":
        return "已读到 key（%s）" % how
    if src == "store":
        return "已读到 key（%s）" % how
    return "没读到 key（现在会是演示模式）· 去设置 → 连接 里填"
