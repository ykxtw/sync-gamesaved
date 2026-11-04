# -*- coding: utf-8 -*-
"""回归：空配置/删光游戏后可正常启动；校验失败弹错而非静默；stdin=None 不崩"""
import os
import subprocess
import sys

BASE = r'C:\Users\HC\.zcode\workspace\c7d3d2f8-915f-4575-870f-7549bedc0c96\云档'
sys.path.insert(0, BASE)
import 云档 as engine  # noqa: E402

EMPTY = os.path.join(BASE, 'test', 'empty.toml')
results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


# 1) 空游戏库 = 合法配置（删光所有游戏后重启的场景）
with open(EMPTY, 'w', encoding='utf-8') as f:
    f.write('[general]\nnet_wait = 5\n')
cfg = engine.load_config(EMPTY)
check('1 空配置 load_config 不报错', cfg is not None and cfg['games'] == [])
try:
    engine.validate_config(cfg)
    check('2 validate_config 空库不抛', True)
except Exception as e:
    check('2 validate_config 空库不抛', False, repr(e))

# 3) windowed exe 场景：sys.stdin 为 None 时 pause_if_needed 不崩
old_stdin = sys.stdin
try:
    sys.stdin = None
    engine.NO_PAUSE = False
    engine.pause_if_needed()
    check('3 stdin=None 时不崩', True)
except Exception as e:
    check('3 stdin=None 时不崩', False, repr(e))
finally:
    sys.stdin = old_stdin

# 4) 校验失败必须抛 SessionError（GUI 能弹窗），绝不能 sys.exit 被 Tk 吞掉
bad = engine.normalize_config({'game': [{'name': '坏', 'exe': '', 'mode': 'timestamp',
                                         'sync': [
                                             {'local': r'C:\a', 'net': r'\\n\s1'},
                                             {'local': r'C:\a\sub', 'net': r'\\n\s1\sub'}]}]})
try:
    engine.validate_config(bad)
    check('4 非法目录对抛 SessionError', False, '竟然没抛')
except engine.SessionError:
    check('4 非法目录对抛 SessionError', True)
except SystemExit as e:
    check('4 非法目录对抛 SessionError', False, f'仍是 sys.exit({e.code})')

# 5) CLI：空配置直接会话 → 退出码 3 且有中文提示（不能崩、不能静默）
r = subprocess.run([sys.executable, '-I', os.path.join(BASE, '云档.py'),
                    '--config', EMPTY, '--no-pause'],
                   capture_output=True, cwd=BASE, timeout=60)
out = (r.stdout or b'').decode('utf-8', 'replace')
check('5 CLI 空配置会话=rc3+提示', r.returncode == 3 and '还没有游戏' in out, f'rc={r.returncode}')

# 6) CLI：--list 空配置也正常
r = subprocess.run([sys.executable, '-I', os.path.join(BASE, '云档.py'),
                    '--list', '--config', EMPTY, '--no-pause'],
                   capture_output=True, cwd=BASE, timeout=60)
check('6 CLI --list 空配置正常', r.returncode == 0, f'rc={r.returncode}')

# 7) GUI 脚本：空配置烟雾启动（空状态界面）
r = subprocess.run([sys.executable, '-I', os.path.join(BASE, '云档GUI.py'),
                    '--config', EMPTY, '--smoke', '2500'],
                   capture_output=True, cwd=BASE, timeout=60)
check('7 GUI 空配置烟雾启动', r.returncode == 0, f'rc={r.returncode}')

# 8) GUI windowed exe：空配置烟雾启动（stdin=None 的真实场景）
EXE = os.path.join(BASE, 'dist', '云档.exe')
if os.path.isfile(EXE):
    r = subprocess.run([EXE, '--config', EMPTY, '--smoke', '2500'], capture_output=True, timeout=60)
    check('8 GUI exe 空配置启动', r.returncode == 0, f'rc={r.returncode}')
else:
    print('（跳过 8：exe 未构建）')
    results.append(True)

# 9) 主界面封面不残留：选中→取消选择→再选（删除游戏后图标残留的场景）
import 云档GUI as gui  # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo.toml')


def _has_img(lbl):
    try:
        return bool(lbl.cget('image'))
    except Exception:
        return getattr(lbl, '_image', None) is not None


dcfg = engine.load_config(DEMO)
app = gui.App(DEMO, dcfg)
r2 = {}


def _cover_steps():
    app.selected = 0
    app._refresh_detail()
    r2['set'] = _has_img(app.lbl_cover)
    app.selected = None
    app._refresh_detail()
    r2['cleared'] = not _has_img(app.lbl_cover)
    app.selected = 0
    app._refresh_detail()
    r2['again'] = _has_img(app.lbl_cover)
    app.destroy()


app.after(800, _cover_steps)
app.mainloop()
check('9 封面选中时显示', r2.get('set'))
check('9 取消选择不残留', r2.get('cleared'))
check('9 再选恢复显示', r2.get('again'))

os.remove(EMPTY)
print('====', 'ALL PASS' if all(results) else f'FAILED {results}', '====')
sys.exit(0 if all(results) else 1)
