# -*- coding: utf-8 -*-
"""B4：flow 绝对标定。

「他停了」这条判断原来用的是拍的 6.0，没人知道 6 是不是真静止。这个脚本让你
**原地不动站 20 秒**，录 10 帧，量出真静止时的帧间差，把地板写进 flow_baseline.json。
标定过之后，调度层才允许说「他没动了」；没标定就一直不许说（见 router.detect）。

用法:
    python tools/calibrate_flow.py            # 用 config.json 里的选区/窗口抓 10 帧
    python tools/calibrate_flow.py 15         # 抓 15 帧
    python tools/calibrate_flow.py --clear    # 删掉标定结果
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import capture  # noqa: E402
import config as cfgmod  # noqa: E402
import signals  # noqa: E402


def main():
    if "--clear" in sys.argv:
        try:
            os.remove(signals.FLOW_PATH)
            print("已删掉标定结果（%s）。这一版将不允许判断「他停了」。" % signals.FLOW_PATH)
        except FileNotFoundError:
            print("还没有标定结果。")
        return

    n = 10
    for a in sys.argv[1:]:
        if a.isdigit():
            n = max(4, min(60, int(a)))
    cfg = cfgmod.load()
    print("=" * 60)
    print("原地不动的标定：接下来 %.0f 秒，请不要移动鼠标、不要让画面动。" % (n * 0.6))
    print("（按住 Shift 走动会让画面动，标定就不准了）")
    print("=" * 60)
    time.sleep(2)

    prev = None
    samples = []
    for i in range(n):
        img, err, label = capture.grab_current(cfg)
        if img is None:
            print("抓帧失败：%s" % err)
            return 1
        sig = signals.signature(img)
        if prev is not None:
            f = signals.sig_diff(sig, prev)
            samples.append(round(f, 2))
            print("  第 %d 帧 flow=%.2f" % (i + 1, f))
        prev = sig
        time.sleep(0.6)

    if not samples:
        print("样本不足。")
        return 1
    d = signals.write_flow_baseline(samples)
    print("\n样本：%s" % samples)
    print("中位数 %.2f / 最大 %.2f → 地板 = %.2f（= 中位数 × 1.6，最低 1.2）"
          % (d["median"], d["max"], d["floor"]))
    print("已写入：%s" % signals.FLOW_PATH)
    print("\n注意：真正静止时 flow 应该在个位数。如果中位数就很大（>10），说明画面本身在动"
          "（粒子/雨/NPC 走动），这个地板会把「走得慢」当「停下」——换一个真正静止的场景再标定。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
