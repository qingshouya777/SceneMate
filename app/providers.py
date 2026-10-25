# -*- coding: utf-8 -*-
"""接哪家：OpenAI 兼容的各家预设。

原则：
- **DeepSeek 放第一，且是默认**（这套东西是按它调的）；
- 但接口一律走 OpenAI 兼容格式（`{base}/chat/completions`），所以别家只要兼容就能接；
- 每家只需要三样东西：base_url、key、模型名。模型名可以手填，也可以在线拉。
- 这个软件是**看图说话**的，必须选带视觉的模型。不支持的模型不会报错，只会看不见画面
  ——所以每家都标了 `vision`，界面上也会提示。
"""

import json
import urllib.request

DEFAULT_ID = "deepseek"

PROVIDERS = [
    {
        "id": "deepseek",
        "name": "DeepSeek（默认）",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "docs": "https://platform.deepseek.com/api_keys",
        "note": "默认这条路。地址不用带 /v1，程序会自己补 /chat/completions。",
        "models": [
            {"id": "deepseek-flash", "vision": True, "note": "默认，快，够用"},
            {"id": "deepseek-chat", "vision": True, "note": "稳一点，慢一点"},
            {"id": "deepseek-reasoner", "vision": False, "note": "会想很久，且不看图"},
        ],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "docs": "https://platform.openai.com/api-keys",
        "note": "国内需要自己解决网络。",
        "models": [
            {"id": "gpt-4o", "vision": True, "note": "看图稳"},
            {"id": "gpt-4o-mini", "vision": True, "note": "便宜"},
            {"id": "gpt-4.1", "vision": True, "note": ""},
            {"id": "gpt-4.1-mini", "vision": True, "note": ""},
        ],
    },
    {
        "id": "dashscope",
        "name": "通义千问（阿里云）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
        "docs": "https://bailian.console.aliyun.com/",
        "note": "百炼平台，必须选 qwen-vl 系列的视觉模型。",
        "models": [
            {"id": "qwen-vl-max", "vision": True, "note": "看图好"},
            {"id": "qwen-vl-plus", "vision": True, "note": "便宜"},
            {"id": "qwen3-vl-plus", "vision": True, "note": ""},
        ],
    },
    {
        "id": "moonshot",
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "key_env": "MOONSHOT_API_KEY",
        "docs": "https://platform.moonshot.cn/console/api-keys",
        "note": "要选带 vision 的模型名。",
        "models": [
            {"id": "moonshot-v1-8k-vision-preview", "vision": True, "note": "看图"},
            {"id": "kimi-latest", "vision": True, "note": ""},
            {"id": "moonshot-v1-32k", "vision": False, "note": "不看图"},
        ],
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key_env": "ZHIPUAI_API_KEY",
        "docs": "https://bigmodel.cn/usercenter/apikeys",
        "note": "地址要带 /api/paas/v4。",
        "models": [
            {"id": "glm-4v-plus", "vision": True, "note": "看图"},
            {"id": "glm-4v", "vision": True, "note": ""},
            {"id": "glm-4.5v", "vision": True, "note": ""},
        ],
    },
    {
        "id": "siliconflow",
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "key_env": "SILICONFLOW_API_KEY",
        "docs": "https://cloud.siliconflow.cn/account/ak",
        "note": "一个 key 很多开源模型，模型名要写全（带斜杠）。",
        "models": [
            {"id": "Qwen/Qwen2.5-VL-72B-Instruct", "vision": True, "note": ""},
            {"id": "Qwen/Qwen2.5-VL-32B-Instruct", "vision": True, "note": ""},
        ],
    },
    {
        "id": "openrouter",
        "name": "OpenRouter（聚合）",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "docs": "https://openrouter.ai/keys",
        "note": "一个 key 走很多家，模型名带厂商前缀。",
        "models": [
            {"id": "google/gemini-2.5-flash", "vision": True, "note": ""},
            {"id": "anthropic/claude-sonnet-4", "vision": True, "note": ""},
            {"id": "qwen/qwen2.5-vl-72b-instruct", "vision": True, "note": ""},
        ],
    },
    {
        "id": "custom",
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "key_env": "CUSTOM_API_KEY",
        "docs": "",
        "note": "任何 OpenAI 兼容的地址都行：本地 ollama / LM Studio 也可以（地址填 http://127.0.0.1:11434/v1）。",
        "models": [],
    },
]

_BY_ID = {p["id"]: p for p in PROVIDERS}


def get(pid):
    return _BY_ID.get(str(pid or "")) or _BY_ID[DEFAULT_ID]


def names():
    return [(p["id"], p["name"]) for p in PROVIDERS]


def default_model(pid):
    p = get(pid)
    for m in p.get("models") or []:
        if m.get("vision"):
            return m["id"]
    return ((p.get("models") or [{}])[0] or {}).get("id", "")


def model_vision(pid, model):
    p = get(pid)
    for m in p.get("models") or []:
        if m.get("id") == model:
            return bool(m.get("vision")), m.get("note") or ""
    return None, ""


def guess_from_base_url(base_url):
    """老配置里只有 base_url，反推是哪家（迁移用）。"""
    u = str(base_url or "").lower()
    for p in PROVIDERS:
        b = (p.get("base_url") or "").lower()
        if b and (u.startswith(b.rstrip("/")) or b.rstrip("/").endswith(u.rstrip("/"))):
            return p["id"]
    if "deepseek" in u:
        return "deepseek"
    if "openai" in u:
        return "openai"
    if "dashscope" in u:
        return "dashscope"
    if "moonshot" in u:
        return "moonshot"
    if "bigmodel" in u or "zhipu" in u:
        return "zhipu"
    if "siliconflow" in u:
        return "siliconflow"
    if "openrouter" in u:
        return "openrouter"
    return "custom"


def completions_url(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def models_url(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base + "/models"


def fetch_models(base_url, key, timeout=10):
    """拉模型列表。失败返回 ([], 原因)。"""
    url = models_url(base_url)
    if not url:
        return [], "地址是空的"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + (key or "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return [], "拉不到：%s" % e
    rows = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        return [], "返回格式不认识"
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("id"):
            out.append(str(r["id"]))
    out.sort()
    return out, ""
