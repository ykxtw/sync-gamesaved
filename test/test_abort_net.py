# -*- coding: utf-8 -*-
"""中止响应回归：网络目录不可达 / 等待游戏进程 / robocopy 重试期间，
「中止会话」与「强制退出」必须即时生效（旧版要等满 net_wait/timeout_launch 秒）。

分两部分：
  引擎级（1~3）：直接调用引擎函数，量测中止延迟；
  GUI 级（4~6） ：真实窗口 + 会话线程，覆盖「中止会话」按钮与关闭→强制退出路径。
"""
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import 云档 as engine  # noqa: E402

results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


NET_WAIT = 20          # 故意设长：有 bug 时会等满 20 秒
TOML = os.path.join(BASE, 'test', 'test_abort_net.toml')
LOC = os.path.join(BASE, 'test', 'loc')
NET_MISS = os.path.join(BASE, 'test', 'net_missing_abort')
os.makedirs(LOC, exist_ok=True)
if os.path.isdir(NET_MISS):
    import shutil
    shutil.rmtree(NET_MISS, ignore_errors=True)

with open(TOML, 'w', encoding='utf-8') as f:
    f.write(f"""[general]
backup_keep = 3
timeout_launch = 30
net_wait = {NET_WAIT}

[[game]]
name = '中止测试'
exe = ''
mode = 'timestamp'
sync = [
  {{ local = '{LOC}', net = '{NET_MISS}' }},
]
""")

cfg = engine.load_config(TOML)
game = cfg['games'][0]


def abort_after(sec, control):
    def _t():
        time.sleep(sec)
        control.abort.set()
    threading.Thread(target=_t, daemon=True).start()


# ---------- 1) 预检网络等待期间中止 ----------
control = engine.SessionControl()
abort_after(1.0, control)
t0 = time.time()
code, msg = engine.EXIT_OK, ''
try:
    engine.run_session(cfg, game, sync_only=True, control=control)
except engine.SessionError as e:
    code, msg = e.code, e.msg
el1 = time.time() - t0
check('1 预检网络等待中中止→立即退出(≤3s)', el1 < 3.0, f'elapsed={el1:.1f}s')
check('2 退出码=5(EXIT_ABORT)', code == engine.EXIT_ABORT, f'code={code} msg={msg}')

# ---------- 2) 等待游戏进程期间中止 ----------
control2 = engine.SessionControl()
engine._ACTIVE.control = control2
abort_after(1.0, control2)
t0 = time.time()
res2 = 'returned'
try:
    engine.wait_for_start(['no_such_process_zzz.exe'], 30)
    res2 = 'returned'
except engine.SessionError:
    res2 = 'aborted'
el2 = time.time() - t0
engine._ACTIVE.control = None
check('3 等待启动中中止→立即退出(≤4s)', res2 == 'aborted' and el2 < 4.0, f'res={res2} elapsed={el2:.1f}s')

# ---------- 3) robocopy 重试间隔中止（源目录不存在→任何平台 rc=16） ----------
control3 = engine.SessionControl()
engine._ACTIVE.control = control3
abort_after(2.0, control3)
t0 = time.time()
res3 = 'returned'
try:
    engine.robocopy_run(os.path.join(BASE, 'test', 'no_such_src_dir'), NET_MISS,
                         'timestamp', engine.Exclude([]))
    res3 = 'returned'
except engine.SessionError:
    res3 = 'aborted'
el3 = time.time() - t0
engine._ACTIVE.control = None
check('4 robocopy重试间隔中止→立即打断(≤5s)', res3 == 'aborted' and el3 < 5.0, f'res={res3} elapsed={el3:.1f}s')

# ---------- 4~6) GUI 级 ----------
import 云档GUI as gui  # noqa: E402

app = gui.App(TOML, engine.load_config(TOML))
timing = {}


def s_start():  # 场景A：仅同步会话 → 预检网络等待 → 点「中止会话」
    app.select_game(0)
    app._confirm_abort = lambda name: True
    app.start_session(True)
    app.after(1200, s_abort)


def s_abort():
    timing['abort'] = time.time()
    app.on_abort()
    check('5 中止后立即反馈(正在中止…+按钮禁用)',
          app.lbl_status.cget('text') == '正在中止…'
          and str(app.btn_abort.cget('state')) == 'disabled')
    sess = app.sessions.get(str(app.cfg['games'][app.selected].get('id') or ''))
    check('6 中止信号已置位', sess is not None and sess.control.abort.is_set())
    app.after(100, s_watch_a)


def s_watch_a():
    if not app.running:
        timing['el_a'] = time.time() - timing['abort']
        check('7 网络等待中中止会话→会话快速结束(≤3s)', timing['el_a'] < 3.0,
              f"elapsed={timing['el_a']:.1f}s status={app.lbl_status.cget('text')!r}")
        check('8 状态显示已中止', '已中止' in app.lbl_status.cget('text'))
        app.after(600, s_start_b)
        return
    if time.time() - timing['abort'] > 40:
        check('7 网络等待中中止会话→会话快速结束(≤3s)', False, '超时未结束')
        app.destroy()
        return
    app.after(100, s_watch_a)


def s_start_b():  # 场景B：再次开始会话 → 关闭窗口 → 强制退出
    app._confirm_abort = None
    app._ask_close_choice = lambda mode: 'force'
    app.start_session(True)
    app.after(1200, s_force)


def s_force():
    timing['close'] = time.time()
    app.on_close()
    check('9 强制退出：pending+中止信号', app._pending_close
          and all(s2.control.abort.is_set() for s2 in app.sessions.values()))
    # 窗口销毁由 _poll 的 done→destroy 完成；销毁后 mainloop 返回，
    # 由模块级代码量测耗时（after 回调与 destroy 存在竞态，不在此轮询）


app.after(600, s_start)
app.after(60000, lambda: app.destroy() if app.winfo_exists() else None)
app.mainloop()

# ---------- 5) 强制退出 → 窗口快速销毁（主循环返回=已销毁） ----------
if 'close' in timing:
    el_b = time.time() - timing['close']
    check('10 强制退出→窗口快速销毁(≤4s)', el_b < 4.0, f'elapsed={el_b:.1f}s')
else:
    check('10 强制退出→窗口快速销毁(≤4s)', False, '场景B未执行')

print(f'\n==== 结果: {sum(results)}/{len(results)} 通过 ====')
sys.exit(0 if all(results) else 1)
