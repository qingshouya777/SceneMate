# -*- coding: utf-8 -*-
"""大脑层：拼提示词、调 VLM、把三行输出拆开并跑两条硬判据。

**D1 的整改**：v3 在这里有一段"反认亲补丁"（把"刚才/还是/终于/回到/又回"一律封杀），
结果她每次想接自己的话都被按住——这才是 29 次调用产不出回调的真正原因。v4 把它
整段删掉，改成在契约里划两条线（回忆可以编，眼前不能编），并把「接住上一句」做成
代码判据（见 contract.check_reference）。

**A1/A3 在这里落地**：模型输出必须先过两条判据才准显示；不过就带着具体理由重说一次，
再不过就这一格不说——宁可沉默，也不要又冒出一句接不上的话。
"""

import base64
import json
import random
import re
import time
import urllib.error
import urllib.request

from PIL import Image, ImageStat

import contract
import prompts

VALID_MOODS = ["开心", "平静", "害羞", "感动", "难过", "生气", "惊讶"]

# 说明：build_prompt/ask 都留着 **legacy，是为了让 settings_ui 的预览继续能跑。
# v3 的 goal_detail / landmark_state 这些参数收下但不参与拼装——v4 没有目的和地标。


def build_prompt(cfg, system_memory="", force_speak=False, prompt_map=None,
                 retry_note="", shape=None, recent_lines=None,
                 memory_block=None, frame_count=1,
                 decision=None, sig=None, router=None, story=False,
                 story_exit=False, **legacy):
    """拼 system 消息。最近说过的话作为真正的 user/assistant 消息另发。"""
    pm = prompt_map or prompts.load_all()
    if not (pm.get("main") or "").strip():
        return ""
    if memory_block is not None and not system_memory:
        system_memory = memory_block
    branch0 = (decision or {}).get("branch") if decision else None
    # 起手式只在"接话"这种没有明确引子的格子里给。
    # 读字/气泡/过渡已经有引子了，再塞一句"先说身上一个感觉"就是两个方向抢方向盘；
    # 故事格/故事出口自己有指令，也不要（调用方就算传了 shape，也丢掉）。
    if story or story_exit:
        shape = ""
    elif shape is None and branch0 in ("follow", None):
        shape = prompts.pick_shape(pm.get("shapes") or "")

    blocks = contract.build_blocks(
        cfg, pm, router=router, decision=decision, sig=sig,
        recent_lines=recent_lines, system_memory=system_memory,
        force=force_speak, shape=shape, story=story, story_exit=story_exit,
    )
    if retry_note:
        blocks["handoff_block"] = (blocks.get("handoff_block", "")
                                   + "\n（刚才那句没过：%s）" % retry_note).strip()
    values = dict(blocks)
    values["name"] = (cfg.get("persona") or {}).get("name") or "她"
    values["relation"] = ((cfg.get("persona") or {}).get("relation") or "").strip()
    values["output_format"] = (pm.get("output_format") or "").strip()
    return prompts.render(pm.get("main") or "", values)


def current_note_for(cfg, prompt_map=None, recent_lines=None, override=None,
                     frame_count=1, story=False, story_exit=False, **legacy):
    pm = prompt_map or prompts.load_all()
    if story_exit:
        # 故事刚收尾：最后一条消息必须站在"回到眼前"这边，否则模型会继续讲袋口/橘子。
        return "（刚才那段以前的事已经讲完了。这一格回到眼前：别接着故事里的东西，看眼前。）"
    if story:
        # 最后一条 user 消息权重最高。默认那句是"看一眼，说一到三句"，
        # 故事格要是留着它，模型就会去描述/接续画面，前面的说明全白写。
        # 措辞要和 65_分支 / story_note 一致：讲"最近的事"，别往童年跑。
        return "（这一格不用看画面。讲一件最近的事，跟眼前没关系。别报年龄。）"
    note = (override or pm.get("current_frame") or "").strip()
    if not note:
        note = "（现在。只有这一张。看一眼，说一到三句；真没什么可说的，就一句。）"
    call = ((cfg.get("persona") or {}).get("call_player") or "").strip()
    if call and call != "你" and any(call in str(x or "") for x in (recent_lines or [])[:3]):
        used = (prompts.parse_sections(pm.get("call_state") or "").get("used") or "").strip()
        if used:
            note += "\n" + used
    return note


