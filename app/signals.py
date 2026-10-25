# -*- coding: utf-8 -*-
"""视觉信号层（零 VLM、纯 PIL）。

给调度层提供三种东西：
1. **画面里的字**（B1）：8×6 分块各算「高对比笔画占比」，命中的块再 4×4 细化定位。
   原来整帧降采样到 160×100 再算，小字被平均掉，8/11/15 三帧全漏；分块后同一套
   物理量（极亮极暗像素占比）就能过线。
2. **NPC 气泡**（B2）：黑底白字、宽扁、带白字的暗块 = 有人冲你说话。它是画面里
   唯一「本来就是冲人说的话」，比店招好用，所以单独成一类事件，优先级最高。
3. **三环 + 流**（B3/B4）：三环不再只看中心边缘（会把「大片台阶」误判成贴身），
   改成「主体面积 / 底边位置 / 局部清晰度」交叉，并给出置信度；flow 用标定过的
   地板值（tools/calibrate_flow.py 原地不动录 10 帧量出来），没标定就不许说「他停了」。

只依赖 Pillow，不用 numpy —— 用户的 runtime 目录里没有 numpy。
"""

import json
import os
import time

from PIL import Image, ImageFilter, ImageStat

Image.MAX_IMAGE_PIXELS = None

BASE = os.path.dirname(os.path.abspath(__file__))
try:
    from config import DATA as _DATA
    DATA = str(_DATA)
except Exception:
    DATA = BASE
FLOW_PATH = os.path.join(DATA, "flow_baseline.json")

# ---------- 常量（都是物理量，不随素材漂移） ----------

GRID_COLS = 8
GRID_ROWS = 6
SUB_COLS = 4
SUB_ROWS = 4

HI_CUT = 232          # 「极亮」像素：招牌/霓虹的白字
LO_CUT = 28           # 「极暗」像素：黑底、气泡底色
TEXT_THR = 1.0        # 高对比笔画占比门槛（百分比）。三帧验收：8=3.78 9=17.5 11=7.69 15=22.2
COARSE_GATE = 0.35    # 粗格子过了这个分才值得细化（省时间）

BUBBLE_DARK = 58      # 气泡底色的灰度上限
BUBBLE_TEXT = 165     # 气泡里白字的灰度下限


def signature(img, size=(32, 20)):
    g = img.convert("L").resize(size, Image.BILINEAR)
    return list(g.getdata())


def sig_diff(a, b):
    if not a or not b:
        return 999.0
    n = min(len(a), len(b))
    if n <= 0:
        return 999.0
    return sum(abs(int(a[i]) - int(b[i])) for i in range(n)) / float(n)


# ---------- flow 地板标定 ----------

def read_flow_baseline():
    """返回 {"floor": float, "n": int, "ts": str} 或 None（没标定过）。"""
    try:
        with open(FLOW_PATH, encoding="utf-8") as f:
            d = json.load(f)
        floor = float(d.get("floor") or 0)
        if floor <= 0:
            return None
        d["floor"] = floor
        return d
    except Exception:
        return None


