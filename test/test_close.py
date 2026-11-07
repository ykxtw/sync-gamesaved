# -*- coding: utf-8 -*-
"""关闭警告回归：深色警告窗（强制退出/最小化/取消）+ 三选路径 + 同步中强制关闭。
v2.5.0 起会话改为注册表模型：模拟会话用 make_sess() 注册 GameSession。"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import customtkinter as ctk  # noqa: E402

import 云档 as engine  # noqa: E402
import 云档GUI as gui  # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo.toml')
cfg = engine.load_config(DEMO)
app = gui.App(DEMO, cfg)
results = []
answers = {}
calls = {'game': 0, 'sync': 0}
captured = {}
iconified = {'n': 0}
real_ask = app._ask_close_choice
real_iconify = app.iconify


def count_iconify():
    iconified['n'] += 1
    real_iconify()


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


def ask(mode):
    """注入预设答案（逻辑层回归，不弹真实窗口）"""
    calls[mode] = calls.get(mode, 0) + 1
    return answers.get(mode)


def make_sess(gid, name, game_mode=True):
    """注册一个模拟会话（v2.5.0：on_close 遍历 sessions 注册表）"""
    s = gui.GameSession(gid, name, {'name': name, 'sync': []}, False, 'both')
    if game_mode:
        s.launch_time = time.time()
    app.sessions[gid] = s
    return s


app._ask_close_choice = ask


def find_buttons(win):
    out = []
    for w in win.winfo_children():
        if isinstance(w, ctk.CTkButton):
            out.append(w)
        else:
            out.extend(find_buttons(w))
    return out


def click_in(dlg, label):
    for b in find_buttons(dlg):
        if b.cget('text') == label:
            b.invoke()
            return True
    return False


def s1():  # 游戏运行中 + 取消
    app.sessions.clear()
    make_sess('g1', '测试游戏')
    answers['game'] = None
    app.on_close()
    check('1 游戏运行中选取消：窗口保留', app.winfo_exists() and not app._pending_close
          and calls['game'] == 1, f"game={calls['game']}")
    app.after(200, s2)


def s2():  # 游戏运行中 + 最小化继续等待
    answers['game'] = 'minimize'
    iconified['n'] = 0
    app.iconify = count_iconify
    app.on_close()
    ok = (app.state() == 'iconic' or iconified['n'] >= 1) and not app._pending_close and app.running
    app.deiconify()
    check('2 游戏运行中选最小化：会话继续', ok, f"state={app.state()} iconify={iconified['n']}")
    app.after(200, s3)


def s3():  # 同步中 + 拒绝强制关闭
    app.sessions['g1'].launch_time = None   # 模拟“恢复/推送阶段”（无游戏运行）
    answers['sync'] = None
    app.on_close()
    check('3 同步中选取消：窗口保留', app.winfo_exists() and not app._pending_close
          and calls['sync'] == 1, f"sync={calls['sync']}")
    app.after(200, s3b)


def s3b():  # v2.5.0 多会话：一个在游戏中 + 一个在同步 → 按 game 模式
    app.sessions.clear()
    make_sess('m1', '游戏一')
    make_sess('m2', '游戏二', game_mode=False)   # 同步中
    calls['game'] = 0
    answers['game'] = None
    app.on_close()
    check('3b 多会话任一游戏在运行→game模式', calls['game'] == 1 and app.winfo_exists())
    app.sessions.clear()
    app.after(200, s4)


def s4():  # 真实警告窗（游戏模式）：Esc = 取消
    app.sessions.clear()
    make_sess('g1', '测试游戏')
    app._ask_close_choice = real_ask

    def poke():
        dlg = app._close_dlg
        if dlg is None or not dlg.winfo_exists():
            check('4a 警告窗已打开', False, 'no dlg')
            return
        # wine 下窗口映射较慢：等对话框可见且按钮完成布局（最多 ~4 秒）
        btns = find_buttons(dlg)
        if dlg.state() != 'normal' or not btns or not all(b.winfo_rootx() > 0 for b in btns):
            app.after(200, poke)
            return
        captured['title'] = dlg.title()
        captured['state'] = dlg.state()
        captured['result'] = dlg.result
        labels = [b.cget('text') for b in find_buttons(dlg)]
        xs = {b.cget('text'): b.winfo_rootx() for b in find_buttons(dlg)}
        check('4a 真实警告窗可见且标题正确',
              captured['state'] == 'normal' and captured['title'] == '游戏正在运行',
              f"{captured}")
        check('4b 三按钮为 强制退出/最小化/取消 且视觉顺序正确',
              labels == ['取消', '最小化', '强制退出'] and
              xs['强制退出'] < xs['最小化'] < xs['取消'], f"{labels} {xs}")
        dlg.event_generate('<Escape>')

    app.after(700, poke)
    app.on_close()
    check('4c Esc=取消：窗口保留不中止', app.winfo_exists() and not app._pending_close
          and captured.get('result') is None, f"result={captured.get('result')}")
    app.after(200, s5)


def s5():  # 真实警告窗：点「最小化」按钮
    captured.clear()
    iconified['n'] = 0

    def poke():
        dlg = app._close_dlg
        if dlg is not None and dlg.winfo_exists():
            if not click_in(dlg, '最小化'):
                check('5 最小化按钮存在', False, 'btn not found')

    app.after(700, poke)
    app.on_close()
    ok = (app.state() == 'iconic' or iconified['n'] >= 1) and not app._pending_close and app.running
    app.deiconify()
    check('5 点击「最小化」：会话继续（窗口收到任务栏）', ok,
          f"state={app.state()} iconify={iconified['n']}")
    app.after(200, s6)


def s6():  # 真实警告窗（同步模式）：点「取消」
    app.sessions.clear()
    make_sess('g1', '测试游戏', game_mode=False)   # 同步中

    def poke():
        dlg = app._close_dlg
        if dlg is None or not dlg.winfo_exists():
            check('6 同步警告窗已打开', False, 'no dlg')
            return
        labels = [b.cget('text') for b in find_buttons(dlg)]
        check('6a 同步警告窗只有 强制退出/取消 两键',
              sorted(labels) == ['取消', '强制退出'] and dlg.title() == '正在同步', f"{labels}")
        click_in(dlg, '取消')

    app.after(700, poke)
    app.on_close()
    check('6b 同步中选取消：窗口保留', app.winfo_exists() and not app._pending_close)
    app.after(200, s7)


def s7():  # 真实警告窗（同步模式）：点「强制退出」→ 全部会话中止 → 干净退出后窗口销毁
    app.sessions.clear()
    make_sess('g1', '测试游戏', game_mode=False)
    make_sess('g2', '另一游戏', game_mode=False)   # v2.5.0：两个会话同时强制退出

    def poke():
        dlg = app._close_dlg
        if dlg is not None and dlg.winfo_exists():
            click_in(dlg, '强制退出')

    app.after(700, poke)
    app.on_close()
    ok = app._pending_close and all(s.control.abort.is_set() for s in app.sessions.values())
    check('7 强制退出：全部会话中止信号已置位', ok)
    # 模拟工作线程的 done 事件（引擎 finally 释放锁后发出，按游戏ID逐个结束）
    for gid in list(app.sessions):
        app.q.put(('done', engine.EXIT_ABORT, gid))
    app.after(600, s8)


def s8():
    destroyed = not app.winfo_exists()
    check('8 全部会话退出后窗口自动销毁', destroyed)
    if not destroyed:
        app.destroy()


app.after(600, s1)
app.after(25000, lambda: (app.destroy()) if app.winfo_exists() else None)
app.mainloop()

print('====', 'ALL PASS' if all(results) else f'FAILED {results}', '====')
sys.exit(0 if all(results) else 1)
