# -*- coding: utf-8 -*-
"""Key 的本机加密存储（Windows DPAPI）。

为什么不直接写进 config.json：
- 那是明文。配置会被备份、会被 xcopy、会被打包带走，key 跟着就出去了；
- 也不该只认环境变量——大多数人根本不会去设。

所以：key 用 Windows 的 DPAPI 加一次密，存到 `keys.dat`。
加密是**绑当前 Windows 账户**的：把文件拷到别的机器、别的用户名下，解不开。
换句话说，能读它的人 = 能登进这台电脑的人，这已经是本机能做到的上限。

没装 pywin32 也没关系：DPAPI 就在 crypt32.dll 里，ctypes 直接调。
非 Windows 或调用失败时，protect() 返回 None，调用方应该退回"只读环境变量"。
"""

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))

# 和 config.py 用同一个数据目录（含低权限目录的退回逻辑）。
try:
    from config import DATA as _DATA
    DATA = str(_DATA)
except Exception:
    DATA = BASE
STORE_PATH = os.path.join(DATA, "keys.dat")

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        try:
            ctypes.windll.kernel32.LocalFree(blob.pbData)
        except Exception:
            pass


def available():
    return sys.platform.startswith("win")


def protect(text):
    """明文 → base64(DPAPI 密文)。失败返回 None。"""
    if not available() or not text:
        return None
    try:
        blob_in, _keep = _to_blob(str(text).encode("utf-8"))
        blob_out = _Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), "herworld-key", None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            return None
        return base64.b64encode(_from_blob(blob_out)).decode("ascii")
    except Exception:
        return None


def unprotect(b64):
    """base64(DPAPI 密文) → 明文。失败返回 ""。"""
    if not available() or not b64:
        return ""
    try:
        raw = base64.b64decode(b64)
        blob_in, _keep = _to_blob(raw)
        blob_out = _Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            return ""
        return _from_blob(blob_out).decode("utf-8", "replace")
    except Exception:
        return ""


def _read():
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d):
    try:
        tmp = STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STORE_PATH)
        # 尽量只让当前用户读写（Windows 上 chmod 不完全生效，做个姿态）
        try:
            os.chmod(STORE_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def get(provider):
    d = _read()
    return unprotect(d.get(str(provider or "")) or "")


def put(provider, key):
    """返回 (ok, 说明)。"""
    provider = str(provider or "").strip()
    key = str(key or "").strip()
    if not provider:
        return False, "没有选接入方"
    if not key:
        return False, "key 是空的"
    enc = protect(key)
    if not enc:
        return False, "这台系统没法加密保存（%s）。请改用环境变量。" % (
            "非 Windows" if not available() else "DPAPI 调用失败")
    d = _read()
    d[provider] = enc
    if not _write(d):
        return False, "写不进 keys.dat"
    return True, "已加密保存到本机（只有当前 Windows 账户能解）"


def clear(provider=None):
    d = _read()
    if provider is None:
        d = {}
    else:
        d.pop(str(provider), None)
    return _write(d)


def which(provider):
    """这个接入方在本机存过 key 吗（不解密，只判断有没有）。"""
    d = _read()
    return bool(d.get(str(provider or "")))
