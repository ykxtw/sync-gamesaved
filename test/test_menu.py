# -*- coding: utf-8 -*-
"""多游戏菜单 + 模糊匹配测试"""
import os
import subprocess
import sys

BASE = r'C:\Users\HC\.zcode\workspace\c7d3d2f8-915f-4575-870f-7549bedc0c96\云档'
T5 = os.path.join(BASE, 'test', 'test_t5.toml')
with open(T5, 'w', encoding='utf-8') as f:
    f.write(f'''# 测试5：多游戏菜单
[general]
net_wait = 5

[[game]]
name = '阿尔法'
exe = 'C:\\Windows\\System32\\ping.exe'
args = '-n 1 127.0.0.1'
mode = 'timestamp'
sync = [ {{ local = '{BASE}\\test\\loc', net = '{BASE}\\test\\net' }} ]

[[game]]
name = '贝塔'
exe = ''
mode = 'timestamp'
sync = [ {{ local = '{BASE}\\test\\loc', net = '{BASE}\\test\\net' }} ]
''')

def run(args, input_text=None, timeout=90):
    r = subprocess.run([sys.executable, '-I', os.path.join(BASE, '云档.py')] + args,
                       input=(input_text.encode('utf-8') if input_text else None),
                       capture_output=True, cwd=BASE, timeout=timeout)
    return r.returncode, (r.stdout or b'').decode('utf-8', 'replace')

rc, out = run(['--list', '--config', T5, '--no-pause'])
print('PASS' if rc == 0 and '阿尔法' in out and '贝塔' in out else 'FAIL', f'--list 双游戏 rc={rc}')

rc, out = run(['--config', T5, '--no-pause'], input_text='2\n')
print('PASS' if rc == 3 and '未配置 exe' in out else 'FAIL', f'菜单选2=贝塔(无exe应报配置错误) rc={rc}')

rc, out = run(['--config', T5, '--sync-only', '--no-pause'], input_text='2\n')
print('PASS' if rc == 0 and '贝塔' in out else 'FAIL', f'菜单选2=贝塔 sync-only 正常同步 rc={rc}')

rc, out = run(['-g', '尔法', '--config', T5, '--no-pause'])
print('PASS' if rc == 0 and '阿尔法' in out else 'FAIL', f'模糊匹配 -g 尔法 rc={rc}')

rc, out = run(['-g', '不存在的游戏', '--config', T5, '--no-pause'])
print('PASS' if rc == 3 else 'FAIL', f'找不到游戏=配置错误 rc={rc}')