def build_messages(system_prompt, history, img_bytes, note, detail="high", with_image=True):
    messages = [{"role": "system", "content": system_prompt}]
    for m in (history or []):
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    frames = img_bytes if isinstance(img_bytes, (list, tuple)) else [img_bytes]
    frames = [f for f in frames if f] if with_image else []
    parts = [{"type": "text", "text": note}]
    for frame in frames:
        b64 = base64.b64encode(frame).decode("ascii")
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + b64, "detail": detail}})
    messages.append({"role": "user", "content": parts})
    return messages


def render_transcript(system_prompt, history, current_note, image_info=""):
    out = ["[system]", system_prompt or ""]
    for m in (history or []):
        out += ["", "[%s]" % (m.get("role") or "user"), m.get("content") or ""]
    out += ["", "[user]", current_note or ""]
    if image_info:
        out.append("[图片] " + image_info)
    return "\n".join(out)


def estimate_prompt_tokens(system_prompt, history, current_note, image_size=None,
                           detail="high", image_count=1):
    tokens = prompts.estimate_tokens(system_prompt or "")
    tokens += prompts.estimate_tokens(current_note or "")
    for m in (history or []):
        tokens += prompts.estimate_tokens(m.get("content") or "") + 8
    tokens += 16
    if image_size:
        tokens += prompts.estimate_image_tokens(image_size[0], image_size[1], detail) \
            * max(1, int(image_count or 1))
    return tokens


# ---------- HTTP ----------

def _url(cfg):
    """所有接入方都走 OpenAI 兼容的 /chat/completions。"""
    import providers
    base = ((cfg.get("vlm") or {}).get("base_url") or "").strip()
    if not base:
        base = providers.get(cfg.get("vlm", {}).get("provider")).get("base_url") or ""
    return providers.completions_url(base) or "https://api.deepseek.com/chat/completions"


