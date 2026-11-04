#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云档启动器（GUI）—— 游戏存档同步/备份工具的图形界面版

启动器风格：
  左侧游戏库卡片（名称/模式/游玩时长，可选封面图）
  右侧详情 + ▶ 开始游戏 / 仅同步 / 编辑 / 删除
  会话进行时：阶段状态 + 实时日志 + 提前结束并同步 / 中止会话
  所有配置（游戏、目录对、排除、全局参数）均在界面内编辑，保存回 云档.toml

命令行参数：
  --config 路径      指定配置文件（默认与本程序同目录 云档.toml）
  --smoke 毫秒       自检模式：启动后自动退出（供测试）
  --shot 路径        配合 --smoke：退出前截图保存
"""

import argparse
import ctypes
import hashlib
import os
import queue
import re
import sys
import threading
import time
import traceback
from ctypes import wintypes

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import 云档 as engine  # noqa: E402  同目录引擎模块

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None

MODE_LABELS = {'timestamp': '时间戳', 'mirror': '镜像', 'archive': '归档'}
LABEL2MODE = {v: k for k, v in MODE_LABELS.items()}

FONT = 'Microsoft YaHei UI'
C_GREEN = '#2fa572'
C_GREEN_H = '#278c60'
C_RED = '#c04848'
C_RED_H = '#a53b3b'
C_CARD = '#1b1b1b'
C_CARD_H = '#262626'
C_CARD_SEL = '#1f4e79'

PHASE_LABEL = {
    '预检': '正在预检…', '锁定': '正在获取网络同步锁…', '恢复': '正在恢复：网络 → 本地…',
    '启动': '正在启动游戏…', '等待启动': '等待游戏进程出现…', '等待退出': '游戏运行中',
    '推送': '正在推送：本地 → 网络…', '完成': '已完成',
}


# ----------------------------------------------------------------------------
# 桥接：引擎(工作线程) <-> UI(主线程)
# ----------------------------------------------------------------------------
class GuiReporter(engine.Reporter):
    def __init__(self, q):
        self.q = q

    def info(self, msg=''):
        s = str(msg)
        for line in s.splitlines() or ['']:
            engine.LOG.info(line)
        self.q.put(('log', s))

    def phase(self, name, detail=''):
        engine.LOG.info('[阶段] %s %s', name, detail)
        self.q.put(('phase', name, detail))

    def event(self, name, data=None):
        self.q.put(('event', name, data))


class GuiBridge(engine.UiBridge):
    def __init__(self, app):
        self.app = app

    def ask_option(self, title, message, options, default_index=0):
        return self.app.modal_choice(title, message, options, default_index)


def to_raw(cfg):
    """把已归一化的配置转回 TOML 原始结构（供校验与保存）"""
    games = []
    for g in cfg['games']:
        games.append({k: g[k] for k in ('id', 'name', 'exe', 'args', 'workdir', 'process',
                                        'mode', 'exclude', 'backup_keep', 'cover', 'sync')
                      if g.get(k) not in (None, '', [])})
    return {'general': {'backup_keep': cfg['backup_keep'], 'timeout_launch': cfg['timeout_launch'],
                        'net_wait': cfg['net_wait']},
            'game': games}


def center_window(win, parent=None, size_pt=None):
    """把窗口定位到屏幕中央（parent=None）或父窗口中央。
    size_pt=(宽,高)：隐藏状态下 winfo 尺寸不可靠（CTk 停留在初始 200x200），需显式传入点单位尺寸。
    注意：Windows 下 winfo_screenwidth 是逻辑像素而映射后的 winfo_width 是物理像素，需按缩放换算。"""
    win.update_idletasks()
    try:
        scale = win._get_window_scaling()
    except Exception:
        scale = 1.0
    if size_pt is not None:
        w_px, h_px = size_pt[0] * scale, size_pt[1] * scale
    else:
        if win.winfo_width() < 10:
            return
        w_px, h_px = win.winfo_width(), win.winfo_height()
    if parent is None:
        sw = win.winfo_screenwidth() * scale
        sh = win.winfo_screenheight() * scale
        x = (sw - w_px) / 2
        y = (sh - h_px) / 2
    else:
        x = parent.winfo_rootx() + (parent.winfo_width() - w_px) / 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h_px) / 2
    win.geometry(f'+{max(0, int(x))}+{max(0, int(y))}')


def fmt_hours(minutes):
    return f'{minutes / 60:.1f}h' if minutes else '—'


def show_dialog_robust(win, parent=None, size_pt=None, grab=True):
    """统一的安全弹窗显示（修复“点击统计/设置/编辑偶发一闪而过且整个程序假死”）：
    CTkToplevel 在 Windows 上会执行“标题栏配色”例程（super().__init__ 内同步一次，
    resizable() 还会额外再排一次），例程结束时会把窗口恢复为例程开始时的状态。
    若例程晚于本方的 deiconify 才执行，窗口会被再次隐藏——表现为“一闪而过就没了”；
    旧代码随后又无条件 grab_set，隐藏窗口持有 grab → 整个程序无响应。
    这里改为：显示 → 延时复查（若被例程隐藏则重新显示）→ 仅当窗口真实可见时才 grab。"""

    def _grab():
        if not grab or not win.winfo_exists():
            return
        try:
            if win.state() == 'normal':
                win.grab_set()
        except tk.TclError:
            pass

    def _recheck():
        if not win.winfo_exists():
            return
        try:
            if win.state() in ('withdrawn', 'iconic'):
                win.deiconify()
        except tk.TclError:
            return
        win.after(60, _grab)

    def _show():
        if not win.winfo_exists():
            return
        try:
            sp = size_pt
            if sp is None:
                win.update_idletasks()
                sp = (win.winfo_reqwidth(), win.winfo_reqheight())
            center_window(win, parent, size_pt=sp)
            win.deiconify()
        except Exception:
            return
        win.after(120, _recheck)   # 复查点晚于 CTk 标题栏例程（创建后 ~10ms+update 耗时）
        win.after(350, _recheck)   # 二次兜底（系统繁忙时例程可能延迟）

    win.after(30, _show)


def to_app_relative(path):
    """位于程序目录内的路径存为相对路径（如 covers/icon_x.png），便于整个文件夹拷走；否则保留绝对路径"""
    try:
        rel = os.path.relpath(path, engine.APP_DIR)
    except ValueError:
        return path
    if rel.startswith('..'):
        return path
    return rel.replace('/', '\\')


def resolve_app_path(path):
    """把相对路径解析回程序目录下的绝对路径"""
    if path and not os.path.isabs(path):
        return os.path.join(engine.APP_DIR, path)
    return path


def short(p, limit=46):
    """长路径中段省略，保持界面整洁"""
    p = str(p)
    if len(p) <= limit:
        return p
    return p[:18] + ' … ' + p[-(limit - 21):]


def extract_exe_icon(exe_path, out_png, size=256):
    """提取 exe/lnk 的内嵌图标并保存为 PNG（Windows）。成功返回 True。
    优先用 SHDefExtractIconW 请求大图标，失败则逐级降尺寸，最后回退 ExtractAssociatedIconW。"""
    if os.name != 'nt' or Image is None:
        return False
    if not exe_path or not os.path.isfile(exe_path):
        return False
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class ICONINFO(ctypes.Structure):
        _fields_ = [('fIcon', wintypes.BOOL), ('xHotspot', wintypes.DWORD),
                    ('yHotspot', wintypes.DWORD), ('hbmMask', wintypes.HBITMAP),
                    ('hbmColor', wintypes.HBITMAP)]

    class BMIH(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    # 64 位句柄必须显式声明 argtypes，否则默认按 32 位 int 传参溢出
    shell32.SHDefExtractIconW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                          ctypes.POINTER(wintypes.UINT),
                                          ctypes.POINTER(wintypes.HICON),
                                          ctypes.POINTER(wintypes.HICON), wintypes.UINT]
    shell32.ExtractAssociatedIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    shell32.ExtractAssociatedIconW.restype = wintypes.HICON
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]

    def _hicon_to_png(hicon):
        info = ICONINFO()
        if not user32.GetIconInfo(hicon, ctypes.byref(info)):
            return False
        ok = False
        try:
            if info.hbmColor:
                hdc = user32.GetDC(0)
                bi = BMIH()
                bi.biSize = ctypes.sizeof(BMIH)
                bi.biWidth = size
                bi.biHeight = -size          # 自顶向下
                bi.biPlanes = 1
                bi.biBitCount = 32
                buf = ctypes.create_string_buffer(size * size * 4)
                got = gdi32.GetDIBits(hdc, info.hbmColor, 0, size, buf, ctypes.byref(bi), 0)
                user32.ReleaseDC(0, hdc)
                if got:
                    img = Image.frombytes('RGBA', (size, size), buf.raw)
                    r, g, b, a = img.split()
                    os.makedirs(os.path.dirname(out_png) or '.', exist_ok=True)
                    Image.merge('RGBA', (b, g, r, a)).save(out_png, 'PNG')
                    ok = True
        finally:
            if info.hbmColor:
                gdi32.DeleteObject(info.hbmColor)
            if info.hbmMask:
                gdi32.DeleteObject(info.hbmMask)
            user32.DestroyIcon(hicon)
        return ok

    for s in (size, 128, 64, 48, 32):
        hicon = wintypes.HICON()
        flags = wintypes.UINT(0)
        nsize = ((s & 0xFFFF) << 16) | (s & 0xFFFF)
        if shell32.SHDefExtractIconW(str(exe_path), 0, ctypes.byref(flags),
                                     ctypes.byref(hicon), None, nsize) == 0 and hicon.value:
            if _hicon_to_png(hicon):
                return True
    # 回退：关联图标（兼容 .lnk）
    try:
        idx = wintypes.UINT(0)
        hicon = shell32.ExtractAssociatedIconW(None, str(exe_path), ctypes.byref(idx))
        if hicon:
            return _hicon_to_png(hicon)
    except Exception:
        pass
    return False


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class App(ctk.CTk):
    def __init__(self, config_path, cfg, startup_error=None, smoke_ms=0, shot_path=None):
        super().__init__()
        self.cfg_path = config_path
        self.cfg = cfg
        self.startup_error = startup_error
        self.q = queue.Queue()
        self.selected = None
        self.running = False
        self.control = None
        self.launch_time = None
        self.smoke_ms = smoke_ms
        self.shot_path = shot_path

        self.title(f'云档启动器 v{engine.VERSION}')
        self.geometry('1080x700')
        self.minsize(960, 620)
        self.configure(fg_color='#101010')
        self.withdraw()   # 先隐藏：按已知尺寸定位到屏幕中央后再显示，避免界面闪现
        self.after(150, self._show_centered)

        if Image is not None:
            try:
                self.iconbitmap(default='')
            except Exception:
                pass

        self._build()
        if cfg and cfg['games']:
            self.selected = 0
        self.refresh_all()
        self._pending_close = False
        self._close_dlg = None     # 当前打开的关闭警告窗（防重入）
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(80, self._poll)

        if startup_error:
            self.after(300, lambda: messagebox.showerror('配置错误', startup_error, parent=self))
        if smoke_ms:
            if shot_path:
                self.after(max(300, smoke_ms - 900), self._take_shot)
            self.after(smoke_ms, self.destroy)

    # ---------------- 界面搭建 ----------------
    def _show_centered(self):
        center_window(self, size_pt=(1080, 700))
        self.deiconify()

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 顶栏
        top = ctk.CTkFrame(self, height=54, fg_color='#161616', corner_radius=0)
        top.grid(row=0, column=0, columnspan=2, sticky='ew')
        ctk.CTkLabel(top, text='  云档启动器', font=ctk.CTkFont(FONT, 20, 'bold')).pack(side='left', padx=12, pady=10)
        ctk.CTkLabel(top, text='存档同步 · 开玩前拉取 / 退出后推送', font=ctk.CTkFont(FONT, 12),
                     text_color='#8a8a8a').pack(side='left', padx=4)
        for text, cmd in (('统计', self.show_stats), ('全局设置', self.show_settings)):
            ctk.CTkButton(top, text=text, width=92, height=32, fg_color='#2a2a2a',
                          hover_color='#3a3a3a', command=cmd).pack(side='right', padx=6, pady=10)

        # 左侧：游戏库
        self.sidebar = ctk.CTkScrollableFrame(self, width=300, fg_color='#141414', corner_radius=0)
        self.sidebar.grid(row=1, column=0, sticky='nsw')
        ctk.CTkLabel(self.sidebar, text='游戏库', font=ctk.CTkFont(FONT, 15, 'bold')).pack(anchor='w', padx=14, pady=(10, 6))
        self.cards_frame = ctk.CTkFrame(self.sidebar, fg_color='transparent')
        self.cards_frame.pack(fill='x', padx=8)
        self.btn_add = ctk.CTkButton(self.sidebar, text='+ 添加游戏', height=40,
                                     fg_color='#232323', hover_color='#333333',
                                     command=self.on_add_game)
        self.btn_add.pack(fill='x', padx=8, pady=10)

        # 右侧：详情
        self.main = ctk.CTkFrame(self, fg_color='transparent')
        self.main.grid(row=1, column=1, sticky='nsew', padx=14, pady=(10, 12))
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(4, weight=1)   # 多余空间全部给日志框（而非标签行）
        self._build_detail()

    def _build_detail(self):
        # 头部：封面 + 名称 + 元信息
        head = ctk.CTkFrame(self.main, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew')
        self.cover_box = ctk.CTkFrame(head, width=96, height=96, fg_color='transparent')
        self.cover_box.pack(side='left', padx=(0, 14))
        self.cover_box.pack_propagate(False)
        self.lbl_cover = ctk.CTkLabel(self.cover_box, text='?', font=ctk.CTkFont(FONT, 36, 'bold'),
                                      fg_color=C_CARD, corner_radius=10)
        self.lbl_cover.pack(fill='both', expand=True)
        info = ctk.CTkFrame(head, fg_color='transparent')
        info.pack(side='left', fill='x', expand=True)
        self.lbl_name = ctk.CTkLabel(info, text='未选择游戏', font=ctk.CTkFont(FONT, 24, 'bold'))
        self.lbl_name.pack(anchor='w')
        self.lbl_meta = ctk.CTkLabel(info, text='从左侧选择一个游戏，或点击“+ 添加游戏”', justify='left',
                                     text_color='#9a9a9a', font=ctk.CTkFont(FONT, 13))
        self.lbl_meta.pack(anchor='w', pady=(4, 0))

        # 会话状态条
        self.status_bar = ctk.CTkFrame(self.main, fg_color='transparent')
        self.status_bar.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        self.lbl_status = ctk.CTkLabel(self.status_bar, text='就绪', font=ctk.CTkFont(FONT, 14, 'bold'),
                                       text_color='#8fd6a8')
        self.lbl_status.pack(side='left')
        self.btn_stopwait = ctk.CTkButton(self.status_bar, text='提前结束并同步', width=140, height=32,
                                          fg_color='#b0762c', hover_color='#96641f',
                                          command=self.on_stop_wait)
        self.btn_abort = ctk.CTkButton(self.status_bar, text='中止会话', width=100, height=32,
                                       fg_color=C_RED, hover_color=C_RED_H,
                                       command=self.on_abort)

        # 操作按钮
        acts = ctk.CTkFrame(self.main, fg_color='transparent')
        acts.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        self.btn_play = ctk.CTkButton(acts, text='▶  开始游戏', width=170, height=42,
                                      font=ctk.CTkFont(FONT, 16, 'bold'),
                                      fg_color=C_GREEN, hover_color=C_GREEN_H,
                                      command=lambda: self.start_session(False))
        self.btn_play.pack(side='left')
        self.btn_down = ctk.CTkButton(acts, text='仅下载', width=88, height=42,
                                      fg_color='#2a2a2a', hover_color='#3a3a3a',
                                      command=lambda: self.start_session(True, 'down'))
        self.btn_down.pack(side='left', padx=(8, 0))
        self.btn_up = ctk.CTkButton(acts, text='仅上传', width=88, height=42,
                                    fg_color='#2a2a2a', hover_color='#3a3a3a',
                                    command=lambda: self.start_session(True, 'up'))
        self.btn_up.pack(side='left', padx=(8, 0))
        self.btn_opendir = ctk.CTkButton(acts, text='打开存档目录', width=110, height=42,
                                         fg_color='#2a2a2a', hover_color='#3a3a3a',
                                         command=self.on_open_dir)
        self.btn_opendir.pack(side='left', padx=(16, 0))
        self.btn_edit = ctk.CTkButton(acts, text='编辑', width=76, height=42,
                                      fg_color='#2a2a2a', hover_color='#3a3a3a',
                                      command=self.on_edit_game)
        self.btn_edit.pack(side='right', padx=(8, 0))
        self.btn_del = ctk.CTkButton(acts, text='删除', width=76, height=42,
                                     fg_color='#2a2a2a', hover_color=C_RED,
                                     command=self.on_del_game)
        self.btn_del.pack(side='right')

        # 日志（紧跟按钮行；日志框自身占满剩余空间）
        self.lbl_log = ctk.CTkLabel(self.main, text='会话日志', font=ctk.CTkFont(FONT, 13, 'bold'),
                                    text_color='#9a9a9a')
        self.lbl_log.grid(row=3, column=0, sticky='w', pady=(10, 2))
        self.txt_log = ctk.CTkTextbox(self.main, font=ctk.CTkFont('Consolas', 12),
                                      fg_color='#0d0d0d', text_color='#c8c8c8')
        self.txt_log.grid(row=4, column=0, sticky='nsew')
        self.txt_log.configure(state='disabled')

    # ---------------- 刷新 ----------------
    def refresh_all(self):
        self._refresh_cards()
        self._refresh_detail()

    def _refresh_cards(self):
        for w in self.cards_frame.winfo_children():
            w.destroy()
        games = self.cfg['games'] if self.cfg else []
        for i, g in enumerate(games):
            total, week = (0.0, 0.0)
            try:
                total, week = engine.game_minutes(self.cfg['stats_csv'], g['name'], g.get('id'))
            except Exception:
                pass
            sel = (i == self.selected)
            # 两行式卡片：标题略大、副行信息略小（旧版单按钮只能同一字号）
            card = ctk.CTkFrame(self.cards_frame, height=72, corner_radius=10,
                                fg_color=C_CARD_SEL if sel else C_CARD)
            card.pack(fill='x', pady=3)
            card.pack_propagate(False)
            lbl_t = ctk.CTkLabel(card, text=g['name'], anchor='w', justify='left',
                                 font=ctk.CTkFont(FONT, 16, 'bold'))
            lbl_t.pack(anchor='w', padx=12, pady=(9, 0))
            sub = f"{MODE_LABELS.get(g['mode'], g['mode'])} · 累计 {fmt_hours(total)} · 本周 {fmt_hours(week)}"
            lbl_s = ctk.CTkLabel(card, text=sub, anchor='w', justify='left',
                                 text_color='#9a9a9a', font=ctk.CTkFont(FONT, 11))
            lbl_s.pack(anchor='w', padx=12, pady=(1, 8))
            normal = C_CARD_SEL if sel else C_CARD
            hover = C_CARD_SEL if sel else C_CARD_H

            def _click(e, i=i):
                self.select_game(i)

            def _enter(e, c=card, h=hover):
                c.configure(fg_color=h)

            def _leave(e, c=card, n=normal):
                c.configure(fg_color=n)

            for wgt in (card, lbl_t, lbl_s):
                wgt.bind('<Button-1>', _click)
                wgt.bind('<Enter>', _enter)
                wgt.bind('<Leave>', _leave)

    def _refresh_detail(self):
        games = self.cfg['games'] if self.cfg else []
        if self.selected is None or not (0 <= self.selected < len(games)):
            self.lbl_name.configure(text='未选择游戏')
            self.lbl_meta.configure(text='从左侧选择一个游戏，或点击“+ 添加游戏”')
            self._set_cover(None)
            return
        g = games[self.selected]
        self.lbl_name.configure(text=g['name'])
        pairs = '\n'.join(f"· {short(p['local'])}  ↔  {short(p['net'])}" for p in g['sync']) or '（未配置目录对）'
        excl = '、'.join(g['exclude']) or '无'
        proc = '自动跟踪进程树' if not g['process'] else '、'.join(g['process'])
        exe = short(g['exe']) if g['exe'] else '（仅同步）'
        meta = (f"模式：{MODE_LABELS.get(g['mode'], g['mode'])}    备份保留：{g['backup_keep']} 份    等待：{proc}\n"
                f"程序：{exe}\n"
                f"目录对：\n{pairs}\n"
                f"排除：{excl}")
        self.lbl_meta.configure(text=meta, text_color='#b8b8b8', wraplength=640, justify='left')
        self._set_cover(g.get('cover'))

    def _set_cover(self, path):
        """重建封面标签——CTkLabel 的 image=None 不会清除已显示图像，销毁重建才彻底"""
        img = None
        full = resolve_app_path(path)   # 相对路径（covers\…）解析回程序目录
        if full and Image is not None and os.path.isfile(full):
            try:
                img = ctk.CTkImage(Image.open(full), size=(96, 96))
            except Exception:
                img = None
        self.lbl_cover.destroy()
        if img is not None:
            self.lbl_cover = ctk.CTkLabel(self.cover_box, image=img, text='')
            self.lbl_cover._image_ref = img   # 防 CTkImage 被垃圾回收
        else:
            name = (self.cfg['games'][self.selected]['name'] if self._has_selection() else '?')
            self.lbl_cover = ctk.CTkLabel(self.cover_box, text=name[:1],
                                          font=ctk.CTkFont(FONT, 36, 'bold'),
                                          fg_color=C_CARD, corner_radius=10)
        self.lbl_cover.pack(fill='both', expand=True)

    def _has_selection(self):
        return (self.cfg is not None and self.selected is not None
                and 0 <= self.selected < len(self.cfg['games']))

    def select_game(self, i):
        if self.running:
            return
        self.selected = i
        self._refresh_cards()
        self._refresh_detail()

    # ---------------- 配置操作 ----------------
    def _save_cfg(self):
        try:
            engine.save_toml(self.cfg_path, self.cfg)
            return True
        except Exception as e:
            messagebox.showerror('保存失败', f'写入配置失败：{e}', parent=self)
            return False

    def _validate_and_normalize(self):
        try:
            normalized = engine.normalize_config(to_raw(self.cfg))
            engine.validate_config(normalized)
            self.cfg = normalized
            return True
        except engine.SessionError as e:
            messagebox.showerror('配置校验失败', e.msg, parent=self)
            return False

    def apply_game(self, game_dict, index):
        """编辑/新增保存入口（由编辑器对话框调用）。返回 True 表示已应用。"""
        if not self._ensure_cfg():
            return False
        games = self.cfg['games']
        if index is None:
            games.append(game_dict)
        else:
            # 保险：编辑路径若未带回旧ID，从原条目恢复，避免换ID导致统计分裂
            if not str(game_dict.get('id') or '').strip() and index < len(games):
                game_dict['id'] = games[index].get('id', '')
            games[index] = game_dict
        engine.ensure_game_ids(self.cfg)   # 新增游戏补发隐藏ID
        if not self._validate_and_normalize():
            if index is None:
                games.pop()
            return False
        if not self._save_cfg():
            return False
        self.selected = len(games) - 1 if index is None else index
        self.refresh_all()
        return True

    def apply_general(self, backup_keep, timeout_launch, net_wait):
        self.cfg['backup_keep'] = backup_keep
        self.cfg['timeout_launch'] = timeout_launch
        self.cfg['net_wait'] = net_wait
        if not self._validate_and_normalize():
            return False
        if not self._save_cfg():
            return False
        self._refresh_cards()
        return True

    def _ensure_cfg(self):
        if self.cfg is None:
            self.cfg = engine.normalize_config({'game': []})
        return True

    # ---------------- 事件：配置 ----------------
    def on_add_game(self):
        if self.running:
            return
        GameEditor(self, None)

    def on_edit_game(self):
        if self.running or not self._has_selection():
            return
        GameEditor(self, self.selected)

    def on_del_game(self):
        if self.running or not self._has_selection():
            return
        g = self.cfg['games'][self.selected]
        if not messagebox.askyesno('删除游戏', f"确定删除「{g['name']}」的配置？\n（网络端存档与备份不会被删除）", parent=self):
            return
        self.cfg['games'].pop(self.selected)
        if self._save_cfg():
            self.selected = None
            self.refresh_all()

    def on_open_dir(self):
        if not self._has_selection():
            return
        loc = self.cfg['games'][self.selected]['sync'][0]['local']
        try:
            os.startfile(loc)
        except OSError as e:
            messagebox.showerror('打开失败', str(e), parent=self)

    # ---------------- 事件：会话 ----------------
    def on_close(self):
        """关闭窗口：会话进行中先弹深色警告（游戏运行中三选/同步中二选）"""
        if self._pending_close:
            # 已确认过“强制退出”，正在等会话收尾（或 8 秒兜底）：
            # 此时再点 X 不应重复弹确认框，直接销毁窗口
            try:
                self.destroy()
            except Exception:
                pass
            return
        if self._close_dlg is not None and self._close_dlg.winfo_exists():
            return   # 警告窗已在前台（任务栏关闭等旁路触发），忽略重复请求
        if not self.running:
            self.destroy()
            return
        if self.launch_time:
            ans = self._ask_close_choice('game')
            if ans is None:
                return
            if ans == 'minimize':
                self.iconify()   # 最小化，会话继续，游戏退出后自动推送
                return
            self._force_close()
        else:
            if self._ask_close_choice('sync') == 'force':
                self._force_close()

    def _ask_close_choice(self, mode):
        """弹出深色关闭警告并等待选择：'force' / 'minimize' / None（取消）。"""
        dlg = None
        try:
            dlg = CloseWarnDialog(self, mode)
            self._close_dlg = dlg
            # 兜底看门狗：万一警告窗被隐藏竞态卡住（grab 未生效且不可见），
            # 1.5 秒后自动取消，避免 wait_window 无限挂起导致程序假死
            self.after(1500, lambda d=dlg: self._close_dlg_watchdog(d))
            self.wait_window(dlg)
            return dlg.result
        except Exception:
            engine.LOG.exception('关闭确认窗口异常，按“取消”处理')
            return None
        finally:
            self._close_dlg = None

    def _close_dlg_watchdog(self, dlg):
        """只盯住创建时传入的那个警告窗，不误伤后续新弹窗"""
        if dlg is not None and dlg.winfo_exists():
            try:
                if dlg.state() in ('withdrawn', 'iconic'):
                    engine.LOG.warning('关闭警告窗未正常显示，自动按“取消”处理')
                    dlg.destroy()
            except Exception:
                pass

    def _force_close(self):
        """中止会话并等待其干净退出（释放锁/标记）后关闭；8 秒兜底强退"""
        if self.control is not None:
            self.control.abort.set()
        self._pending_close = True
        self.btn_stopwait.configure(state='disabled')
        self.btn_abort.configure(state='disabled')
        self.lbl_status.configure(text='正在中止…', text_color='#e0b050')
        self.after(8000, self._destroy_if_pending)

    def _destroy_if_pending(self):
        if self._pending_close:
            try:
                self.destroy()
            except Exception:
                pass

    def start_session(self, sync_only, direction='both'):
        if self.running or not self._has_selection():
            if not self._has_selection():
                messagebox.showinfo('提示', '请先选择一个游戏。', parent=self)
            return
        game = self.cfg['games'][self.selected]
        self.running = True
        self.launch_time = None
        self.control = engine.SessionControl()
        self._set_running_ui(True)
        self._log_line('')
        self._log_line('─' * 56)
        q = self.q
        bridge, control = GuiBridge(self), self.control

        def work():
            code = engine.EXIT_OK
            try:
                code = engine.run_session(self.cfg, game, dry_run=False, sync_only=sync_only,
                                          direction=direction, bridge=bridge, control=control,
                                          reporter=GuiReporter(q))
            except engine.SessionError as e:
                engine.LOG.warning('会话中止: %s', e.msg)
                q.put(('log', f'[错误] {e.msg}'))
                code = e.code
            except Exception:
                engine.LOG.exception('未预期的异常')
                q.put(('log', '[异常] ' + (traceback.format_exc().splitlines()[-1] or '')))
                code = engine.EXIT_UNEXPECTED
            finally:
                q.put(('done', code))

        threading.Thread(target=work, daemon=True).start()

    def on_stop_wait(self):
        if self.control is not None:
            self.control.stop_wait.set()

    def on_abort(self):
        if self.control is None:
            return
        if messagebox.askyesno('中止会话', '确定中止？本次不会推送存档到网络，\n下次运行会按“未正常结束”处理。',
                               parent=self):
            self.control.abort.set()

    def _set_running_ui(self, running):
        state = 'disabled' if running else 'normal'
        for b in (self.btn_play, self.btn_down, self.btn_up, self.btn_edit, self.btn_del,
                  self.btn_add, self.btn_opendir):
            b.configure(state=state)
        if running:
            self.btn_stopwait.pack(side='right', padx=4)
            self.btn_abort.pack(side='right')
        else:
            self.btn_stopwait.pack_forget()
            self.btn_abort.pack_forget()
        self.lbl_status.configure(text='准备中…' if running else '就绪',
                                  text_color='#e0b050' if running else '#8fd6a8')

    # ---------------- 轮询队列 ----------------
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == 'log':
                    self._log_line(item[1])
                elif kind == 'phase':
                    self.lbl_status.configure(text=PHASE_LABEL.get(item[1], item[1]) +
                                              (f'（{item[2]}）' if item[2] else ''))
                elif kind == 'event' and item[1] == 'launched':
                    self.launch_time = time.time()
                elif kind == 'event' and item[1] == 'exited':
                    self.launch_time = None
                elif kind == 'done':
                    code = item[1]
                    self.running = False
                    self.launch_time = None
                    ok = (code == engine.EXIT_OK)
                    self._set_running_ui(False)
                    if self._pending_close:
                        self.destroy()          # 强制关闭流程：会话已干净退出
                        return
                    if self.state() == 'iconic':
                        self.deiconify()        # 最小化等待的场景：完成后还原窗口
                        self.lift()
                    self.lbl_status.configure(
                        text=('✔ 完成' if ok else f'✘ 结束（退出码 {code}）'),
                        text_color='#8fd6a8' if ok else '#e08080')
                    self._refresh_cards()
                    if ok:
                        self._log_line('')
                        self._log_line('✔ 会话完成')
        except queue.Empty:
            pass
        if self.launch_time:
            secs = int(time.time() - self.launch_time)
            self.lbl_status.configure(text=f'游戏运行中 · 已 {secs // 60:02d}:{secs % 60:02d}')
        self.after(80, self._poll)

    # ---------------- 日志/弹窗/杂项 ----------------
    def _log_line(self, s):
        self.txt_log.configure(state='normal')
        self.txt_log.insert('end', s.rstrip('\n') + '\n')
        if self.txt_log.index('end-1c').split('.')[0] and int(float(self.txt_log.index('end-1c'))) > 900:
            self.txt_log.delete('1.0', '100.0')
        self.txt_log.see('end')
        self.txt_log.configure(state='disabled')

    def modal_choice(self, title, message, options, default_index=0):
        """工作线程调用：主线程弹模态对话框，阻塞等待所选下标。"""
        ev = threading.Event()
        holder = {'idx': default_index}

        def show():
            dlg = None
            try:
                dlg = ctk.CTkToplevel(self)
                dlg.withdraw()   # 先隐藏，定位后再显示
                dlg.title(title)
                dlg.resizable(False, False)
                dlg.attributes('-topmost', True)
                dlg.transient(self)
                frm = ctk.CTkFrame(dlg, fg_color='#1c1c1c')
                frm.pack(fill='both', expand=True, padx=2, pady=2)
                ctk.CTkLabel(frm, text=title, font=ctk.CTkFont(FONT, 16, 'bold')).pack(anchor='w', padx=18, pady=(16, 6))
                ctk.CTkLabel(frm, text=message, justify='left', wraplength=440,
                             font=ctk.CTkFont(FONT, 13)).pack(anchor='w', padx=18, pady=(0, 14))
                row = ctk.CTkFrame(frm, fg_color='transparent')
                row.pack(fill='x', padx=18, pady=(0, 16))

                def pick(i):
                    holder['idx'] = i
                    dlg.destroy()
                    ev.set()

                for i, o in enumerate(options):
                    ctk.CTkButton(row, text=o, command=lambda i=i: pick(i),
                                  fg_color=C_CARD_SEL if i == default_index else '#2a2a2a',
                                  hover_color=C_CARD_SEL if i == default_index else '#3a3a3a',
                                  ).pack(side='right', padx=(6, 0))
                dlg.protocol('WM_DELETE_WINDOW', lambda: pick(default_index))
                show_dialog_robust(dlg, self)   # 居中显示 + 防隐藏竞态 + 安全 grab
            except Exception:
                ev.set()

        self.after(0, show)
        ev.wait()
        return holder['idx']

    def show_stats(self):
        StatsDialog(self)

    def show_settings(self):
        SettingsDialog(self)

    def _take_shot(self):
        if ImageGrab is None:
            return

        def grab():
            try:
                x, y = self.winfo_rootx(), self.winfo_rooty()
                w, h = self.winfo_width(), self.winfo_height()
                ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(self.shot_path)
                print('shot saved:', self.shot_path)
            except Exception as e:
                print('shot failed:', e)

        try:
            self.attributes('-topmost', True)
            self.lift()
            self.focus_force()
            self.update_idletasks()
        except Exception:
            pass
        self.after(400, grab)


# ----------------------------------------------------------------------------
# 对话框：关闭警告 / 游戏编辑器 / 全局设置 / 统计
# ----------------------------------------------------------------------------
class CloseWarnDialog(ctk.CTkToplevel):
    """关闭云档时的警告确认（深色界面风格，替代系统 messagebox）。

    mode='game'  游戏运行中误关 → [强制退出] [最小化] [取消]（用户指定三选项）
    mode='sync'  同步进行中关闭 → [强制退出] [取消]
    选择结果：dlg.result = 'force' | 'minimize' | None（取消 / 关闭警告窗）
    推荐项（Enter）：游戏运行中=最小化；同步中=取消（不触发破坏性操作）
    """

    def __init__(self, app, mode='game'):
        super().__init__(app)
        self.app = app
        self.mode = mode
        self.result = None
        game = (mode == 'game')
        width = 480 if game else 420
        self.title('游戏正在运行' if game else '正在同步')
        self.resizable(False, False)
        self.attributes('-topmost', True)   # 游戏常为全屏/置顶，警告窗须压在其上
        self.transient(app)
        self.configure(fg_color='#181818')
        self.withdraw()   # 先隐藏，定位后再显示

        frm = ctk.CTkFrame(self, fg_color='#1c1c1c', corner_radius=0)
        frm.pack(fill='both', expand=True, padx=2, pady=2)

        # 图标 + 标题（左对齐，与其他对话框一致）
        head = ctk.CTkFrame(frm, fg_color='transparent')
        head.pack(fill='x', padx=24, pady=(22, 10))
        self._warn_icon(head).pack(side='left')
        ctk.CTkLabel(head, text=('游戏正在运行' if game else '正在同步'),
                     font=ctk.CTkFont(FONT, 18, 'bold')).pack(side='left', padx=(14, 0))

        if game:
            body = ('云档正在等待游戏退出，之后会自动把存档推送回网络目录。\n'
                    '若现在关闭云档：\n'
                    '  · 游戏本身不受影响（独立进程）\n'
                    '  · 游戏退出后不会自动推送存档到网络\n'
                    '  · 下次运行会按“未正常结束”处理（镜像模式会要求确认）')
        else:
            body = '云档正在同步存档。\n现在关闭将中止本次同步（本次不推送存档）。'
        ctk.CTkLabel(frm, text=body, justify='left', wraplength=width - 76,
                     font=ctk.CTkFont(FONT, 13), text_color='#c8c8c8').pack(
            anchor='w', padx=24, pady=(0, 16 if game else 0))

        if game:
            # 推荐提示条（呼应主界面绿色系：#15281e 底 / #8fd6a8 字）
            tip = ctk.CTkFrame(frm, fg_color='#15281e', corner_radius=8)
            tip.pack(fill='x', padx=24, pady=(0, 16))
            ctk.CTkLabel(tip, text='推荐选「最小化」：窗口最小化到任务栏，游戏退出后自动完成同步并恢复窗口。',
                         justify='left', wraplength=width - 100,
                         font=ctk.CTkFont(FONT, 12), text_color='#8fd6a8').pack(
                anchor='w', padx=12, pady=8)

        # 按钮行：右对齐，视觉顺序 [强制退出] [最小化] [取消]
        row = ctk.CTkFrame(frm, fg_color='transparent')
        row.pack(fill='x', padx=24, pady=(0, 20))

        picked = {'done': False}   # 防 Enter/点击双触发时对已销毁窗口二次 destroy

        def pick(v):
            if picked['done']:
                return
            picked['done'] = True
            self.result = v
            try:
                self.destroy()
            except Exception:
                pass

        if game:
            specs = (('强制退出', C_RED, C_RED_H, 'force', False),
                     ('最小化', C_GREEN, C_GREEN_H, 'minimize', True),
                     ('取消', '#2a2a2a', '#3a3a3a', None, False))
        else:
            specs = (('强制退出', C_RED, C_RED_H, 'force', False),
                     ('取消', '#2a2a2a', '#3a3a3a', None, False))
        for text, fg, hover, value, bold in reversed(specs):   # reversed + side='right' → 保持specs视觉顺序
            ctk.CTkButton(row, text=text, width=112, height=36,
                          font=ctk.CTkFont(FONT, 14, 'bold' if bold else 'normal'),
                          fg_color=fg, hover_color=hover,
                          command=lambda v=value: pick(v)).pack(side='right', padx=(6, 0))

        # 键盘：Esc=取消；Enter=推荐项（最小化/取消，不触发破坏性操作）
        default = 'minimize' if game else None
        self.bind('<Return>', lambda e: pick(default))
        self.bind('<Escape>', lambda e: pick(None))
        self.protocol('WM_DELETE_WINDOW', lambda: pick(None))   # 点警告窗的 X = 取消

        show_dialog_robust(self, app)   # 居中显示 + 防隐藏竞态 + 安全 grab
        self.after(200, self._focus_self)
        self.after(450, self._focus_self)

    def _focus_self(self):
        """显示后把键盘焦点拿到警告窗，Enter/Esc 快捷键才生效"""
        try:
            if self.winfo_exists() and self.state() == 'normal':
                self.focus_set()
        except Exception:
            pass

    def _warn_icon(self, parent):
        """琥珀色圆形警示图标（Canvas 绘制，与“正在中止…”状态色一致，不依赖符号字体）"""
        try:
            scale = self._get_window_scaling()
        except Exception:
            scale = 1.0
        s = max(30, int(40 * scale))
        cv = tk.Canvas(parent, width=s, height=s, bg='#1c1c1c', highlightthickness=0, bd=0)
        pad = max(1, int(2 * scale))
        cv.create_oval(pad, pad, s - pad, s - pad, fill='#e0b050', outline='')
        cv.create_text(s / 2, s / 2 + scale, text='!', font=(FONT, 19, 'bold'), fill='#181818')
        return cv


class GameEditor(ctk.CTkToplevel):
    def __init__(self, app, index):
        super().__init__(app)
        self.app = app
        self.index = index
        self.game = dict(app.cfg['games'][index]) if index is not None else None
        self.title('编辑游戏' if index is not None else '添加游戏')
        self.geometry('760x700')
        self.minsize(700, 540)
        self.resizable(True, True)
        self.configure(fg_color='#181818')
        self.transient(app)
        self.withdraw()   # 先隐藏，按已知尺寸定位到主窗口中央后再显示

        self._build()
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        # 安全显示：等 CTkToplevel 标题栏例程结束后再显示，且防“被例程再次隐藏”竞态
        show_dialog_robust(self, app, size_pt=(760, 700))

    def _build(self):
        g = self.game or {}
        # 结构：row0 = 可滚动表单，row1 = 固定底部按钮栏（始终可见可点）
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        frm = ctk.CTkScrollableFrame(self, fg_color='#181818', corner_radius=0)
        frm.grid(row=0, column=0, sticky='nsew', padx=14, pady=(12, 0))
        self._btns = []   # 行内按钮引用（测试用：校验不被裁切）

        self.e_name = self._row(frm, 0, '游戏名称')
        self.e_name.insert(0, g.get('name', ''))

        r = 1
        self.e_exe = self._row(frm, r, '程序路径（exe/lnk，留空=仅同步）', browse='file')
        self.e_exe.insert(0, g.get('exe', ''))
        r += 1
        self.e_args = self._row(frm, r, '启动参数（可空）')
        self.e_args.insert(0, g.get('args', ''))
        r += 1
        self.e_workdir = self._row(frm, r, '工作目录（可空，默认 exe 所在目录）', browse='dir')
        self.e_workdir.insert(0, g.get('workdir', ''))
        r += 1
        self.e_cover = self._row(frm, r, '封面图片（可空，png/jpg）', browse='img',
                                 extra=('从图标生成', self._make_icon_from_exe))
        self.e_cover.insert(0, g.get('cover', ''))
        r += 1
        self.e_process = self._row(frm, r, '等待进程（逗号/顿号分隔，空=自动跟踪进程树）')
        self.e_process.insert(0, '、'.join(g.get('process', [])))
        r += 1
        self.e_keep = self._row(frm, r, '备份/快照保留份数（0=不清理）')
        self.e_keep.insert(0, str(g.get('backup_keep', self.app.cfg['backup_keep'] if self.app.cfg else 10)))
        r += 1

        # 模式
        ctk.CTkLabel(frm, text='同步模式', font=ctk.CTkFont(FONT, 13), text_color='#9a9a9a').grid(
            row=r, column=0, sticky='w', pady=(8, 2))
        self.seg_mode = ctk.CTkSegmentedButton(
            frm, values=['时间戳', '镜像', '归档'],
            command=self._on_mode)
        self.seg_mode.set(MODE_LABELS.get(g.get('mode', 'timestamp'), '时间戳'))
        self.seg_mode.grid(row=r, column=1, sticky='w', pady=(8, 2))
        self.lbl_mode_tip = ctk.CTkLabel(frm, text='', font=ctk.CTkFont(FONT, 11), text_color='#7a8a7a',
                                         justify='left')
        self.lbl_mode_tip.grid(row=r + 1, column=1, sticky='w')
        self._on_mode(self.seg_mode.get())
        r += 2

        # 目录对
        ctk.CTkLabel(frm, text='目录对（本地 ↔ 网络）', font=ctk.CTkFont(FONT, 13),
                     text_color='#9a9a9a').grid(row=r, column=0, sticky='w', pady=(6, 2))
        ctk.CTkButton(frm, text='+ 添加目录对', width=110, height=28, fg_color='#232323',
                      hover_color='#333333', command=lambda: self._add_pair('', '')).grid(
            row=r, column=1, sticky='e', pady=(6, 2))
        r += 1
        # 普通容器、高度随内容自适应（外层表单本身可滚动，无需内嵌滚动区）
        self.pairs_frame = ctk.CTkFrame(frm, fg_color='#141414', corner_radius=8)
        self.pairs_frame.grid(row=r, column=0, columnspan=2, sticky='ew')
        self.pair_rows = []
        for p in (g.get('sync') or [{'local': '', 'net': ''}]):
            self._add_pair(p.get('local', ''), p.get('net', ''))
        r += 1

        # 排除
        ctk.CTkLabel(frm, text='排除规则（每行一条，如 *.tmp / shadercache/）', font=ctk.CTkFont(FONT, 13),
                     text_color='#9a9a9a').grid(row=r, column=0, sticky='w', pady=(8, 2))
        r += 1
        self.txt_exclude = ctk.CTkTextbox(frm, height=68, font=ctk.CTkFont(FONT, 12), fg_color='#0d0d0d')
        self.txt_exclude.grid(row=r, column=0, columnspan=2, sticky='ew')
        self.txt_exclude.insert('1.0', '\n'.join(g.get('exclude', [])))
        r += 1

        # 底部按钮栏：固定在窗口底部，不随表单滚动
        footer = ctk.CTkFrame(self, fg_color='transparent', height=56)
        footer.grid(row=1, column=0, sticky='ew', padx=14, pady=10)
        self.btn_save = ctk.CTkButton(footer, text='保存', width=110, height=36,
                                      fg_color=C_GREEN, hover_color=C_GREEN_H,
                                      font=ctk.CTkFont(FONT, 14, 'bold'),
                                      command=self.on_save)
        self.btn_save.pack(side='right')
        ctk.CTkButton(footer, text='取消', width=90, height=36,
                      fg_color='#2a2a2a', hover_color='#3a3a3a',
                      command=self.destroy).pack(side='right', padx=(0, 8))
        ctk.CTkLabel(footer, text='目录对可增删行；路径含反斜杠直接粘贴即可',
                     font=ctk.CTkFont(FONT, 11), text_color='#6a6a6a').pack(side='left')

    def _row(self, frm, r, label, browse=None, extra=None):
        """一行 = 标签 + 输入框（自动伸展）+ 可选按钮。
        按钮先按 side='right' 入包再放输入框，保证按钮永远不会被输入框挤出可视区。"""
        ctk.CTkLabel(frm, text=label, font=ctk.CTkFont(FONT, 13), text_color='#9a9a9a').grid(
            row=r, column=0, sticky='w', pady=(8, 0))
        holder = ctk.CTkFrame(frm, fg_color='transparent')
        holder.grid(row=r, column=1, sticky='ew', pady=(8, 0))
        e = ctk.CTkEntry(holder, width=300, font=ctk.CTkFont(FONT, 12))

        def _btn(text, command, w=96):
            b = ctk.CTkButton(holder, text=text, width=w, height=28,
                              fg_color='#232323', hover_color='#333333', command=command)
            b.pack(side='right', padx=(6, 0))
            self._btns.append(b)
            return b

        if browse:
            texts = {'file': '选择程序…', 'dir': '选择目录…', 'img': '选择图片…'}

            def pick():
                if browse == 'file':
                    p = filedialog.askopenfilename(parent=self, title='选择程序',
                                                   filetypes=[('程序', '*.exe *.lnk *.bat'), ('所有文件', '*.*')])
                    if p:
                        p = os.path.normpath(p)
                        self._maybe_autofill(p)
                        self._maybe_auto_icon(p)
                elif browse == 'dir':
                    p = filedialog.askdirectory(parent=self, title='选择目录')
                else:
                    p = filedialog.askopenfilename(parent=self, title='选择封面',
                                                   filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp *.gif')])
                if p:
                    p = os.path.normpath(p)
                    if browse == 'img':
                        p = to_app_relative(p)   # 程序目录内的图存相对路径
                    e.delete(0, 'end')
                    e.insert(0, p)

            _btn(texts.get(browse, '浏览…'), pick)
        if extra:
            _btn(extra[0], extra[1], w=110)
        e.pack(side='left', fill='x', expand=True)
        frm.grid_columnconfigure(1, weight=1)
        return e

    def _on_mode(self, value):
        tips = {
            '时间戳': '两端各自保留最新，不删除多余文件（最安全，推荐）',
            '镜像': '两端完全一致，会删除多余文件（有会话标记保护）',
            '归档': '网络端保存 zip 快照（按份数保留），启动时恢复最新',
        }
        self.lbl_mode_tip.configure(text=tips.get(value, ''))

    # ---- 图标 → 封面 ----
    def _covers_dir(self):
        # 固定在程序目录下（与 云档.toml/云档.log 同级）
        return os.path.join(engine.APP_DIR, 'covers')

    def _icon_path_for(self, exe):
        h = hashlib.md5(str(exe).lower().encode('utf-8', 'replace')).hexdigest()[:10]
        return os.path.join(self._covers_dir(), f'icon_{h}.png')

    def _maybe_autofill(self, exe_path):
        """选择程序后自动填充：名称/等待进程为空时填入程序名，已有内容则跳过该栏"""
        base = os.path.basename(exe_path)
        stem = os.path.splitext(base)[0]
        if not self.e_name.get().strip():
            self.e_name.delete(0, 'end')
            self.e_name.insert(0, stem)
        if exe_path.lower().endswith('.exe') and not self.e_process.get().strip():
            self.e_process.insert(0, base)

    def _set_cover_entry(self, abs_path):
        self.e_cover.delete(0, 'end')
        self.e_cover.insert(0, to_app_relative(abs_path))

    def _maybe_auto_icon(self, exe_path):
        """选择程序后：若封面为空且是 exe/lnk，自动提取图标作封面（尽力而为，不弹窗打扰）"""
        if self.e_cover.get().strip():
            return
        if not exe_path.lower().endswith(('.exe', '.lnk')):
            return
        out = self._icon_path_for(exe_path)
        if extract_exe_icon(exe_path, out):
            self._set_cover_entry(out)

    def _make_icon_from_exe(self):
        exe = self.e_exe.get().strip().strip('"')
        if not exe or not os.path.isfile(exe):
            messagebox.showwarning('生成图标', '请先填写程序路径（选择程序或手动输入）。', parent=self)
            return
        out = self._icon_path_for(exe)
        if extract_exe_icon(exe, out):
            self._set_cover_entry(out)
        else:
            messagebox.showinfo('生成图标', '未能从该程序提取图标（可能没有内嵌图标资源）。\n可以手动选择一张图片作为封面。', parent=self)

    def _add_pair(self, loc, net):
        """一行布局：本地栏 | 按钮 | 网络栏 | 按钮 | 删除按钮"""
        row = ctk.CTkFrame(self.pairs_frame, fg_color='transparent')
        row.pack(fill='x', pady=3)

        def pick(entry, title):
            p = filedialog.askdirectory(parent=self, title=title)
            if p:
                entry.delete(0, 'end')
                entry.insert(0, os.path.normpath(p))

        def _btn(text, command, color='#232323', hover='#333333'):
            b = ctk.CTkButton(row, text=text, width=30, height=28,
                              fg_color=color, hover_color=hover, command=command)
            b.pack(side='left', padx=(4, 0))
            self._btns.append(b)

        e_local = ctk.CTkEntry(row, placeholder_text='本地目录', font=ctk.CTkFont(FONT, 12))
        e_local.pack(side='left', fill='x', expand=True)
        e_local.insert(0, loc)
        _btn('…', lambda: pick(e_local, '选择本地目录'))
        e_net = ctk.CTkEntry(row, placeholder_text='网络目录（\\\\NAS\\共享\\…）', font=ctk.CTkFont(FONT, 12))
        e_net.pack(side='left', padx=(4, 0), fill='x', expand=True)
        e_net.insert(0, net)
        _btn('…', lambda: pick(e_net, '选择网络目录（可直接输入 UNC 路径）'))
        _btn('✕', lambda: self._del_pair(row), color='#3a2323', hover=C_RED)
        self.pair_rows.append({'local': e_local, 'net': e_net, 'frame': row})

    def _del_pair(self, row):
        for i, pr in enumerate(self.pair_rows):
            if pr['frame'] is row:
                row.destroy()
                self.pair_rows.pop(i)
                return

    def on_save(self):
        name = self.e_name.get().strip()
        if not name:
            messagebox.showwarning('校验', '游戏名称不能为空。', parent=self)
            return
        pairs = []
        for pr in self.pair_rows:
            loc, net = engine.norm_dir(pr['local'].get()), engine.norm_dir(pr['net'].get())
            if loc or net:
                pairs.append({'local': loc, 'net': net})
        if not pairs or any(not p['local'] or not p['net'] for p in pairs):
            messagebox.showwarning('校验', '至少需要一个完整的目录对（本地与网络目录都要填写）。', parent=self)
            return
        try:
            keep = int(self.e_keep.get().strip() or '10')
        except ValueError:
            messagebox.showwarning('校验', '备份保留份数必须是数字。', parent=self)
            return
        # 与展示侧对称：显示用“、”连接，解析就必须同时接受 , ， 、 三种分隔
        # （旧版只按逗号拆，编辑保存一次就会把多进程合成单个带顿号的字符串，
        #   等待进程判定从此失效——启动等待超时/预检失效）
        procs = [x.strip() for x in re.split(r'[,，、]+', self.e_process.get()) if x.strip()]
        gd = {
            'name': name,
            # 编辑时沿用原隐藏ID（统计归属不变）；新增时留空，由 apply_game 补发
            'id': (self.game or {}).get('id', ''),
            'exe': self.e_exe.get().strip().strip('"'),
            'args': self.e_args.get().strip(),
            'workdir': self.e_workdir.get().strip(),
            'process': procs,
            'mode': LABEL2MODE.get(self.seg_mode.get(), 'timestamp'),
            'exclude': [l.strip() for l in self.txt_exclude.get('1.0', 'end').splitlines() if l.strip()],
            'backup_keep': max(0, keep),
            'cover': self.e_cover.get().strip().strip('"'),
            'sync': pairs,
        }
        if self.app.apply_game(gd, self.index):
            self.destroy()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('全局设置')
        self.geometry('560x232')   # 高度按内容定死：3行设置+快捷入口+按钮，无多余空白
        self.resizable(False, False)
        self.configure(fg_color='#181818')
        self.transient(app)
        self.withdraw()   # 先隐藏

        frm = ctk.CTkFrame(self, fg_color='transparent')
        frm.pack(fill='both', expand=True, padx=18, pady=14)
        cfg = app.cfg or {}

        def row(r, label):
            ctk.CTkLabel(frm, text=label, font=ctk.CTkFont(FONT, 13), text_color='#9a9a9a').grid(
                row=r, column=0, sticky='w', pady=(10, 0))
            e = ctk.CTkEntry(frm, width=300, font=ctk.CTkFont(FONT, 12))
            e.grid(row=r, column=1, sticky='ew', pady=(10, 0))
            return e

        self.e_keep = row(0, '推送前备份保留份数（0=不清理）')
        self.e_keep.insert(0, str(cfg.get('backup_keep', 10)))
        self.e_timeout = row(1, '等待游戏进程出现的超时（秒）')
        self.e_timeout.insert(0, str(cfg.get('timeout_launch', 120)))
        self.e_netwait = row(2, '网络目录不可达时的等待（秒）')
        self.e_netwait.insert(0, str(cfg.get('net_wait', 60)))
        frm.grid_columnconfigure(1, weight=1)

        links = ctk.CTkFrame(frm, fg_color='transparent')
        links.grid(row=3, column=0, columnspan=3, sticky='w', pady=(12, 0))
        ctk.CTkButton(links, text='打开配置文件（高级编辑）', width=190, height=30,
                      fg_color='#232323', hover_color='#333333',
                      command=lambda: os.startfile(self.app.cfg_path)).pack(side='left', padx=(0, 8))
        ctk.CTkButton(links, text='打开日志文件夹', width=140, height=30,
                      fg_color='#232323', hover_color='#333333',
                      command=lambda: os.startfile(engine.APP_DIR)).pack(side='left')

        btns = ctk.CTkFrame(frm, fg_color='transparent')
        btns.grid(row=4, column=0, columnspan=3, sticky='e', pady=(12, 0))
        self.btn_cancel = ctk.CTkButton(btns, text='取消', width=90, fg_color='#2a2a2a', hover_color='#3a3a3a',
                                        command=self.destroy)
        self.btn_cancel.pack(side='left', padx=6)
        self.btn_save = ctk.CTkButton(btns, text='保存', width=110, fg_color=C_GREEN, hover_color=C_GREEN_H,
                                      command=self.on_save)
        self.btn_save.pack(side='left')
        show_dialog_robust(self, app, size_pt=(560, 232))   # 构建完成后再安全显示

    def on_save(self):
        try:
            keep = max(0, int(self.e_keep.get().strip() or '10'))
            timeout = max(5, int(self.e_timeout.get().strip() or '120'))
            netwait = max(0, int(self.e_netwait.get().strip() or '60'))
        except ValueError:
            messagebox.showwarning('校验', '数值项必须是整数。', parent=self)
            return
        if self.app.apply_general(keep, timeout, netwait):
            self.destroy()


class StatsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.title('游玩时长统计')
        self.geometry('520x380')
        self.resizable(False, False)
        self.configure(fg_color='#181818')
        self.transient(app)
        self.withdraw()   # 先隐藏

        if app.cfg:
            name_to_id, id_to_name = engine.stats_maps(app.cfg)
            s = engine.stats_summary(app.cfg['stats_csv'], name_to_id or None, id_to_name or None)
        else:
            s = {'week_start': None, 'totals': {}, 'week': {}, 'names': {}}
        txt = ctk.CTkTextbox(self, font=ctk.CTkFont('Consolas', 13), fg_color='#0d0d0d')
        txt.pack(fill='both', expand=True, padx=12, pady=12)
        lines = [f'{"游戏":<14}{"累计(小时)":>12}{"本周(小时)":>12}', '-' * 40]
        gt = gw = 0.0
        for key in sorted(s['totals'], key=lambda k: s['names'].get(k, k)):
            w = s['week'].get(key, 0.0)
            gt += s['totals'][key]
            gw += w
            lines.append(f'{s["names"].get(key, "?"):<14}{s["totals"][key] / 60:>12.1f}{w / 60:>12.1f}')
        if s['totals']:
            lines.append('-' * 40)
            lines.append(f'{"合计":<14}{gt / 60:>12.1f}{gw / 60:>12.1f}')
        if s['week_start']:
            lines.append('')
            lines.append(f'本周自 {s["week_start"].isoformat()} 起')
        if not s['totals']:
            lines.append('暂无记录。')
        elif app.cfg:
            lines.append('')
            lines.append('（按游戏隐藏ID归属，改名不影响统计）')
        txt.insert('1.0', '\n'.join(lines))
        txt.configure(state='disabled')
        show_dialog_robust(self, app, size_pt=(520, 380))   # 构建完成后再安全显示


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')

    ap = argparse.ArgumentParser(prog='云档启动器', description='游戏存档同步/备份 启动器')
    ap.add_argument('-c', '--config', help='配置文件路径（默认: 本程序同目录 云档.toml）')
    ap.add_argument('--smoke', type=int, default=0, help='自检：启动后自动退出（毫秒）')
    ap.add_argument('--shot', help='配合 --smoke：退出前截图保存到该路径')
    args = ap.parse_args()

    cpath = args.config or engine.default_config_path()
    cfg = None
    startup_error = None
    if os.path.isfile(cpath):
        try:
            cfg = engine.load_config(cpath)
        except engine.SessionError as e:
            startup_error = e.msg
    # 一次性迁移：给游戏补发隐藏ID，并把旧统计 CSV 升级为带 ID 格式（改名不丢统计）
    if cfg is not None and cfg['games']:
        changed = engine.ensure_game_ids(cfg)
        changed = engine.migrate_stats_ids(cfg['stats_csv'], cfg) or changed
        if changed:
            try:
                engine.save_toml(cpath, cfg)
            except OSError:
                pass
    try:
        engine.local_guard()
    except engine.SessionError as e:
        print(e.msg)
        if os.name == 'nt':
            # 窗口版没有控制台，用系统弹窗提示，避免“双击后没反应”的困惑
            ctypes.windll.user32.MessageBoxW(None, e.msg, '云档启动器', 0x10)
        return 1

    app = App(cpath, cfg, startup_error=startup_error, smoke_ms=args.smoke, shot_path=args.shot)
    app.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
