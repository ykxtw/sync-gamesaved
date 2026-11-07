# -*- coding: utf-8 -*-
"""GUI 烟雾测试：启动窗口自动退出 + 截图 + 配置往返"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 自动定位源码根目录
sys.path.insert(0, BASE)
import 云档 as engine  # noqa: E402

DEMO = os.path.join(BASE, 'test', 'demo.toml')
COVER = os.path.join(BASE, 'test', 'cover_测试.png')
SHOT = os.path.join(BASE, 'test', 'gui_shot.png')
CSV = os.path.join(BASE, '云档统计.csv')   # v2.2.4 起固定在程序目录

# 1) 生成封面占位图 + 演示统计
try:
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (256, 256), '#274b8f')
    d = ImageDraw.Draw(img)
    d.ellipse([64, 64, 192, 192], fill='#4f8fd6')
    d.ellipse([104, 104, 152, 152], fill='#e8f0fa')
    img.save(COVER)
except Exception as e:
    print('cover gen failed:', e)

os.makedirs(os.path.dirname(CSV), exist_ok=True)
with open(CSV, 'w', encoding='utf-8-sig', newline='') as f:
    f.write('日期,游戏,开始,结束,分钟\n')
    f.write('2026-10-28,阿尔法,20:00:00,21:30:00,90\n')
    f.write('2026-11-01,阿尔法,19:00:00,20:10:00,70\n')
    f.write('2026-11-01,贝塔,15:00:00,16:05:00,65\n')

# 2) 演示配置（含封面）
loc = os.path.join(BASE, 'test', 'loc')
net = os.path.join(BASE, 'test', 'net')
cfg = engine.normalize_config({
    'general': {'backup_keep': 10, 'timeout_launch': 120, 'net_wait': 10},
    'game': [
        {'name': '阿尔法', 'exe': 'C:\\Windows\\System32\\ping.exe', 'args': '-n 1 127.0.0.1',
         'mode': 'timestamp', 'cover': COVER, 'exclude': ['*.tmp', 'shadercache/'],
         'sync': [{'local': loc, 'net': net}]},
        {'name': '贝塔', 'exe': '', 'mode': 'mirror',
         'sync': [{'local': loc, 'net': net}]},
        {'name': '伽马', 'exe': 'C:\\Windows\\System32\\ping.exe', 'args': '-n 1 127.0.0.1',
         'mode': 'archive', 'backup_keep': 5,
         'sync': [{'local': loc, 'net': net}]},
    ]})
engine.save_toml(DEMO, cfg)
back = engine.load_config(DEMO)
assert back['games'][0]['cover'] == COVER, 'cover 往返失败'
assert back['games'][2]['backup_keep'] == 5
print('PASS 配置往返（含 cover）')

# 3) 启动窗口，3.5 秒后自动退出并截图
for f in (SHOT,):
    if os.path.isfile(f):
        os.remove(f)
r = subprocess.run([sys.executable, '-I', os.path.join(BASE, '云档GUI.py'),
                    '--config', DEMO, '--smoke', '3500', '--shot', SHOT],
                   capture_output=True, cwd=BASE, timeout=60)
out = (r.stdout or b'').decode('utf-8', 'replace') + (r.stderr or b'').decode('utf-8', 'replace')
print('smoke rc =', r.returncode)
if out.strip():
    print(out[-800:])
print('PASS 烟雾启动' if r.returncode == 0 else 'FAIL 烟雾启动')
print('PASS 截图生成' if os.path.isfile(SHOT) else 'FAIL 截图未生成')