def write_flow_baseline(samples):
    """标定结果落盘。samples 是一串静止时的 flow 值。"""
    vals = sorted(float(x) for x in samples if x is not None)
    if not vals:
        return None
    mid = vals[len(vals) // 2]
    floor = round(max(1.2, mid * 1.6), 2)
    d = {"floor": floor, "n": len(vals), "median": round(mid, 2),
         "max": round(vals[-1], 2), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        with open(FLOW_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return d


# ---------- 基础统计 ----------

def _band(g, box):
    w, h = g.size
    crop = g.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    ed = ImageStat.Stat(crop.filter(ImageFilter.FIND_EDGES))
    return round(ed.mean[0], 2), round(ImageStat.Stat(crop).mean[0], 1), round(ed.stddev[0], 2)


def _stroke_ratio(crop):
    """高对比笔画占比：极亮与极暗像素同时密集 = 字。返回百分比。"""
    h = crop.histogram()
    n = float(sum(h)) or 1.0
    hi = sum(h[HI_CUT + 1:]) / n
    lo = sum(h[:LO_CUT]) / n
    return min(hi, lo) * 200.0


def text_proxy(img):
    """B1：8×6 分块 + 命中块 4×4 细化。返回命中信息（无命中 hit=False）。"""
    g = img.convert("L")
    W, H = g.size
    cells = []
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            box = (int(c * W / GRID_COLS), int(r * H / GRID_ROWS),
                   int((c + 1) * W / GRID_COLS), int((r + 1) * H / GRID_ROWS))
            crop = g.crop(box)
            cells.append((round(_stroke_ratio(crop), 2), c, r, box, crop))

    hits = []
    for coarse, c, r, box, crop in cells:
        if coarse < COARSE_GATE:
            continue
        bw, bh = crop.size
        for rr in range(SUB_ROWS):
            for cc in range(SUB_COLS):
                sb = crop.crop((int(cc * bw / SUB_COLS), int(rr * bh / SUB_ROWS),
                                int((cc + 1) * bw / SUB_COLS), int((rr + 1) * bh / SUB_ROWS)))
                sc = _stroke_ratio(sb)
                if sc >= TEXT_THR:
                    x0 = box[0] + int(cc * bw / SUB_COLS)
                    y0 = box[1] + int(rr * bh / SUB_ROWS)
                    hits.append({
                        "score": round(sc, 2),
                        "rect": [round(x0 / float(W), 3), round(y0 / float(H), 3),
                                 round(bw / float(SUB_COLS) / W, 3),
                                 round(bh / float(SUB_ROWS) / H, 3)],
                        "cell": [c, r], "sub": [cc, rr],
                    })
    hits.sort(key=lambda d: -d["score"])
    best = hits[0] if hits else None
    return {
        "hit": bool(best),
        "score": best["score"] if best else 0.0,
        "rect": best["rect"] if best else None,
        "all": hits[:4],
    }


def find_bubble(img, text_hits=None):
    """B2：黑底白字的对话气泡。

    气泡在画面里很小（约 6% 宽、4% 高），连通块一压缩就和背后的暗部粘成一片，
    所以不按「整块暗」找，而是复用文字那一路：气泡里的白字会让**相邻两三个
    细格**同时高分（实测 9.png：0.562 与 0.593 两块，分数 17.3/10.6，同一行
    紧挨着）。把这些细格按行合并，再验「框内很暗 + 白字密 + 横扁」。
    招牌是整片立面、不会出现「两格紧挨且都很小」这种形状。
    """
    hits = (text_hits if text_hits is not None else text_proxy(img)).get("all") or []
    if len(hits) < 1:
        return {"hit": False, "score": 0.0, "rect": None}
    g = img.convert("L")
    W, H = g.size

    groups = []
    for h in hits:
        x, y, w, hh = h["rect"]
        placed = False
        for grp in groups:
            gx, gy, gw, gh = grp["rect"]
            same_row = abs(y - gy) <= hh * 0.6
            near = (x <= gx + gw + w * 0.6) and (gx <= x + w + w * 0.6)
            if same_row and near:
                grp["rect"] = [min(gx, x), min(gy, y),
                               max(gx + gw, x + w) - min(gx, x),
                               max(gy + gh, y + hh) - min(gy, y)]
                grp["scores"].append(h["score"])
                placed = True
                break
        if not placed:
            groups.append({"rect": [x, y, w, hh], "scores": [h["score"]]})

    best = None
    for grp in groups:
        x, y, w, hh = grp["rect"]
        bw, bh = int(w * W), int(hh * H)
        # 气泡很小：太宽太高的都不是气泡，是招牌/暗部
        if bw < 0.045 * W or bw > 0.26 * W:
            continue
        if bh < 0.015 * H or bh > 0.060 * H:
            continue
        aspect = bw / float(max(1, bh))
        if aspect < 2.0:
            continue
        # 框内实测：暗底 + 白字
        crop = g.crop((max(0, int(x * W)), max(0, int(y * H)),
                       min(W, int((x + w) * W)), min(H, int((y + hh) * H))))
        hist = crop.histogram()
        n = float(sum(hist)) or 1.0
        dark = sum(hist[:BUBBLE_DARK + 12]) / n
        bright = sum(hist[BUBBLE_TEXT + 1:]) / n
        mean = sum(i * hist[i] for i in range(256)) / n
        if mean > 118 or dark < 0.30 or not (0.03 <= bright <= 0.40):
            continue
        if len(grp["scores"]) < 2 and max(grp["scores"]) < 8:
            continue
        score = (sum(grp["scores"]) / 10.0) * min(1.0, aspect / 3.0) * min(1.0, dark / 0.5)
        if best is None or score > best["score"]:
            best = {"hit": True, "score": round(score, 3),
                    "rect": [round(x, 3), round(y, 3), round(w, 3), round(hh, 3)],
                    "dark": round(dark, 2), "bright": round(bright, 3),
                    "mean": round(mean, 1), "cells": len(grp["scores"])}
    if best is None:
        return {"hit": False, "score": 0.0, "rect": None}
    return best


def ring_estimate(g, ctr_edge, ctr_std):
    """B3：三环 = 主体面积 × 底边位置 × 局部清晰度，三项交叉后给置信度。

    原来只用中心边缘分位数，结果「画面里一大片台阶/栏杆」被判成贴身——台阶是一堆
    平行线，边缘均值高但方差低、而且铺满下半屏，看着像很近，其实没有主体。
    现在：中心边缘单独一项不再能定档，必须先有「主体面积」和「局部清晰度」对得上；
    三项都弱就明说没把握（conf 低），而不是硬给一个「远处」。
    """
    w, h = g.size
    # 主体面积：中心带切 4×3 格，逐格算边缘方差；再和全图 75 分位比
    cells = []
    for r in range(3):
        for c in range(4):
            box = (int((0.28 + c * 0.11) * w), int((0.30 + r * 0.16) * h),
                   int((0.28 + (c + 1) * 0.11) * w), int((0.30 + (r + 1) * 0.16) * h))
            crop = g.crop(box)
            cells.append(round(ImageStat.Stat(crop.filter(ImageFilter.FIND_EDGES)).stddev[0], 2))
    whole = g.resize((240, 135), Image.BILINEAR)
    whole_std = round(ImageStat.Stat(whole.filter(ImageFilter.FIND_EDGES)).stddev[0], 2) or 1.0
    subject_area = sum(1 for v in cells if v > whole_std * 1.15) / float(len(cells))

    # 底边位置：最下面 18% 的边缘强度 / 中心带边缘强度（贴身时脚边很乱）
    bot_ed, _bot_lum, _ = _band(g, (0.0, 0.82, 1.0, 1.0))
    bottom_pos = bot_ed / max(1.0, ctr_edge)

    # 局部清晰度：中心带边缘方差 / 全图边缘方差
    sharp = ctr_std / whole_std

    def clamp01(v):
        return max(0.0, min(1.0, v))

    near = (0.42 * clamp01((ctr_edge - 20.0) / 18.0)
            + 0.30 * clamp01((sharp - 1.0) / 0.8)
            + 0.28 * clamp01((bottom_pos - 0.45) / 0.5))
    # 没有主体面积，就不许判贴身（这是修「台阶误判」的那一刀）
    if subject_area < 0.25:
        near = min(near, 0.44)

    if near >= 0.72:
        label = "贴身"
    elif near >= 0.45:
        label = "眼前"
    else:
        label = "远处"
    margin = min(abs(near - 0.45), abs(near - 0.72))
    conf = round(min(1.0, 0.30 + margin * 2.0 + subject_area * 0.2), 2)
    return {
        "label": label, "conf": conf,
        "parts": {"subject_area": round(subject_area, 2), "ctr_edge": round(ctr_edge, 2),
                  "sharp": round(sharp, 2), "bottom": round(bottom_pos, 2)},
        "near": round(near, 2),
    }


def analyze(img, prev_sig=None, floor=None, cur_sig=None):
    """一帧 → 一套信号。prev_sig 给 flow；floor 给「他停了」的绝对地板。

    cur_sig 可由调用方先算好传进来（截帧闸门也要用同一份签名）。
    """
    im = img.convert("RGB")
    g = im.convert("L")
    small = g.copy()
    if max(small.size) > 720:
        small.thumbnail((720, 720), Image.BILINEAR)

    mean = round(ImageStat.Stat(small).mean[0], 1)
    edge = round(ImageStat.Stat(small.filter(ImageFilter.FIND_EDGES)).mean[0], 2)
    top_ed, top_lum, _ = _band(g, (0, 0, 1, 0.22))
    ctr_edge, ctr_lum, ctr_std = _band(g, (0.28, 0.30, 0.72, 0.78))
    bot_ed, bot_lum, _ = _band(g, (0, 0.75, 1, 1))
    bot_std = round(ImageStat.Stat(
        g.crop((0, int(0.75 * g.size[1]), g.size[0], g.size[1]))).stddev[0], 1)

    sig = cur_sig or signature(im)
    flow = None if prev_sig is None else round(sig_diff(sig, prev_sig), 2)
    stop = bool(floor and flow is not None and flow <= float(floor))

    tx = text_proxy(im)
    return {
        "ts": time.time(),
        "mean": mean, "edge": edge,
        "top_lum": top_lum, "top_edge": top_ed,
        "ctr_lum": ctr_lum, "ctr_edge": ctr_edge, "ctr_std": ctr_std,
        "bot_lum": bot_lum, "bot_edge": bot_ed, "bot_std": bot_std,
        "openness": round(top_lum - bot_lum, 1),
        "sky_clean": round(1.0 / (1.0 + top_ed), 3),
        "text": tx,
        "bubble": find_bubble(im, tx),
        "ring": ring_estimate(g, ctr_edge, ctr_std),
        "flow": flow,
        "stop": stop,
        "sig": sig,
    }


if __name__ == "__main__":
    import sys

    folder = sys.argv[1] if len(sys.argv) > 1 else ""
    if folder and os.path.isdir(folder):
        files = sorted([f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg"))],
                       key=lambda x: int("".join(c for c in x if c.isdigit()) or 0))
        prev, rows = None, []
        t0 = time.time()
        for f in files:
            r = analyze(Image.open(os.path.join(folder, f)), prev)
            prev = r["sig"]
            r.pop("sig")
            rows.append((f, r))
            print("%-10s ring=%-3s(%.2f) text=%-3s %.2f bubble=%-3s %.2f flow=%s stop=%s" % (
                f, r["ring"]["label"], r["ring"]["conf"], r["text"]["hit"],
                r["text"]["score"], r["bubble"]["hit"], r["bubble"]["score"],
                r["flow"], r["stop"]))
        print("耗时 %.2fs / %d 帧" % (time.time() - t0, len(files)))
    else:
        print("用法: python signals.py <图片目录>")
