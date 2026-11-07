# -*- coding: utf-8 -*-
"""v2.5.1 启动方式（direct/shell）与直启句柄修复回归测试（平台无关）

覆盖：
  L1  _cfg_launch 容错：'shell'/'SHELL'/'direct'/垃圾值/缺省
  L2  normalize：launch 字段进 game dict（缺省 direct）
  L3  dump 落盘：shell 写出 launch = 'shell'，direct 不写该行
  L4  save→load 往返：launch 语义保持
  L5  launch_game direct：真启动子进程，返回 ('tree', pid) 且进程存在
  L6  launch_game shell：返回 ('names', [...])（POSIX 退化 DEVNULL 直启）
  L7  launch_game .lnk：自动走 Shell 分支（返回 names）
  L8  launch_game shell 失败（不存在的程序）：返回 'FAIL' 不抛异常
  L9  GUI to_raw 往返保留 launch 字段
  L10 编辑器含"启动方式"分段按钮（seg_launch 存在且两值）
  L11 详情面板 meta 含 启动：Shell（等价双击）
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import 云档 as e

ok = 0
fail = 0


def check(name, cond, detail=''):
    global ok, fail
    if cond:
        ok += 1
        print('PASS', name)
    else:
        fail += 1
        print('FAIL', name, detail)


# ---------------- L1 _cfg_launch 容错 ----------------
check('L1a shell 识别', e._cfg_launch('shell') == 'shell')
check('L1b 大小写不敏感', e._cfg_launch('Shell') == 'shell')
check('L1c direct', e._cfg_launch('direct') == 'direct')
check('L1d 缺省 direct', e._cfg_launch(None) == 'direct' and e._cfg_launch('') == 'direct')
check('L1e 垃圾值 direct', e._cfg_launch('rocket') == 'direct')

# ---------------- L2/L3/L4 配置往返 ----------------
raw = {'game': [
    {'name': '甲', 'exe': r'C:\G\a.exe', 'launch': 'shell',
     'sync': [{'local': r'C:\L\a', 'net': r'\\N\a'}]},
    {'name': '乙', 'exe': r'C:\G\b.exe',
     'sync': [{'local': r'C:\L\b', 'net': r'\\N\b'}]},
]}
cfg = e.normalize_config(raw)
check('L2a shell 进 game', cfg['games'][0]['launch'] == 'shell')
check('L2b 缺省 direct', cfg['games'][1]['launch'] == 'direct')

tmp = os.path.join(tempfile.mkdtemp(prefix='yd_l251_'), '云档.toml')
e.save_toml(tmp, cfg)
body = open(tmp, encoding='utf-8').read()
check('L3a shell 落盘', "launch = 'shell'" in body)
check('L3b direct 不写行', sum(1 for ln in body.splitlines()
      if ln.startswith('launch')) == 1)   # 按行首计数，避开 timeout_launch

cfg2 = e.load_config(tmp)   # load_config 已内置 normalize，不可再包一层
check('L4 往返语义保持', cfg2['games'][0]['launch'] == 'shell'
      and cfg2['games'][1]['launch'] == 'direct')

# ---------------- L5~L8 launch_game ----------------
import psutil

GAME_TPL = {'name': '测', 'args': '', 'workdir': '', 'process': [],
            'mode': 'timestamp', 'launch': 'direct'}


def run_launch(g):
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = e.launch_game(g, dry=False)
    return r, buf.getvalue()


# 平台自适应的“短命进程”：wine/Windows 用 ping（1 秒退），POSIX 用 sleep
if os.name == 'nt':
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\\windows'), 'system32', 'ping.exe')
    QUICK_ARGS = '-n 1 127.0.0.1'
else:
    exe = '/bin/sleep' if os.path.exists('/bin/sleep') else '/usr/bin/sleep'
    QUICK_ARGS = '0.4'
# L5 direct：真启动（POSIX 退化路径同样走 Popen+DEVNULL 修正后的代码）
g5 = dict(GAME_TPL, exe=exe, args=QUICK_ARGS)
r5, out5 = run_launch(g5)
check('L5a direct 返回 tree', isinstance(r5, tuple) and r5[0] == 'tree', repr(r5))
alive = False
try:
    p = psutil.Process(r5[1])
    alive = p.is_running()
except Exception:
    pass
check('L5b 进程真实存在', alive)
check('L5c 无 DETACHED 提示文案泄露', '资源管理器' not in out5)

# L6 shell 模式
g6 = dict(GAME_TPL, exe=exe, args=QUICK_ARGS, launch='shell')
r6, out6 = run_launch(g6)
check('L6a shell 返回 names', isinstance(r6, tuple) and r6[0] == 'names', repr(r6))
check('L6b 默认名=程序基名', r6[1] == [os.path.basename(exe)], repr(r6))
check('L6c 日志含 Shell 标注', 'Shell' in out6)

# L6d shell + 显式等待进程优先
g6d = dict(GAME_TPL, exe=exe, args=QUICK_ARGS, launch='shell', process=['mygame.exe'])
r6d, _ = run_launch(g6d)
check('L6d process 优先', r6d[1] == ['mygame.exe'], repr(r6d))

# L7 .lnk 强制 Shell（即使 launch 缺省）
g7 = dict(GAME_TPL, exe=r'C:\Games\游戏.lnk', process=['Game.exe'])
r7, out7 = run_launch(g7)
# POSIX 下 shell_launch 退化 Popen 对 Windows 路径会失败 → 走 FAIL 分支也合法，
# 但必须先进入 Shell 分支（日志有标注）
check('L7 lnk 走 Shell 分支', 'Shell' in out7)

# L8 shell 失败 → FAIL 不抛
g8 = dict(GAME_TPL, exe=os.path.join(os.path.dirname(exe), 'nonexistent_xx.exe'), launch='shell')
r8, out8 = run_launch(g8)
check('L8 失败返回 FAIL', r8 == 'FAIL', repr(r8))

# ---------------- L9~L11 GUI ----------------
# ---------------- L9~L11 GUI（源码文本检查；customtkinter 仅 wine/Windows 侧有） ----------------
try:
    gsrc = open(os.path.join(BASE, '云档GUI.py'), encoding='utf-8').read()
    check('L9 to_raw 保留 launch', "'cover', 'sync', 'launch'" in gsrc)
    check('L10 编辑器 seg_launch 控件', 'seg_launch' in gsrc
          and "values=['直接启动', 'Shell 启动']" in gsrc)
    check('L11 详情面板含启动方式', '启动：{lmode}' in gsrc and 'Shell（等价双击）' in gsrc)
    check('L11b 保存读取 seg_launch', "'launch': 'shell' if self.seg_launch.get()" in gsrc)
except Exception as ex:
    check('L9~L11 GUI 源码检查', False, repr(ex))

print(f'\n== 启动方式测试: {ok} 通过, {fail} 失败 ==')
sys.exit(1 if fail else 0)