def _thinking_fields(cfg):
    v = cfg.get("vlm") or {}
    style = str(v.get("thinking_style") or "auto").lower()
    effort = str(v.get("reasoning_effort") or "none").lower()
    pid = str(v.get("provider") or "").lower()
    base = str(v.get("base_url") or "").lower()
    if not pid:                      # 老配置没有 provider 字段
        pid = "deepseek" if "deepseek" in base else "other"
    if style == "none":
        return {}
    if style == "auto":
        style = "deepseek" if pid == "deepseek" else "standard"
    if style == "deepseek":
        if effort in ("", "none", "off", "disabled"):
            return {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    if style == "standard" and effort not in ("", "none", "off", "disabled"):
        return {"reasoning_effort": effort}
    return {}


def _thinking_off(cfg):
    """探针/重试用：明确关掉 DeepSeek 的思考，别让 reasoning 吃掉输出预算。"""
    v = cfg.get("vlm") or {}
    pid = str(v.get("provider") or "").lower()
    base = str(v.get("base_url") or "").lower()
    if pid == "deepseek" or "deepseek" in base:
        return {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}
    return {}


def _post_json(url, payload, api_key, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return {"ok": True, "raw": raw, "latency": round(time.time() - t0, 2), "status": 200}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        return {"ok": False, "status": int(e.code), "error": "HTTP %d %s" % (e.code, detail),
                "detail": detail, "latency": round(time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "status": 0, "error": "网络: %s" % e,
                "latency": round(time.time() - t0, 2)}


def _request_with_fallback(cfg, api_key, payload, timeout):
    res = _post_json(_url(cfg), payload, api_key, timeout)
    if res.get("ok") or res.get("status") != 400:
        return res
    detail = (res.get("detail") or "").lower()
    p2 = json.loads(json.dumps(payload))
    changed = False
    if "thinking" in p2 or "reasoning_effort" in p2:
        p2.pop("thinking", None)
        p2.pop("reasoning_effort", None)
        changed = True
    if "temperature" in detail and "temperature" in p2:
        p2.pop("temperature", None)
        changed = True
    return _post_json(_url(cfg), p2, api_key, timeout) if changed else res


def _content_of(obj):
    try:
        c = obj["choices"][0]["message"].get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(str(p.get("text") or "") if isinstance(p, dict) else str(p)
                           for p in c)
    except Exception:
        pass
    return ""


def _open_stream(cfg, api_key, payload, timeout):
    url = _url(cfg)
    base = json.loads(json.dumps(payload))
    base["stream"] = True
    attempts = []
    p0 = json.loads(json.dumps(base))
    p0.setdefault("stream_options", {"include_usage": True})
    attempts.append(p0)
    p1 = json.loads(json.dumps(p0))
    p1.pop("stream_options", None)
    attempts.append(p1)
    p2 = json.loads(json.dumps(p1))
    p2.pop("thinking", None)
    p2.pop("reasoning_effort", None)
    attempts.append(p2)
    p3 = json.loads(json.dumps(p2))
    p3.pop("temperature", None)
    attempts.append(p3)
    last = {"status": 0, "error": "请求失败"}
    for item in attempts:
        body = json.dumps(item, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
            method="POST")
        try:
            return urllib.request.urlopen(req, timeout=timeout), None
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            last = {"status": int(e.code), "error": "HTTP %d %s" % (e.code, detail)}
            if int(e.code) == 400:
                continue
            return None, last
        except Exception as e:
            return None, {"status": 0, "error": "网络: %s" % e}
    return None, last


# ---------- 解析 ----------

def _trim_line(line, max_chars):
    if not max_chars or len(line) <= max_chars:
        return line
    cut = line[:max_chars]
    m = re.search(r"[。！？!?…\s][^。！？!?…\s]*$", cut)
    if m and m.start() > max_chars * 0.5:
        cut = cut[:m.start() + 1]
    return cut.rstrip("，。；、 ") + "…"


_SENT_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]+[”’\"']?|[^。！？!?…\n]+$")


_QUOTE_PAIRS = ("「」", "『』", "“”", "\"\"", "''")


def clean_text(text):
    """去标签、去包在整句外面的那对引号。

    注意：不能像老代码那样 `strip("「」")` —— 那会把她句中本来就有的收尾引号吃掉：
    「写的「LEON」」被判成两端引号，收尾的 」 就没了，两句还会粘在一起。
    只有**整句从头到尾被一对引号包住**时才脱掉。
    """
    s = re.sub(r"【[^】]{0,20}】", "", str(text or ""))
    s = s.strip()
    if len(s) >= 2:
        for pair in _QUOTE_PAIRS:
            a, b = pair[0], pair[1]
            if s[0] == a and s[-1] == b:
                s = s[1:-1].strip()
                break
    return s.strip()


def split_sentences(text, cfg):
    out = []
    limit = int((cfg.get("behavior") or {}).get("max_line_chars", 60) or 0)
    for line in str(text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in _SENT_RE.findall(line):
            s = clean_text(part).strip("，,。.、：: 　")
            if s:
                out.append(_trim_line(s, limit))
    return out


# 这些词是"提示卡"里的字，不是台词。模型偶尔会照抄（实测把"刚才有人说话"念了出来）。
# 判据只做一件事：整句和它一样，或者只是它前面加了几个字，就丢掉这一句。
_META_WORDS = (
    "气泡", "读字", "过渡", "读他", "欲望", "回访", "审美", "接话", "闲话",
    "刚才那块字", "刚才有人说话", "刚才那句", "这一片细节", "光换了一档",
    "他不动了", "有东西压过来", "刚才走过的地方", "她刚讲的那件事",
)


def _is_echo(line, blocklist):
    from textutil import norm
    n = norm(line)
    if not n:
        return True
    for b in blocklist or ():
        nb = norm(b)
        if not nb:
            continue
        # 只抓"整句就是这张卡"：实测那次就是原样念出来的。
        # 不搞模糊匹配——"刚才那块字我认得"是真台词，不能被误杀。
        # 半截复述（"这一片细节真多"）交给 A3 拦，那边判的是"像不像解说"。
        if n == nb:
            return True
    return False


def parse_output(raw, cfg, blocklist=None):
    """把三行拆成 {say, mood, space, ref, step, account, skip, reason}。"""
    parts = contract.split_output(raw)
    st = contract.parse_state(parts["state"])
    say = parts["say"].strip()
    skip, reason = False, ""
    m = re.match(r"^【\s*不说\s*[｜|]?\s*([^】]*)】$", say)
    if m:
        skip, reason = True, (m.group(1) or "不说").strip()
    if not say:
        skip = True
        reason = reason or "没有台词"
    lines = [] if skip else split_sentences(say, cfg)
    limit = int((cfg.get("behavior") or {}).get("sentences_per_take", 3) or 3)
    lines = lines[:limit]
    if not skip:
        kept = [x for x in lines if not _is_echo(x, list(_META_WORDS) + list(blocklist or []))]
        if len(kept) != len(lines):
            lines = kept
            if not lines:
                skip, reason = True, "只是复述了提示"
    if not skip and not lines:
        skip, reason = True, "拆不出句子"
    mood = st["mood"] if st["mood"] in VALID_MOODS else "平静"
    return {
        "ok": True, "skip": skip, "reason": reason,
        "lines": lines, "line": "" if skip else "".join(
            x if x.endswith(("。", "？", "！", "…")) else x + "。" for x in lines),
        "mood": "沉默" if skip else mood,
        "space": st["space"], "ref": st["ref"], "step": st["step"],
        "heart": bool(st.get("heart")),
        "account": st["account"],
        "pending_add": st["pending_add"], "pending_clear": st["pending_clear"],
        "evidence": parts["evidence"], "tagged": parts["tagged"],
        "raw_say": say,
    }


def validate(parsed, prev_lines, branch="", anchors=None, call="", story=False,
             force=False, preempt=False, story_exit=False, place_close=False):
    """A1 + A3 + A4 + 兜底。返回 {ok, a1, a3, a4, guard, nudge}。

    lang（她自己起话头）那一格跳过 A1/A4：讲一件新的事，本来就不该接上一句，
    结尾也不会正好落在他身上；A3（别当解说）和两条兜底仍然生效。
    故事出口（story_exit）跳过 A1，但保留 A4：回到眼前，落点仍要在他身上。
    看一个地方的最后一格（place_close）额外检查"该走了"，别再约看一会。
    """
    if parsed.get("skip"):
        return {"ok": True, "a1": None, "a3": None, "a4": None, "nudge": ""}
    line = parsed.get("line") or ""
    untagged = not parsed.get("tagged", True)
    if preempt:
        # 抢占 = 换话题。这一格的由头是**新出现的东西**，不是上一句。
        # 还要求"接住上一句"，就会挤出"前半句清旧账、后半句说新事"的缝合句：
        # 「我往哪边走你倒是给个话…那边水挺宽的…」——两半本来没关系。
        a1 = {"ok": True, "how": "换话题", "ref": parsed.get("ref") or ""}
        a4 = {"ok": True, "last": "", "how": "换话题"}
    elif story_exit:
        # 上一句是故事收尾（"所以现在拎东西，我都把袋口攥紧"）。
        # 还要求接住它，模型就会把故事里的袋口/橘子带到眼前；这里只要求回到眼前、落点在他。
        a1 = {"ok": True, "how": "回到眼前", "ref": parsed.get("ref") or ""}
        a4 = contract.ends_on_him(line, call=call)
    elif story or branch == "lang" or force or untagged:
        # 这几类格子不查 A1/A4，只留 A3（别当解说）：
        # - story / lang：她自己在起话头讲一件事，不接上一句，结尾也不会正好落在他身上；
        #   实测不豁免的话，唯一一次真故事（"以前我七岁那年…"）会被 A4 判死。
        # - force：连着好几格没开口了，这格的任务是"必须开口"；再卡 A1/A4，
        #   强制就和判据对撞，强制了还是沉默。
        # - untagged：模型漏了三行标签，不能因为格式把半句好话吞掉，A1 记「未标注」。
        how = "未标注" if untagged else ("强制" if force and not story else "起话头")
        a1 = {"ok": True, "how": how, "ref": parsed.get("ref") or ""}
        a4 = {"ok": True, "last": ""}
    else:
        a1 = contract.check_reference(line, parsed.get("ref"), prev_lines)
        a4 = contract.ends_on_him(line, call=call)
    a3 = contract.landing_test(line, call=call)
    guards = contract.guard_checks(line)
    reading = contract.reading_check(line, branch)
    story_like = bool(story or branch == "lang")
    small = contract.small_thing_check(line, story=story_like)
    stock = contract.stock_check(line, story=story_like)
    entry = contract.entry_check(line, story=story_like)
    count = contract.count_check(line, story=story_like)
    stop = contract.stop_check(line, branch=branch, story=story_like)
    step = contract.step_check(line, story=story_like)
    place = (contract.place_close_check(line) if place_close
             else {"ok": True, "how": "不适用"})
    nudge = []
    if not a1["ok"]:
        nudge.append("没接住你上一句（引用=%s）：这一句要么带上上一句里的一个词，"
                     "要么对那句话做一个动作，【态】的引用写清楚。" % (parsed.get("ref") or "空"))
    if not a3["ok"]:
        nudge.append("这句删掉景物的词之后看不出你在对他说话：加点你自己或他的东西。")
    if not a4["ok"]:
        nudge.append("最后一句停在了画面上（%s）。落点要在他身上：问他一句、要他一句、"
                     "或者怪他一句都行。" % a4["last"][:14])
    if not guards["ok"]:
        nudge.extend(guards["problems"])
    if not reading["ok"]:
        nudge.append(reading["why"])
    if not small["ok"]:
        nudge.append(small["why"])
    if not stock["ok"]:
        nudge.append(stock["why"])
    if not entry["ok"]:
        nudge.append(entry["why"])
    if not count["ok"]:
        nudge.append(count["why"])
    if not stop["ok"]:
        nudge.append(stop["why"])
    if not step["ok"]:
        nudge.append(step["why"])
    if not place["ok"]:
        nudge.append(place["why"])
    return {"ok": a1["ok"] and a3["ok"] and a4["ok"] and guards["ok"]
                   and reading["ok"] and small["ok"] and stock["ok"]
                   and entry["ok"] and count["ok"] and stop["ok"]
                   and step["ok"] and place["ok"],
            "a1": a1, "a3": a3, "a4": a4, "guard": guards, "reading": reading,
            "small": small, "stock": stock, "entry": entry, "count": count,
            "stop": stop, "step": step, "place": place,
            "nudge": "；".join(nudge)}


# ---------- 对外 ----------

def complete_text(prompt, cfg, api_key, max_tokens=800, timeout=None):
    """纯文本调用，用来压缩长期记忆。"""
    if not api_key:
        return {"ok": False, "error": "没有 API Key"}
    if not (prompt or "").strip():
        return {"ok": False, "error": "提示词是空的"}
    v = cfg.get("vlm") or {}
    payload = {
        "model": v.get("model") or "deepseek-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35, "max_tokens": int(max_tokens), "stream": False,
    }
    payload.update(_thinking_fields(cfg))
    res = _request_with_fallback(cfg, api_key, payload,
                                 float(timeout or v.get("timeout", 25)))
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "请求失败",
                "latency": res.get("latency", 0)}
    try:
        obj = json.loads(res.get("raw") or "")
        text = _content_of(obj)
        usage = obj.get("usage") or {}
    except Exception as e:
        return {"ok": False, "error": "响应解析失败: %s" % e}
    return {"ok": bool((text or "").strip()), "text": (text or "").strip(),
            "usage": usage, "latency": res.get("latency", 0)}


def probe(cfg, api_key, image_bytes=None, prompt="只回复两个字：可以",
          max_tokens=24, timeout=None):
    """连通性探针。给图就是视觉探针（用来确认这个模型真的能看见）。

    返回 {ok, text, latency, error, model, url}。不抛异常，界面直接显示结果。
    """
    if not api_key:
        return {"ok": False, "error": "没有 key", "text": "", "latency": 0}
    v = cfg.get("vlm") or {}
    model = v.get("model") or ""
    url = _url(cfg)
    content = prompt
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + b64, "detail": "low"}}]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        # 探针至少给 128 个 token：思考开着时，16 个 token 会全被 reasoning 吃掉，
        # 正文为空，界面就误报“接口不通”。
        "temperature": 0, "max_tokens": max(128, int(max_tokens)), "stream": False,
    }
    payload.update(_thinking_off(cfg))
    res = _request_with_fallback(cfg, api_key, payload,
                                 float(timeout or min(30, v.get("timeout", 30))))
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "请求失败",
                "text": "", "latency": res.get("latency", 0),
                "model": model, "url": url, "status": res.get("status")}
    try:
        obj = json.loads(res.get("raw") or "")
        text = (_content_of(obj) or "").strip()
    except Exception as e:
        return {"ok": False, "error": "响应解析失败: %s" % e, "text": "",
                "latency": res.get("latency", 0), "model": model, "url": url}
    return {"ok": bool(text), "text": text[:80], "latency": res.get("latency", 0),
            "error": "" if text else "模型返回了空内容", "model": model, "url": url}


