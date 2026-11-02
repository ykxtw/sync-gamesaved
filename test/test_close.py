# -*- coding: utf-8 -*-
"""关闭警告回归：游戏运行中误关的三选路径 + 同步中强制关闭"""
import os
import sys
import time

BASE = r'C:\Users\HC\.zcode\workspace\c7d3d2f8-915f-4575-870f-7549bedc0c96\云档'
sys.path.insert(0, BASE)
import 云档 as engine  # noqa: E402
import 云档GUI as gui  # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo.toml')
cfg = engine.load_config(DEMO)
app = gui.App(DEMO, cfg)
results = []
answers = {}
calls = {'ync': 0, 'yn': 0}
gui.messagebox.askyesnocancel = lambda *a, **k: (calls.__setitem__('ync', calls['ync'] + 1), answers.get('ync'))[1]
gui.messagebox.askyesno = lambda *a, **k: (calls.__setitem__('yn', calls['yn'] + 1), answers.get('yn'))[1]


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


def s1():  # 游戏运行中 + 取消
    app.running = True
    app.control = engine.SessionControl()
    app.launch_time = time.time()
    answers['ync'] = None
    app.on_close()
    check('1 游戏运行中选取消：窗口保留', app.winfo_exists() and not app._pending_close
          and calls['ync'] == 1, f"ync={calls['ync']}")
    app.after(200, s2)


def s2():  # 游戏运行中 + 最小化继续等待
    answers['ync'] = True
    app.on_close()
    ok = app.state() == 'iconic' and not app._pending_close and app.running
    app.deiconify()
    check('2 游戏运行中选最小化：会话继续', ok, f"state={app.state()}")
    app.after(200, s3)


def s3():  # 同步中 + 拒绝强制关闭
    app.launch_time = None          # 模拟“恢复/推送阶段”（无游戏运行）
    answers['yn'] = False
    app.on_close()
    check('3 同步中拒绝强制关闭：窗口保留', app.winfo_exists() and not app._pending_close
          and calls['yn'] == 1, f"yn={calls['yn']}")
    app.after(200, s4)


def s4():  # 同步中 + 强制关闭 → 中止信号置位 → 会话干净退出后窗口销毁
    answers['yn'] = True
    app.on_close()
    ok = app._pending_close and app.control.abort.is_set()
    check('4 同步中强制关闭：中止信号已置位', ok)
    # 模拟工作线程的 done 事件（引擎 finally 释放锁后发出）
    app.running = False
    app.q.put(('done', engine.EXIT_ABORT))
    app.after(600, s5)


def s5():
    destroyed = not app.winfo_exists()
    check('5 会话退出后窗口自动销毁', destroyed)
    if not destroyed:
        app.destroy()


app.after(600, s1)
app.after(4000, lambda: (app.destroy()) if app.winfo_exists() else None)
app.mainloop()

print('====', 'ALL PASS' if all(results) else f'FAILED {results}', '====')
sys.exit(0 if all(results) else 1)
