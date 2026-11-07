# -*- coding: utf-8 -*-
"""打包产物验证：GUI exe 烟雾启动 + CLI exe 功能"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 自动定位源码根目录
GUI = os.path.join(BASE, 'dist', '云档.exe')
CLI = os.path.join(BASE, 'dist', '云档-cli.exe')
DEMO = os.path.join(BASE, 'test', 'demo.toml')

# wine 测试环境：真 robocopy 不存在，垫片（python 副本）装在 C:\py311。
# exe 进程按「exe目录→cwd→system32→PATH」搜索 robocopy，故垫片存在时以它为 cwd。
# 真 Windows 上垫片不存在，cwd=None，行为与原版完全一致。
RC_CWD = r'C:\py311' if os.path.isfile(r'C:\py311\robocopy.exe') else None

print('exe 大小: GUI %.1fMB / CLI %.1fMB' % (
    os.path.getsize(GUI) / 1048576, os.path.getsize(CLI) / 1048576))

# GUI exe 烟雾（窗口自动关闭）
r = subprocess.run([GUI, '--config', DEMO, '--smoke', '3000'], capture_output=True, timeout=60, cwd=RC_CWD)
print('PASS GUI exe 烟雾启动' if r.returncode == 0 else f'FAIL GUI exe rc={r.returncode}')
if r.returncode != 0:
    print((r.stderr or b'').decode('utf-8', 'replace')[-600:])

# CLI exe：列表 / 仅同步（先确保目录对存在，否则预检会询问创建）
os.makedirs(os.path.join(BASE, 'test', 'loc'), exist_ok=True)
os.makedirs(os.path.join(BASE, 'test', 'net'), exist_ok=True)
r = subprocess.run([CLI, '--list', '--config', DEMO, '--no-pause'], capture_output=True, timeout=60, cwd=RC_CWD)
out = (r.stdout or b'').decode('utf-8', 'replace')
print('PASS CLI exe --list' if r.returncode == 0 and '阿尔法' in out else f'FAIL CLI --list rc={r.returncode}')

r = subprocess.run([CLI, '--config', DEMO, '-g', '贝塔', '--sync-only', '--no-pause'],
                   capture_output=True, timeout=120, cwd=RC_CWD)
out = (r.stdout or b'').decode('utf-8', 'replace')
print('PASS CLI exe 会话' if r.returncode == 0 and '全部操作完成' in out else f'FAIL CLI 会话 rc={r.returncode}')
if r.returncode != 0:
    print(out[-600:])
