# -*- coding: utf-8 -*-
"""GUI 编辑器“启用存档同步”开关交互回归（真实 Tk 窗口，Windows/wine 下运行）

覆盖：
  1. 编辑仅收录游戏：开关=关，同步配置区隐藏、仅收录说明可见
  2. 打开开关：同步配置区恢复显示
  3. 关闭开关保存：nosync 落盘、目录对保留
  4. 重新打开开关保存：nosync=False、目录对完整恢复（不丢配置）
  5. 编辑普通游戏：开关=开、同步区显示
  6. 新增游戏关闭开关保存：无需目录对即可入库
  7. 主界面联动：仅收录游戏卡片“不同步”、仅下载/仅上传/打开存档目录禁用
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import 云档 as engine      # noqa: E402
import 云档GUI as gui      # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo_ns.toml')
with open(DEMO, 'wb') as f:
    f.write('''
[general]
backup_keep = 10
timeout_launch = 120
net_wait = 5

[[game]]
name = '甲'
exe = 'C:\\\\Windows\\\\System32\\\\ping.exe'
mode = 'timestamp'
sync = [
  { local = 'C:\\\\pkg\\\\sync-gamesaved\\\\test\\\\loc', net = 'C:\\\\pkg\\\\sync-gamesaved\\\\test\\\\net' },
]

[[game]]
name = '乙（仅收录）'
exe = 'C:\\\\Windows\\\\System32\\\\notepad.exe'
mode = 'timestamp'
nosync = true
sync = []
'''.encode('utf-8'))

cfg = engine.load_config(DEMO)
app = gui.App(DEMO, cfg)
res = {}
T0 = time.time()


def visible(w):
    try:
        return bool(w.winfo_ismapped())
    except Exception:
        return False


def step1_edit_nosync():
    app.select_game(1)
    app.update()
    res['card_nosync'] = '不同步' in str(app.cards_frame.winfo_children()[1].winfo_children()[1].cget('text'))
    # 按钮：仅下载/仅上传/打开存档目录 禁用；开始游戏可用
    res['btns_disabled'] = all(str(app.cfg and b.cget('state')) == 'disabled'
                               for b in (app.btn_down, app.btn_up, app.btn_opendir))
    res['play_enabled'] = str(app.btn_play.cget('state')) == 'normal'
    ed = gui.GameEditor(app, 1)
    res['ed'] = ed
    app.after(600, step2_check_editor)


def step2_check_editor():
    ed = res['ed']
    ed.update_idletasks(); ed.update()
    res['sw_off'] = (ed.sw_sync.get() == 0)
    res['zone_hidden'] = not visible(ed.sync_zone)
    res['hint_shown'] = visible(ed.lbl_nosync)
    # 打开开关 → 同步区恢复
    ed.sw_sync.toggle()
    ed.update_idletasks(); ed.update()
    res['zone_back'] = visible(ed.sync_zone) and not visible(ed.lbl_nosync)
    # 再关回去（还原为仅收录状态）后保存
    ed.sw_sync.toggle()
    ed.update()
    ed.btn_save.invoke()
    app.after(600, step3_after_save)


def step3_after_save():
    c = engine.load_config(DEMO)
    g = [x for x in c['games'] if x['name'] == '乙（仅收录）'][0]
    res['kept_nosync'] = (g['nosync'] is True)
    # 现在填入目录对再关闭开关保存 → 目录对应保留
    app.select_game(0)
    app.update()
    ed2 = gui.GameEditor(app, 0)
    res['ed2'] = ed2
    app.after(600, step4_sync_game_editor)


def step4_sync_game_editor():
    ed2 = res['ed2']
    ed2.update_idletasks(); ed2.update()
    res['normal_sw_on'] = (ed2.sw_sync.get() == 1) and visible(ed2.sync_zone)
    # 关闭开关保存 → 甲变成仅收录但目录对保留
    ed2.sw_sync.toggle()
    ed2.update()
    ed2.btn_save.invoke()
    app.after(600, step5_after_toggle_off)


def step5_after_toggle_off():
    c = engine.load_config(DEMO)
    g = [x for x in c['games'] if x['name'] == '甲'][0]
    res['toggle_off_kept_pairs'] = (g['nosync'] is True and len(g['sync']) == 1)
    # 再编辑：开关应显示关；打开后保存 → 恢复为同步游戏，目录对不丢
    app.selected = 0
    app._refresh_detail()
    app.update()
    ed3 = gui.GameEditor(app, 0)
    res['ed3'] = ed3
    app.after(600, step6_reenable)


def step6_reenable():
    ed3 = res['ed3']
    ed3.update_idletasks(); ed3.update()
    res['reopened_sw_off'] = (ed3.sw_sync.get() == 0 and not visible(ed3.sync_zone))
    ed3.sw_sync.toggle()
    ed3.update()
    ed3.btn_save.invoke()
    app.after(600, step7_new_game)


def step7_new_game():
    c = engine.load_config(DEMO)
    g = [x for x in c['games'] if x['name'] == '甲'][0]
    res['reenabled'] = (g['nosync'] is False and len(g['sync']) == 1)
    # 新增游戏：关闭开关 → 无需目录对即可保存
    ed4 = gui.GameEditor(app, None)
    res['ed4'] = ed4
    app.after(600, step8_save_new)


def step8_save_new():
    ed4 = res['ed4']
    ed4.update_idletasks(); ed4.update()
    ed4.e_name.delete(0, 'end'); ed4.e_name.insert(0, '丙（新收录）')
    ed4.sw_sync.toggle()
    ed4.update()
    # 清空目录对行（新增默认给了一行空行，无碍；这里验证空目录对+关开关可保存）
    ed4.btn_save.invoke()
    app.after(700, finish)


def finish():
    c = engine.load_config(DEMO)
    g = [x for x in c['games'] if x['name'] == '丙（新收录）']
    res['new_nosync'] = bool(g) and g[0]['nosync'] is True
    app.destroy()


app.after(500, step1_edit_nosync)
deadline = time.time() + 30
while res.get('new_nosync') is None and time.time() < deadline:
    try:
        app.update()
        time.sleep(0.05)
    except Exception:
        break
try:
    app.destroy()
except Exception:
    pass

checks = [
    ('卡片显示不同步', res.get('card_nosync')),
    ('仅收录按钮禁用', res.get('btns_disabled')),
    ('开始游戏仍可用', res.get('play_enabled')),
    ('开关初始=关', res.get('sw_off')),
    ('同步区隐藏', res.get('zone_hidden')),
    ('仅收录说明可见', res.get('hint_shown')),
    ('开关联动恢复显示', res.get('zone_back')),
    ('仅收录保存不丢nosync', res.get('kept_nosync')),
    ('普通游戏开关=开', res.get('normal_sw_on')),
    ('关开关保存保留目录对', res.get('toggle_off_kept_pairs')),
    ('重开编辑开关=关', res.get('reopened_sw_off')),
    ('重新开启恢复同步游戏', res.get('reenabled')),
    ('新增仅收录游戏免目录对', res.get('new_nosync')),
]
bad = [n for n, v in checks if not v]
for n, v in checks:
    print('PASS' if v else 'FAIL', n)
print('====', 'ALL PASS (13)' if not bad else f'FAILED: {bad}', '====')
sys.exit(0 if not bad else 1)
