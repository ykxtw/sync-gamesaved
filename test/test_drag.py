# -*- coding: utf-8 -*-
"""v2.5.2 游戏卡片拖动排序回归测试

平台无关部分（任何环境可跑）：
  D1  版本号 2.5.2
  D2  源码含拖动排序关键实现（root 级事件绑定 / 按下轻量选中 / 实时换位 /
      落盘保存 / 边缘自动滚动 / 位移阈值防误拖）
  D3  引擎配置往返保序（拖动排序落盘的前提）

功能部分（需真实或虚拟显示：Windows 直接跑；Linux 需 DISPLAY，如 Xvfb）：
  T1  点击选中：按下→原地松开 → 选中切换、详情跟随、顺序不变、不写盘
  T2  拖动换位：卡甲依次拖过乙、丙中线以下 → 顺序 [乙,丙,甲,丁]
  T3  落盘持久：云档.toml 重读顺序一致；选中跟随被拖游戏（甲→下标2）
  T4  拖回原位：甲拖回列表顶部 → 顺序复原并写盘
  T5  运行徽标下可拖：带“同步中”徽标的卡片可拖动（会话按游戏ID索引）
  T6  单游戏拖拽：无换位目标，不抛异常、顺序不变
"""
import os
import sys
import tempfile
import time
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

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


# ---------------- 平台无关部分 ----------------
import 云档 as e  # noqa: E402

check('D1 版本号 2.5.2', e.VERSION == '2.5.2', e.VERSION)

gsrc = open(os.path.join(BASE, '云档GUI.py'), encoding='utf-8').read()
check('D2a root 级拖拽事件绑定',
      "self.bind('<B1-Motion>', self._on_card_drag)" in gsrc
      and "self.bind('<ButtonRelease-1>', self._on_card_drop)" in gsrc)
check('D2b 按下轻量选中（不重建卡片）', 'def _card_press' in gsrc and "'fresh': True" in gsrc)
check('D2c 实时换位 pack before/after', 'before=target' in gsrc and 'after=target' in gsrc)
check('D2d 落盘保存', '已保存新的游戏排序' in gsrc)
check('D2e 边缘自动滚动', 'yview_scroll' in gsrc and '_autoscroll_tick' in gsrc)
check('D2f 位移阈值防误拖（<6px 仍为点击）', 'moved' in gsrc and '< 6:' in gsrc)

raw = {'game': [
    {'name': '甲', 'exe': 'C:\\G\\a.exe', 'sync': [{'local': 'C:\\L\\a', 'net': 'C:\\N\\a'}]},
    {'name': '乙', 'exe': 'C:\\G\\b.exe', 'sync': [{'local': 'C:\\L\\b', 'net': 'C:\\N\\b'}]},
    {'name': '丙', 'exe': 'C:\\G\\c.exe', 'sync': [{'local': 'C:\\L\\c', 'net': 'C:\\N\\c'}]},
]}
cfg0 = e.normalize_config(raw)
check('D3a 引擎保序', [g['name'] for g in cfg0['games']] == ['甲', '乙', '丙'])

tmpdir = tempfile.mkdtemp(prefix='yd_drag_')
TOML = os.path.join(tmpdir, '云档.toml')
e.save_toml(TOML, cfg0)
back = e.load_config(TOML)
check('D3b 落盘往返保序', [g['name'] for g in back['games']] == ['甲', '乙', '丙'])

# ---------------- 功能部分（需显示环境） ----------------
can_gui = sys.platform == 'win32' or os.environ.get('DISPLAY')
if not can_gui:
    print('SKIP 功能测试（无 DISPLAY；Windows 或 Xvfb 环境下自动执行）')
    print(f'\n== 拖动排序测试: {ok} 通过, {fail} 失败 ==')
    sys.exit(1 if fail else 0)

import 云档GUI as gui  # noqa: E402

