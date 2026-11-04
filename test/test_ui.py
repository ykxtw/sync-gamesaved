# -*- coding: utf-8 -*-
"""UI 回归：居中（主窗/编辑/设置）、设置窗高度、仅下载/仅上传（GUI+CLI）"""
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'test'))
import 云档 as engine  # noqa: E402
import 云档GUI as gui  # noqa: E402
import make_fixture as mf  # noqa: E402

results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


LOC, NET = os.path.join(BASE, 'test', 'loc'), os.path.join(BASE, 'test', 'net')
DEMO = os.path.join(BASE, 'test', 'demo.toml')
os.makedirs(LOC, exist_ok=True)
os.makedirs(NET, exist_ok=True)

# ---------- 1~3 居中 + 设置窗高度 + 启动不闪现 ----------
cfg = engine.load_config(DEMO)
app = gui.App(DEMO, cfg)
r = {'app_init_withdrawn': app.state() == 'withdrawn'}
T0 = time.time()


def step_center():
    r['app_normal'] = app.state() == 'normal'
    scale = app._get_window_scaling()
    sw = app.winfo_screenwidth() * scale
    sh = app.winfo_screenheight() * scale
    r['app_dx'] = abs(app.winfo_rootx() - (sw - app.winfo_width()) // 2)
    r['app_dy'] = abs(app.winfo_rooty() - (sh - app.winfo_height()) // 2)
    r['gap_btn_log'] = app.lbl_log.winfo_rooty() - (app.btn_play.winfo_rooty() + app.btn_play.winfo_height())
    r['log_h'] = app.txt_log.winfo_height()
    ed = gui.GameEditor(app, None)
    r['ed_init_withdrawn'] = ed.state() == 'withdrawn'
    app.after(900, lambda: check_editor(ed))


def check_editor(ed):
    ed.update_idletasks()
    ed.update()
    r['ed_normal'] = ed.state() == 'normal'
    r['ed_dx'] = abs(ed.winfo_rootx() - (app.winfo_rootx() + (app.winfo_width() - ed.winfo_width()) // 2))
    r['ed_dy'] = abs(ed.winfo_rooty() - (app.winfo_rooty() + (app.winfo_height() - ed.winfo_height()) // 2))
    ed.destroy()
    st = gui.SettingsDialog(app)
    r['st_init_withdrawn'] = st.state() == 'withdrawn'
    app.after(900, lambda: check_settings(st))


def check_settings(st):
    st.update_idletasks()
    st.update()
    r['st_normal'] = st.state() == 'normal'
    r['st_dx'] = abs(st.winfo_rootx() - (app.winfo_rootx() + (app.winfo_width() - st.winfo_width()) // 2))
    r['st_h'] = st.winfo_height()
    r['st_gap'] = (st.winfo_rooty() + st.winfo_height()) - \
                  (st.btn_cancel.winfo_rooty() + st.btn_cancel.winfo_height())
    st.destroy()
    app.after(200, run_down)


def run_down():
    app.select_game(0)          # 阿尔法（exe=ping，但 down/up 不会启动游戏）
    r['mark_down'] = app.txt_log.index('end-1c')
    app.btn_down.invoke()
    app.after(400, lambda: watch('down'))


def run_up():
    r['mark_up'] = app.txt_log.index('end-1c')
    app.btn_up.invoke()
    app.after(400, lambda: watch('up'))


def watch(which):
    if not app.running:
        r[which] = app.txt_log.get(r['mark_' + which], 'end')
        if which == 'down':
            app.after(200, run_up)
        else:
            app.destroy()
        return
    if time.time() - T0 > 60:
        app.destroy()
        return
    app.after(400, lambda: watch(which))


app.after(700, step_center)
app.mainloop()

check('1 主窗屏幕居中', r['app_dx'] <= 80 and r['app_dy'] <= 80,
      f"dx={r['app_dx']:.0f} dy={r['app_dy']:.0f}")
check('1b 主窗先隐藏后显示（不闪现）', r['app_init_withdrawn'] and r['app_normal'])
check('2 编辑器跟随主窗居中', r['ed_dx'] <= 90 and r['ed_dy'] <= 90,
      f"dx={r['ed_dx']:.0f} dy={r['ed_dy']:.0f}")
check('2b 编辑器先隐藏后显示', r['ed_init_withdrawn'] and r['ed_normal'])
check('3a 设置窗跟随主窗居中', r['st_dx'] <= 90, f"dx={r['st_dx']:.0f}")
check('3b 设置窗高度收紧', r['st_h'] <= 395, f"h={r['st_h']:.0f}px")
check('3c 设置窗底部空余小', r['st_gap'] <= 45, f"gap={r['st_gap']:.0f}px")
check('3d 设置窗先隐藏后显示', r['st_init_withdrawn'] and r['st_normal'])
cond4a = ('---- 恢复：网络 → 本地 ----' in r['down']
          and '---- 推送：本地 → 网络 ----' not in r['down'] and '全部操作完成' in r['down'])
check('4a GUI 仅下载：有恢复无推送', cond4a,
      '' if cond4a else repr(r['down'][-500:]))
cond4b = ('---- 推送：本地 → 网络 ----' in r['up']
          and '---- 恢复：网络 → 本地 ----' not in r['up'] and '全部操作完成' in r['up'])
check('4b GUI 仅上传：有推送无恢复', cond4b,
      '' if cond4b else repr(r['up'][-500:]))
check('5 按钮行到日志标题紧凑', r['gap_btn_log'] <= 40,
      f"gap={r['gap_btn_log']:.0f}px")
check('6 日志框足够大', r['log_h'] >= 240,
      f"h={r['log_h']:.0f}px")

# ---------- 4c/4d CLI 方向化同步的文件语义 ----------
def fresh_fixture():
    mf.clean()
    mf.w(os.path.join(mf.NET, 'a.txt'), 'net-newer\n', +100)
    mf.w(os.path.join(mf.LOC, 'a.txt'), 'loc-old\n', -100)
    mf.w(os.path.join(mf.LOC, 'b.txt'), 'loc-newer\n', +100)
    mf.w(os.path.join(mf.NET, 'b.txt'), 'net-old\n', -100)
    mf.w(os.path.join(mf.NET, 'netonly.txt'), 'netonly\n')
    mf.w(os.path.join(mf.LOC, 'loconly.txt'), 'loconly\n')


def cli(*args):
    return subprocess.run([sys.executable, '-I', os.path.join(BASE, '云档.py'),
                           '--config', os.path.join(BASE, 'test', 'test_t1.toml'), *args, '--no-pause'],
                          capture_output=True, cwd=BASE, timeout=120)


def rf(p):
    return open(p, encoding='utf-8').read()


T1 = ['--config', os.path.join(BASE, 'test', 'test_t1.toml')]
fresh_fixture()
rc = cli('--down').returncode
check('4c CLI --down 文件语义', rc == 0
      and rf(os.path.join(mf.LOC, 'a.txt')) == 'net-newer\n'
      and os.path.isfile(os.path.join(mf.LOC, 'netonly.txt'))
      and rf(os.path.join(mf.NET, 'b.txt')) == 'net-old\n'
      and not os.path.isfile(os.path.join(mf.NET, 'loconly.txt')), f'rc={rc}')

fresh_fixture()
rc = cli('--up').returncode
check('4d CLI --up 文件语义', rc == 0
      and rf(os.path.join(mf.NET, 'b.txt')) == 'loc-newer\n'
      and os.path.isfile(os.path.join(mf.NET, 'loconly.txt'))
      and rf(os.path.join(mf.LOC, 'a.txt')) == 'loc-old\n'
      and not os.path.isfile(os.path.join(mf.LOC, 'netonly.txt'))
      and os.path.isdir(os.path.join(mf.NET, '云档备份')), f'rc={rc}')

rc = cli('--down', '--up').returncode
check('4e --down/--up 互斥', rc == 3, f'rc={rc}')

print('====', 'ALL PASS' if all(results) else f'FAILED {results}', '====')
sys.exit(0 if all(results) else 1)
