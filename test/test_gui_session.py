# -*- coding: utf-8 -*-
"""GUI 会话级测试：进程内启动窗口 → 程序化触发"开始游戏" → 等待会话完成 → 校验日志与统计"""
import os
import sys
import time

BASE = r'C:\Users\HC\.zcode\workspace\c7d3d2f8-915f-4575-870f-7549bedc0c96\云档'
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'test'))
import 云档 as engine  # noqa: E402
import 云档GUI as gui  # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo.toml')
CSV = os.path.join(BASE, '云档统计.csv')   # v2.2.4 起固定在程序目录

# 目录必须存在，否则预检会弹创建确认框（无人点击会挂起）
os.makedirs(os.path.join(BASE, 'test', 'loc'), exist_ok=True)
os.makedirs(os.path.join(BASE, 'test', 'net'), exist_ok=True)

cfg = engine.load_config(DEMO)
rows_before = sum(1 for _ in open(CSV, encoding='utf-8-sig')) if os.path.isfile(CSV) else 0

app = gui.App(DEMO, cfg)
result = {'done': False, 'code': None, 'log': ''}


def trigger():
    app.select_game(0)  # 阿尔法（exe=ping -n 1）
    app.start_session(False)


def watch():
    if not app.running:
        result['done'] = True
        result['code'] = app.lbl_status.cget('text')
        result['log'] = app.txt_log.get('1.0', 'end')
        app.destroy()
        return
    if time.time() - T0 > 40:
        result['log'] = app.txt_log.get('1.0', 'end')
        app.destroy()
        return
    app.after(400, watch)


T0 = time.time()
app.after(700, trigger)
app.after(900, watch)
app.mainloop()

log = result['log']
conds = {
    'done': bool(result['done']),
    'status': '✔ 完成' in str(result['code']),
    '完成横幅': '全部操作完成' in log,
    '含恢复': '恢复' in log,
    '含推送': '推送' in log,
}
print('条件:', conds, '| status =', repr(result['code']))
ok = all(conds.values())
print('PASS GUI 会话全链路' if ok else 'FAIL GUI 会话全链路')
print('--- 日志尾部 ---')
print('\n'.join(log.splitlines()[-12:]))
rows_after = sum(1 for _ in open(CSV, encoding='utf-8-sig'))
print('PASS 统计已追加' if rows_after > rows_before else 'WARN 统计未追加')
sys.exit(0 if ok else 1)