raw4 = {'general': {'backup_keep': 5, 'timeout_launch': 30, 'net_wait': 3}, 'game': [
    {'name': '甲', 'id': 'gidA', 'exe': 'C:\\G\\a.exe', 'mode': 'timestamp',
     'sync': [{'local': os.path.join(tmpdir, 'la'), 'net': os.path.join(tmpdir, 'na')}]},
    {'name': '乙', 'id': 'gidB', 'exe': 'C:\\G\\b.exe', 'mode': 'timestamp',
     'sync': [{'local': os.path.join(tmpdir, 'lb'), 'net': os.path.join(tmpdir, 'nb')}]},
    {'name': '丙', 'id': 'gidC', 'exe': 'C:\\G\\c.exe', 'mode': 'timestamp',
     'sync': [{'local': os.path.join(tmpdir, 'lc'), 'net': os.path.join(tmpdir, 'nc')}]},
    {'name': '丁', 'id': 'gidD', 'exe': 'C:\\G\\d.exe', 'mode': 'timestamp',
     'sync': [{'local': os.path.join(tmpdir, 'ld'), 'net': os.path.join(tmpdir, 'nd')}]},
]}
cfg = e.normalize_config(raw4)
cfg['stats_csv'] = os.path.join(tmpdir, 'stats.csv')
e.save_toml(TOML, cfg)
app = gui.App(TOML, cfg)


def wait_mapped(a, seconds=4):
    t0 = time.time()
    while not a.winfo_ismapped() and time.time() - t0 < seconds:
        a.update()
        time.sleep(0.05)
    a.update()
    a.update_idletasks()


def _gen(seq, sx, sy):
    """在屏幕坐标 (sx, sy) 处的真实子窗口上合成事件。
    注意：CTk 的 bind() 转发到控件内部 canvas——真实点击落在 canvas 上，
    合成事件也必须生成在 winfo_containing 返回的那个子窗口上（生成在
    CTk 外壳窗口上不会触发控件自身绑定）。事件经 bindtag 冒泡，
    主窗口级拖拽绑定同样能收到。"""
    w = app.winfo_containing(sx, sy)
    if w is None:
        w = app
    w.event_generate(seq, x=sx - w.winfo_rootx(), y=sy - w.winfo_rooty())
    app.update()


def names():
    return [g['name'] for _c, g in app._cards]


def press(card, cx=130, cy=20):
    _gen('<Button-1>', card.winfo_rootx() + cx, card.winfo_rooty() + cy)


def move_over(target, below=True):
    app.update()          # 用重排后的最新坐标定位目标
    _gen('<B1-Motion>', target.winfo_rootx() + 130,
         target.winfo_rooty() + (56 if below else 16))


def release_over(target):
    app.update()
    _gen('<ButtonRelease-1>', target.winfo_rootx() + 130, target.winfo_rooty() + 56)


wait_mapped(app)
wA, wB, wC, wD = [c for c, _g in app._cards]

# T1 点击选中（无位移松开）
press(wB)
release_over(wB)
check('T1a 点击选中乙', app.selected == 1, app.selected)
check('T1b 详情跟随', app.lbl_name.cget('text') == '乙', app.lbl_name.cget('text'))
check('T1c 顺序不变', names() == ['甲', '乙', '丙', '丁'], names())
check('T1d 未写盘', [g['name'] for g in e.load_config(TOML)['games']] == ['甲', '乙', '丙', '丁'])

# T1e 卡内小幅移动：进入拖拽态但不换位，松开复位选中色
press(wB)
move_over(wB, below=True)       # 同一张卡内下移 36px：moved=True、目标是自己
check('T1e 卡内移动不换位', names() == ['甲', '乙', '丙', '丁'], names())
check('T1e2 拖拽提起色', '#2a5a8a' in str(app._cards[1][0].cget('fg_color')))
release_over(wB)
check('T1f 松开复位选中色', '#1f4e79' in str(app._cards[1][0].cget('fg_color')),
      app._cards[1][0].cget('fg_color'))
