# -*- coding: utf-8 -*-
"""打包产物验证：GUI exe 烟雾启动 + CLI exe 功能"""
import os
import subprocess
import sys

BASE = r'C:\Users\HC\.zcode\workspace\c7d3d2f8-915f-4575-870f-7549bedc0c96\云档'
GUI = os.path.join(BASE, 'dist', '云档.exe')
CLI = os.path.join(BASE, 'dist', '云档-cli.exe')
DEMO = os.path.join(BASE, 'test', 'demo.toml')

print('exe 大小: GUI %.1fMB / CLI %.1fMB' % (
    os.path.getsize(GUI) / 1048576, os.path.getsize(CLI) / 1048576))

# GUI exe 烟雾（窗口自动关闭）
r = subprocess.run([GUI, '--config', DEMO, '--smoke', '3000'], capture_output=True, timeout=60)
print('PASS GUI exe 烟雾启动' if r.returncode == 0 else f'FAIL GUI exe rc={r.returncode}')
if r.returncode != 0:
    print((r.stderr or b'').decode('utf-8', 'replace')[-600:])

# CLI exe：列表 / 仅同步（先确保目录对存在，否则预检会询问创建）
os.makedirs(os.path.join(BASE, 'test', 'loc'), exist_ok=True)
os.makedirs(os.path.join(BASE, 'test', 'net'), exist_ok=True)
r = subprocess.run([CLI, '--list', '--config', DEMO, '--no-pause'], capture_output=True, timeout=60)
out = (r.stdout or b'').decode('utf-8', 'replace')
print('PASS CLI exe --list' if r.returncode == 0 and '阿尔法' in out else f'FAIL CLI --list rc={r.returncode}')

r = subprocess.run([CLI, '--config', DEMO, '-g', '贝塔', '--sync-only', '--no-pause'],
                   capture_output=True, timeout=120)
out = (r.stdout or b'').decode('utf-8', 'replace')
print('PASS CLI exe 会话' if r.returncode == 0 and '全部操作完成' in out else f'FAIL CLI 会话 rc={r.returncode}')
if r.returncode != 0:
    print(out[-600:])