def speak(cfg, api_key, build_system, history, note, img_bytes,
          prev_lines=None, image_size=None, on_delta=None,
          force_speak=False, demo_mem="", branch="", anchors=None,
          with_image=True, story=False, preempt=False, story_exit=False,
          place_close=False):
    """一次开口：调用 → 解析 → 过 A1/A3 → 不过就带理由重说一次。

    build_system(retry_note) 由调用方给（它知道线程/记账/信号），重说时把理由塞进去。
    """
    v = cfg.get("vlm") or {}
    detail = v.get("detail") or "high"
    frames = img_bytes if isinstance(img_bytes, (list, tuple)) else [img_bytes]
    # 故事格不附图：帧数按 0 算，否则日志和实测面板会显示"发了 1 帧"，
    # 看到一个根本没发出去的图片 token 数。
    frame_count = max(1, len([f for f in frames if f])) if with_image else 0
    img_tokens = prompts.estimate_image_tokens(
        image_size[0], image_size[1], detail) * frame_count if (image_size and frame_count) else 0

    if not api_key:
        demo = demo_reply((frames or [None])[0], cfg, demo_mem)
        parsed = parse_output(_demo_raw(demo), cfg)
        parsed.update({"demo": True, "latency": 0.2, "usage": {},
                       "prompt_text": "", "checks": {"ok": True},
                       "prompt_tokens_est": 0, "image_tokens_est": img_tokens,
                       "retried": False, "frame_count": frame_count})
        if on_delta and parsed.get("line"):
            on_delta(parsed["line"])
        return parsed

    timeout = float(v.get("timeout", 30))
    attempts = []
    nudge = ""
    result = None
    force_no_think = False
    for attempt in (0, 1):
        system_prompt = build_system(nudge)
        if not system_prompt:
            return {"ok": False, "skip": True, "error": "主提示词是空的（prompts/00_主提示词.txt）"}
        messages = build_messages(system_prompt, history, frames, note, detail,
                                  with_image=with_image)
        est_tokens = estimate_prompt_tokens(
            system_prompt, history, note, image_size, detail, image_count=frame_count)
        payload = {
            "model": v.get("model") or "deepseek-flash",
            "messages": messages,
            "temperature": float(v.get("temperature", 0.6)),
            "max_tokens": int(v.get("max_tokens", 500)),
            "stream": True,
        }
        payload.update(_thinking_off(cfg) if force_no_think else _thinking_fields(cfg))
        t0 = time.time()
        raw, usage = "", {}
        err = ""
        resp, oerr = _open_stream(cfg, api_key, payload, timeout)
        if resp is None:
            err = (oerr or {}).get("error") or "请求失败"
        else:
            try:
                for line in resp:
                    text = line.decode("utf-8", "replace").strip()
                    if not text or text.startswith(":") or not text.startswith("data:"):
                        continue
                    data = text[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    try:
                        delta = obj["choices"][0].get("delta") or {}
                        content = delta.get("content")
                    except Exception:
                        content = None
                    if isinstance(content, list):
                        content = "".join(str(x.get("text") or "") if isinstance(x, dict)
                                          else str(x) for x in content)
                    if content:
                        raw += content
            except Exception as e:
                err = str(e)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
        latency = round(time.time() - t0, 2)

        if not raw.strip():
            if attempt == 0:
                # 思考模型常见：reasoning 把 max_tokens 吃光，正文为空。
                # 不要直接沉默，关掉思考再试一次。
                force_no_think = True
                nudge = ("上一次没有返回正文（可能思考占满了输出）。"
                         "这次不要思考，直接按格式输出正文。")
                continue
            result = {"ok": False, "skip": True, "error": err or "模型没有返回内容",
                      "latency": latency, "usage": usage or {},
                      "prompt_tokens_est": est_tokens,
                      "image_tokens_est": img_tokens, "checks": {"ok": False},
                      "retried": bool(attempt), "frame_count": frame_count}
            break

        parsed = parse_output(raw, cfg, blocklist=anchors)
        checks = validate(parsed, prev_lines, branch=branch, anchors=anchors,
                          call=((cfg.get("persona") or {}).get("call_player") or ""),
                          story=story, force=force_speak, preempt=preempt,
                          story_exit=story_exit, place_close=place_close)
        parsed.update({
            "latency": latency, "usage": usage, "raw": raw,
            "prompt_text": system_prompt, "checks": checks,
            "prompt_tokens_est": est_tokens,
            "image_tokens_est": img_tokens, "retried": bool(attempt),
            "frame_count": frame_count,
        })
        if not parsed.get("tagged", True):
            # 少写【说】【态】【据】：提醒一次格式；但别因为格式把它整句丢掉。
            # 原来这里把 nudge 整个覆盖成"格式"——如果这一句同时要过童年/要反应，
            # 第二次重说就只看到格式提示，等于另一条兜底白设。现在两条一起给。
            checks["untagged"] = True
            checks["ok"] = False
            extra = (checks.get("nudge") or "").strip()
            checks["nudge"] = ("刚才没按格式写：缺【说】【态】【据】三行。"
                               "重写一次，标签照抄，第二行把引用和心动都填上。"
                               + ("；另外：" + extra if extra else ""))
        result = parsed
        if parsed.get("skip") or checks["ok"]:
            break
        attempts.append(checks["nudge"])
        nudge = checks["nudge"]
        if attempt == 1 and checks.get("untagged") and checks["guard"]["ok"] \
                and (checks.get("reading") or {}).get("ok", True) \
                and (checks.get("small") or {}).get("ok", True) \
                and (checks.get("stock") or {}).get("ok", True) \
                and (checks.get("entry") or {}).get("ok", True) \
                and (checks.get("count") or {}).get("ok", True) \
                and (checks.get("stop") or {}).get("ok", True) \
                and (checks.get("step") or {}).get("ok", True) \
                and (checks.get("place") or {}).get("ok", True):
            # 两次都没给标签：这一次放它过去（台词本身可能是好的），记一笔"格式没给"。
            # ok 要跟着置真，否则日志会写成"未过判据"，看起来像这句话被拦了。
            checks["ok"] = True
            checks["untagged_soft"] = True
            parsed["checks"] = checks
            result = parsed
            break
        if attempt == 1 and checks["a3"]["ok"] and checks["a4"]["ok"] \
                and checks["guard"]["ok"] \
                and (checks.get("entry") or {}).get("ok", True) \
                and (checks.get("stop") or {}).get("ok", True) \
                and (checks.get("step") or {}).get("ok", True) \
                and (checks.get("place") or {}).get("ok", True):
            # A3/A4/硬兜底都过，只剩软判据没过：收下这句，别整格丢掉。
            # 软判据 = A1（接上一句）+ 读字落到字上 + 小东西别叫名字 + 别说套话。
            # 「进去」「停下」「踩到」「最后一格还在看」不是软判据：
            # 它们假设了空间/停住/脚感事实，或违背了这一格的收尾任务；两次都不改就这一格不说。
            # 实测代价：她会说"你带我过去看看，就看看"这种好句子，而上一句是
            # "我脚有点酸"——两句本来就没词可接。宁可少一点连续感，也不要整格沉默。
            checks["ok"] = True
            if not checks["a1"]["ok"]:
                checks["a1_soft"] = True
            if not (checks.get("reading") or {}).get("ok", True):
                checks["reading_soft"] = True
            if not (checks.get("small") or {}).get("ok", True):
                checks["small_soft"] = True
            if not (checks.get("stock") or {}).get("ok", True):
                checks["stock_soft"] = True
            if not (checks.get("count") or {}).get("ok", True):
                checks["count_soft"] = True
            if checks.get("untagged"):
                checks["untagged_soft"] = True
            parsed["checks"] = checks
            result = parsed
            break
        if attempt == 1:
            # 两次都不合格：这一格不说，但把**是哪条判据**留下，别再笼统写一句
            why = []
            if not checks["a1"]["ok"]:
                why.append("没接住上一句")
            if not checks["a3"]["ok"]:
                why.append("像在解说")
            if not checks["a4"]["ok"]:
                why.append("落点在画面上")
            g = checks.get("guard") or {}
            if g.get("childhood"):
                why.append("提了童年")
            if g.get("reaction"):
                why.append("向他要反应")
            if not (checks.get("reading") or {}).get("ok", True):
                why.append("没读字")
            if not (checks.get("small") or {}).get("ok", True):
                why.append("叫了小东西的名字")
            if not (checks.get("stock") or {}).get("ok", True):
                why.append("用了套话")
            if not (checks.get("count") or {}).get("ok", True):
                why.append("数了数量")
            if not (checks.get("entry") or {}).get("ok", True):
                why.append("说了进去")
            if not (checks.get("stop") or {}).get("ok", True):
                why.append("说了停下")
            if not (checks.get("step") or {}).get("ok", True):
                why.append("靠脚感编了东西")
            if not (checks.get("place") or {}).get("ok", True):
                why.append("最后一格还在看")
            parsed.update({"skip": True, "lines": [], "line": "", "mood": "沉默",
                           "reason": "两次都不过：" + ("/".join(why) or "判据")})
            result = parsed

    if result is None:
        result = {"ok": False, "skip": True, "error": "请求失败",
                  "checks": {"ok": False}}
    if on_delta and result.get("ok") and not result.get("skip") and result.get("line"):
        on_delta(result["line"])
    return result


def ask(img_bytes, cfg, api_key, system_memory="", history=None, force_speak=False,
        prompt_map=None, retry_note="", shape=None, image_size=None,
        recent_lines=None, current_frame_note=None, decision=None, sig=None,
        router=None, branch="", anchors=None, story=False, preempt=False,
        story_exit=False, place_close=False, **legacy):
    """非流式入口（设置面板实测、自检用）。"""
    pm = prompt_map or prompts.load_all()
    builder = lambda nudge: build_prompt(  # noqa: E731
        cfg, system_memory=system_memory, force_speak=force_speak, prompt_map=pm,
        retry_note=(nudge or retry_note), shape=shape, recent_lines=recent_lines,
        decision=decision, sig=sig, router=router, story=story, story_exit=story_exit)
    note = current_note_for(cfg, pm, recent_lines, current_frame_note,
                            story=story, story_exit=story_exit)
    return speak(cfg, api_key, builder, history or [], note, img_bytes,
                 prev_lines=recent_lines, image_size=image_size,
                 branch=branch, anchors=anchors,
                 with_image=not story, story=story, preempt=preempt,
                 story_exit=story_exit, place_close=place_close)


# ---------- 演示 ----------

DEMO_POOL = [
    ("平静", "我脚有点酸。"),
    ("平静", "你走慢一点。"),
    ("害羞", "别看我。"),
    ("开心", "今天光真好。"),
    ("平静", "我不太想说话了。"),
    ("难过", "以前也走过这么一条街。"),
]


def _demo_raw(demo):
    return "【说】%s\n【态】情绪=%s｜空间=室外｜引用=追问｜步=犹｜记账=无\n【据】演示" % (
        demo.get("line") or "", demo.get("mood") or "平静")


def demo_reply(img_bytes, cfg, memory_block=""):
    """没有 key 时的本地替身，只为了让链路能跑。"""
    mood = "平静"
    try:
        import io
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB") if img_bytes else None
        if im is not None:
            im.thumbnail((120, 120))
            lum = ImageStat.Stat(im).mean
            mood = "开心" if sum(lum) / 3 > 120 else ("难过" if sum(lum) / 3 < 50 else "平静")
    except Exception:
        pass
    used = re.findall(r"「([^」]+)」", memory_block or "")
    pool = list(DEMO_POOL)
    random.shuffle(pool)
    for m, line in pool:
        if line not in used:
            return {"scene": "", "mood": m, "line": line}
    m, line = pool[0]
    return {"scene": "", "mood": m, "line": line}