check('T1f2 未写盘', [g['name'] for g in e.load_config(TOML)['games']] == ['甲', '乙', '丙', '丁'])

# T2/T3 拖动换位：甲依次拖过乙、丙的中线以下
press(wA)
move_over(wB, below=True)      # 甲 → 乙 之后
check('T2a 实时换位（跨乙）', names() == ['乙', '甲', '丙', '丁'], names())
check('T2a2 拖拽提起色（亮于选中色）', '#2a5a8a' in str(app._cards[1][0].cget('fg_color')))
move_over(wC, below=True)      # 甲 → 丙 之后
check('T2b 实时换位（跨丙）', names() == ['乙', '丙', '甲', '丁'], names())
release_over(wC)
check('T3a cfg 顺序同步', [g['name'] for g in app.cfg['games']] == ['乙', '丙', '甲', '丁'])
check('T3b 选中跟随甲', app.selected == 2, app.selected)
check('T3c 落盘保序', [g['name'] for g in e.load_config(TOML)['games']] == ['乙', '丙', '甲', '丁'])
check('T3d 卡片重建后顺序一致', names() == ['乙', '丙', '甲', '丁'], names())

# T4 拖回原位：甲（现下标2）拖到乙上方 → 复原
wA2 = app._cards[2][0]
wB2 = app._cards[0][0]
press(wA2)
move_over(wB2, below=False)    # 甲 → 乙 之前
release_over(wB2)
check('T4 拖回原位', names() == ['甲', '乙', '丙', '丁'], names())
check('T4b 落盘保序', [g['name'] for g in e.load_config(TOML)['games']] == ['甲', '乙', '丙', '丁'])

# T5 运行徽标下可拖（伪造进行中的会话，按游戏ID索引）
fake = types.SimpleNamespace(aborting=False, launch_time=None, name='乙',
                             game=app.cfg['games'][1])
app.sessions['gidB'] = fake
app.refresh_all()
app.update()
check('T5a 徽标出现', 'gidB' in app._card_badges and app._card_badges['gidB'].winfo_exists())
wB3 = app._cards[1][0]
wD3 = app._cards[3][0]
press(wB3)
move_over(wD3, below=True)
release_over(wD3)
check('T5b 徽标卡可拖到末尾', names() == ['甲', '丙', '丁', '乙'], names())
check('T5c 拖后徽标仍在', 'gidB' in app._card_badges and app._card_badges['gidB'].winfo_exists())
del app.sessions['gidB']

# T6 单游戏：无换位目标，不异常
TOML1 = os.path.join(tmpdir, '单游戏.toml')
cfg1 = e.normalize_config({'game': [
    {'name': '独', 'exe': 'C:\\G\\x.exe',
     'sync': [{'local': os.path.join(tmpdir, 'lx'), 'net': os.path.join(tmpdir, 'nx')}]}]})
cfg1['stats_csv'] = os.path.join(tmpdir, 'stats.csv')
app1 = gui.App(TOML1, cfg1)
wait_mapped(app1)


def _gen1(seq, sx, sy):
    w = app1.winfo_containing(sx, sy)
    if w is None:
        w = app1
    w.event_generate(seq, x=sx - w.winfo_rootx(), y=sy - w.winfo_rooty())
    app1.update()


c0 = app1._cards[0][0]
px, py = c0.winfo_rootx() + 130, c0.winfo_rooty() + 20
_gen1('<Button-1>', px, py)
_gen1('<B1-Motion>', px, py + 200)     # 大幅下移（窗口外/无换位目标）
_gen1('<ButtonRelease-1>', px, py + 200)
check('T6a 单游戏顺序不变', len(app1._cards) == 1)
check('T6b 拖拽状态已清', app1._drag is None)

app.destroy()
app1.destroy()
print(f'\n== 拖动排序测试: {ok} 通过, {fail} 失败 ==')
sys.exit(1 if fail else 0)
