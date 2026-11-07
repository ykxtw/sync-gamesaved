# -*- coding: utf-8 -*-
"""配置回滚回归：编辑/全局设置保存失败时必须整体回滚内存配置。

旧版缺陷：apply_game 编辑校验失败后不回滚，非法游戏残留在内存 cfg 里，
之后任何「全局设置/编辑保存」都会再次校验失败，只能重启程序恢复。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import 云档 as engine  # noqa: E402
import 云档GUI as gui  # noqa: E402

TOML = os.path.join(BASE, 'test', 'test_rollback.toml')
results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


def mkgame(name, local, net):
    return {'name': name, 'id': '', 'exe': '', 'args': '', 'workdir': '', 'process': [],
            'mode': 'timestamp', 'exclude': [], 'backup_keep': 3, 'cover': '',
            'nosync': False, 'sync': [{'local': local, 'net': net}]}


# 嵌套检测按平台原生分隔符构造（Windows 反斜杠在 Linux 上不是分隔符）
import tempfile
_RB = tempfile.mkdtemp(prefix='yd_rb_')
SAVE_A = os.path.join(_RB, 'save_a')
NET_A = os.path.join(_RB, 'net_a')
SAVE_B = os.path.join(_RB, 'save_b')

cfg = engine.normalize_config({'general': {'backup_keep': 10, 'timeout_launch': 120, 'net_wait': 20},
                               'game': [mkgame('甲', SAVE_A, NET_A)]})

# 校验失败会弹 showerror——测试中静默
_real_showerror = gui.messagebox.showerror
gui.messagebox.showerror = lambda *a, **k: None

app = gui.App(TOML, cfg)
app.withdraw()   # 测试纯逻辑，不闪窗口

# ---------- 1) 编辑保存校验失败 → 回滚 ----------
bad = mkgame('坏配置', SAVE_A, os.path.join(SAVE_A, 'nested'))   # 本地/网络嵌套 → 校验失败
ok = app.apply_game(bad, 0)
check('1 非法编辑返回 False', ok is False)
check('2 游戏数量不变', len(app.cfg['games']) == 1, f"n={len(app.cfg['games'])}")
check('3 原游戏未被污染', app.cfg['games'][0]['name'] == '甲'
      and app.cfg['games'][0]['sync'][0]['net'] == NET_A)

# ---------- 2) 旧版缺陷复现口：失败后再改全局设置必须能成功 ----------
ok = app.apply_general(11, 130, 65)
check('4 失败后全局设置仍可保存', ok is True)
check('5 全局设置已生效', app.cfg['backup_keep'] == 11 and app.cfg['net_wait'] == 65)
check('6 配置已写盘', 'net_wait = 65' in open(TOML, encoding='utf-8').read())

# ---------- 3) 新增游戏校验失败 → 回滚（不残留半条） ----------
bad2 = mkgame('新增坏配置', SAVE_B, os.path.join(SAVE_B, 'nested'))
ok = app.apply_game(bad2, None)
check('7 非法新增返回 False', ok is False)
check('8 新增失败不留残留', len(app.cfg['games']) == 1, f"n={len(app.cfg['games'])}")

# ---------- 4) 写盘失败 → 回滚 ----------
snap = app.cfg['games'][0]['name']
app._save_cfg = lambda: False
good = mkgame('改写', SAVE_A, NET_A)
ok = app.apply_game(good, 0)
check('9 写盘失败返回 False', ok is False)
check('10 写盘失败已回滚', app.cfg['games'][0]['name'] == snap)
ok = app.apply_general(99, 999, 999)
check('11 设置写盘失败返回 False', ok is False)
check('12 设置写盘失败已回滚', app.cfg['backup_keep'] == 11 and app.cfg['timeout_launch'] == 130)
app._save_cfg = lambda: engine.save_toml(TOML, app.cfg) or True

# ---------- 5) 正常编辑仍可用 ----------
ok = app.apply_game(mkgame('甲改', SAVE_A, NET_A), 0)
check('13 回滚后正常编辑可用', ok is True and app.cfg['games'][0]['name'] == '甲改')

gui.messagebox.showerror = _real_showerror
app.destroy()

print(f'\n==== 结果: {sum(results)}/{len(results)} 通过 ====')
sys.exit(0 if all(results) else 1)
