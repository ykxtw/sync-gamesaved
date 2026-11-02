# -*- coding: utf-8 -*-
"""GUI 编辑器回归：保存/行内按钮可见 + 从图标生成封面 + 添加游戏落盘"""
import os
import sys
import time

BASE = r'C:\Users\HC\.zcode\workspace\c7d3d2f8-915f-4575-870f-7549bedc0c96\云档'
sys.path.insert(0, BASE)
import 云档 as engine  # noqa: E402
import 云档GUI as gui  # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo.toml')
SHOT = os.path.join(BASE, 'test', 'editor_shot.png')
BAK = DEMO + '.bak'
with open(DEMO, 'rb') as f:
    _demo_backup = f.read()
with open(BAK, 'wb') as f:
    f.write(_demo_backup)
cfg = engine.load_config(DEMO)

app = gui.App(DEMO, cfg)
res = {'btn_ok': None, 'btns_ok': None, 'icon': None, 'added': None, 'shot': None}
T0 = time.time()


def open_editor():
    ed = gui.GameEditor(app, None)          # “+ 添加游戏”
    res['ed'] = ed
    app.after(700, measure)


def measure():
    ed = res['ed']
    ed.update_idletasks()
    ed.update()
    # 1) 保存按钮在可视区内
    btn = ed.btn_save
    top = btn.winfo_rooty() - ed.winfo_rooty()
    bottom = top + btn.winfo_height()
    res['btn_ok'] = (top >= 0 and bottom <= ed.winfo_height())
    print(f'保存按钮: bottom={bottom:.0f} 窗口高={ed.winfo_height():.0f} ->',
          'PASS' if res['btn_ok'] else 'FAIL')
    # 2) 行内按钮（选择程序/目录/图片/从图标生成）不被输入框挤出可视区
    vw = ed.winfo_width()
    rx = ed.winfo_rootx()
    clipped = [str(b.cget('text')) for b in ed._btns
               if b.winfo_rootx() + b.winfo_width() > rx + vw + 1]
    res['btns_ok'] = not clipped
    print(f'行内按钮可见性（共 {len(ed._btns)} 个）:',
          'PASS 全部可见' if res['btns_ok'] else f'FAIL 被裁切: {clipped}')
    # 目录对区高度必须随内容自适应（不得出现大片空白）
    ph = ed.pairs_frame.winfo_height()
    res['pairs_h_ok'] = ph < 100
    print(f'目录对区高度: {ph:.0f}px ->', 'PASS' if res['pairs_h_ok'] else 'FAIL(应≈40-60px)')
    try:
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(ed.winfo_rootx(), ed.winfo_rooty(),
                             rx + vw, ed.winfo_rooty() + ed.winfo_height())).save(SHOT)
        res['shot'] = SHOT
    except Exception as e:
        print('截图失败:', e)
    fill_and_save(ed)


def fill_and_save(ed):
    """模拟用户：选程序→自动填充→再选一次验证跳过→图标→目录对→保存"""
    ed.e_exe.insert(0, r'C:\Windows\System32\cmd.exe')
    ed._maybe_autofill(r'C:\Windows\System32\cmd.exe')
    autofill_ok = (ed.e_name.get().strip() == 'cmd'
                   and ed.e_process.get().strip() == 'cmd.exe')
    print('选程序自动填充:', 'PASS' if autofill_ok else
          f'FAIL name={ed.e_name.get()!r} process={ed.e_process.get()!r}')
    # 已有内容时跳过
    ed._maybe_autofill(r'C:\Windows\System32\notepad.exe')
    skip_ok = (ed.e_name.get().strip() == 'cmd'
               and ed.e_process.get().strip() == 'cmd.exe')
    res['autofill'] = autofill_ok and skip_ok
    print('已有内容跳过:', 'PASS' if skip_ok else 'FAIL')
    icon_btn = next(b for b in ed._btns if str(b.cget('text')) == '从图标生成')
    icon_btn.invoke()
    cover = ed.e_cover.get().strip()
    res['icon'] = bool(cover) and not os.path.isabs(cover) \
        and os.path.isfile(gui.resolve_app_path(cover))
    print('从图标生成封面(相对路径):', 'PASS' if res['icon'] else 'FAIL', cover)
    ed.e_keep.delete(0, 'end')
    ed.e_keep.insert(0, '3')
    pr = ed.pair_rows[0]
    pr['local'].insert(0, os.path.join(BASE, 'test', 'loc'))
    pr['net'].insert(0, os.path.join(BASE, 'test', 'net'))
    ed.txt_exclude.insert('1.0', '*.tmp\nshadercache/')
    ed.btn_save.invoke()


def watch():
    if res['added'] is not None or time.time() - T0 > 25:
        app.destroy()
        return
    if 'cmd' in [g['name'] for g in app.cfg['games']]:
        res['added'] = True
        app.after(300, lambda: app.destroy())
        return
    app.after(300, watch)


app.after(500, open_editor)
app.after(1200, watch)
app.mainloop()

added = res['added'] is True
saved = False
if added:
    back = engine.load_config(DEMO)
    g = [x for x in back['games'] if x['name'] == 'cmd']
    saved = bool(g) and g[0]['backup_keep'] == 3 and g[0]['sync'][0]['local'].endswith('loc') \
        and g[0]['cover'].replace('\\', '/').startswith('covers/') \
        and os.path.isfile(gui.resolve_app_path(g[0]['cover'])) \
        and g[0]['process'] == ['cmd.exe']
print('配置落盘:', 'PASS（相对封面路径/等待进程）' if saved else 'FAIL')
with open(DEMO, 'wb') as f:                  # 还原夹具
    f.write(_demo_backup)
os.remove(BAK)
checks = [res['btn_ok'], res['btns_ok'], res['pairs_h_ok'], res['icon'], added and saved, res.get('autofill')]
print('====', 'ALL PASS' if all(checks) else f'FAILED {checks}', '====')
sys.exit(0 if all(checks) else 1)
