# -*- coding: utf-8 -*-
"""窗口标题与图标：黑底白色的游戏手柄。"""

import ctypes
import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageTk

APP_NAME = "游戏搭子"
APP_ID = "HerWorld.GameCompanion"


def _draw_icon(size=256):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    k = size / 256.0

    # 黑色圆角底
    d.rounded_rectangle((10 * k, 10 * k, 246 * k, 246 * k),
                        radius=54 * k, fill=(0, 0, 0, 255))

    # 白色手柄：中间一条，左右两个握把
    d.rounded_rectangle((48 * k, 96 * k, 208 * k, 164 * k),
                        radius=34 * k, fill=(255, 255, 255, 255))
    d.ellipse((30 * k, 116 * k, 94 * k, 190 * k), fill=(255, 255, 255, 255))
    d.ellipse((162 * k, 116 * k, 226 * k, 190 * k), fill=(255, 255, 255, 255))

    # 黑色十字键
    d.rounded_rectangle((78 * k, 118 * k, 120 * k, 142 * k),
                        radius=5 * k, fill=(0, 0, 0, 255))
    d.rounded_rectangle((88 * k, 108 * k, 110 * k, 152 * k),
                        radius=5 * k, fill=(0, 0, 0, 255))

    # 黑色两个按键
    d.ellipse((166 * k, 118 * k, 180 * k, 132 * k), fill=(0, 0, 0, 255))
    d.ellipse((188 * k, 130 * k, 202 * k, 144 * k), fill=(0, 0, 0, 255))
    return im


def _icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "assets", "icon.ico")
    if os.path.isfile(path):
        return path
    # 资源被删了也能启动：画一个同名图标放到临时目录
    path = os.path.join(tempfile.gettempdir(), "game_companion_icon.ico")
    _draw_icon(256).save(path, format="ICO",
                         sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                (64, 64), (128, 128), (256, 256)])
    return path


def apply(root):
    """把窗口图标换成自带图标，并让 Windows 任务栏当成独立程序。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    try:
        path = _icon_path()
        root.iconbitmap(default=path)
        photo = ImageTk.PhotoImage(_draw_icon(256))
        root.iconphoto(True, photo)
        # Tk 不持有 PhotoImage 的引用，不挂住会被回收，图标又变回羽毛。
        root._app_icon_photo = photo
        root._app_icon_path = path
    except Exception:
        pass
