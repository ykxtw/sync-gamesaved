# -*- coding: utf-8 -*-
"""云档 自动化测试执行器：用子进程跑 云档.py，校验退出码与文件状态"""
import os
import shutil
import subprocess
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)                     # 云档\
SCRIPT = os.path.join(ROOT, '云档.py')
LOC = os.path.join(BASE, 'loc')
NET = os.path.join(BASE, 'net')
LOC_A = os.path.join(BASE, 'loc_a')
NET_A = os.path.join(BASE, 'net_a')

results = []


def run(args, input_text=None, timeout=120):
    r = subprocess.run([sys.executable, '-I', SCRIPT] + args,
                       input=(input_text.encode('utf-8') if input_text else None),
                       capture_output=True, cwd=ROOT, timeout=timeout)
    return r.returncode, (r.stdout or b'').decode('utf-8', 'replace')


def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS' if cond else 'FAIL'), name, detail)


def wf(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def rf(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


T1 = os.path.join(BASE, 'test_t1.toml')
T2 = os.path.join(BASE, 'test_t2.toml')
T3 = os.path.join(BASE, 'test_t3.toml')
T4 = os.path.join(BASE, 'test_t4.toml')

# ---------- 场景A：镜像中止（选3 → 退出码5，文件不动） ----------
subprocess.run([sys.executable, os.path.join(BASE, 'make_fixture.py')], capture_output=True)
if os.path.isfile(os.path.join(NET, '云档.ok')):
    os.remove(os.path.join(NET, '云档.ok'))
wf(os.path.join(LOC, 'loc_extra2.txt'), 'abort-case\n')
rc, _ = run(['--config', T2, '--sync-only', '--no-pause'], input_text='3\n')
check('A1 中止退出码=5', rc == 5, f'rc={rc}')
check('A2 文件未被动', os.path.isfile(os.path.join(LOC, 'loc_extra2.txt')))
check('A3 标记未写', not os.path.isfile(os.path.join(NET, '云档.ok')))

# ---------- 场景B：互斥锁 ----------
os.makedirs(os.path.join(NET, '.云档.lock'), exist_ok=True)
rc, out = run(['--config', T2, '--sync-only', '--no-pause'], input_text='2\n')
check('B1 锁被占+退出=4', rc == 4, f'rc={rc}')
check('B2 锁目录仍在', os.path.isdir(os.path.join(NET, '.云档.lock')))
rc, out = run(['--config', T2, '--sync-only', '--no-pause'], input_text='1\n1\n')
check('B3 强制接管成功', rc == 0, f'rc={rc}')
check('B4 结束后锁已释放', not os.path.exists(os.path.join(NET, '.云档.lock')))

# ---------- 场景C：目标进程已在运行（explorer 常驻） ----------
rc, out = run(['--config', T4, '--sync-only', '--no-pause'])
check('C1 已运行检测退出码=4', rc == 4, f'rc={rc}')

# ---------- 场景D：归档模式 ----------
shutil.rmtree(LOC_A, ignore_errors=True)
shutil.rmtree(NET_A, ignore_errors=True)
os.makedirs(LOC_A)
os.makedirs(NET_A)
wf(os.path.join(LOC_A, 'f1.txt'), 'f1-v1\n')
wf(os.path.join(LOC_A, 'f2.txt'), 'f2\n')
rc, out = run(['--config', T3, '--no-pause'])          # 完整会话：恢复(无归档)+启动ping+打包
zips = sorted(f for f in os.listdir(NET_A) if f.endswith('.zip'))
check('D1 首次会话生成1个zip', rc == 0 and len(zips) == 1, f'rc={rc} zips={zips}')
for f in os.listdir(LOC_A):
    os.remove(os.path.join(LOC_A, f))                  # 清空本地
rc, out = run(['--config', T3, '--sync-only', '--no-pause'])
zips = sorted(f for f in os.listdir(NET_A) if f.endswith('.zip'))
check('D2 清空本地后从归档恢复', rc == 0 and rf(os.path.join(LOC_A, 'f1.txt')) == 'f1-v1\n',
      '' if rc == 0 else f'rc={rc} out={out[-400:]}')
check('D3 第二次会话再生成zip', len(zips) == 2, f'zips={zips} out={out[-300:] if len(zips) != 2 else ""}')
# 损坏归档（更早时间戳）→ 恢复应跳过它用最新的；保留策略也应清掉它
with zipfile.ZipFile(os.path.join(NET_A, '测试.01_20000101_000000.zip'), 'w') as z:
    z.writestr('x.txt', 'garbage')
with open(os.path.join(NET_A, '测试.01_20000101_000000.zip'), 'ab') as f:
    f.write(b'corrupt-tail')
wf(os.path.join(LOC_A, 'f1.txt'), 'f1-v2\n')           # 本地修改 → 比最新归档新 → 会触发保护提示
rc, out = run(['--config', T3, '--sync-only', '--no-pause'], input_text='y\n')  # 应答：仍从归档恢复
zips = sorted(f for f in os.listdir(NET_A) if f.endswith('.zip'))
check('D4 保留策略只留2份(损坏的最旧被清)', len(zips) == 2 and not any('20000101' in z for z in zips),
      f'rc={rc} zips={zips} out={out[-300:] if rc else ""}')
# 再次清空本地 → 应从最新完好归档恢复（内容为恢复-推送链路里的 f1-v1）
for f in os.listdir(LOC_A):
    os.remove(os.path.join(LOC_A, f))
rc, out = run(['--config', T3, '--sync-only', '--no-pause'])
check('D5 恢复到最新归档内容', rc == 0 and rf(os.path.join(LOC_A, 'f1.txt')) == 'f1-v1\n',
      f'rc={rc} got={open(os.path.join(LOC_A, "f1.txt")).read() if os.path.isfile(os.path.join(LOC_A, "f1.txt")) else "MISSING"}')

# ---------- 场景E：幂等（再跑一次时间戳同步应为0复制） ----------
subprocess.run([sys.executable, os.path.join(BASE, 'make_fixture.py')], capture_output=True)
run(['--config', T1, '--sync-only', '--no-pause'])
rc, out = run(['--config', T1, '--sync-only', '--no-pause'])
check('E1 幂等：第二次0复制', '复制 0 个文件' in out, out[-200:] if rc else '')

# ---------- 场景F：向导生成配置 ----------
TOW = os.path.join(BASE, 'test_wiz.toml')
if os.path.isfile(TOW):
    os.remove(TOW)
wiz_input = '\n'.join([
    'C:\\Windows\\System32\\ping.exe',   # exe
    '',                                  # 名称默认 ping
    '1',                                 # 模式 timestamp
    '1',                                 # 目录对数
    LOC,                                 # 本地目录
    os.path.join(BASE, 'net_wiz'),       # 网络目录（不存在）
    'y',                                 # 创建
    '',                                  # 排除
    '',                                  # 不继续添加
]) + '\n'
rc, out = run(['--wizard', '--config', TOW, '--no-pause'], input_text=wiz_input)
check('F1 向导退出码=0', rc == 0, f'rc={rc}')
rc, out = run(['--list', '--config', TOW, '--no-pause'])
check('F2 向导产物可解析(--list)', rc == 0 and 'ping' in out, f'rc={rc}')

# ---------- 场景G：--stats ----------
rc, out = run(['--stats', '--config', T1, '--no-pause'])
check('G1 --stats 正常', rc == 0 and '游玩时长统计' in out)

print()
fails = [n for n, ok in results if not ok]
print(f'==== 结果: {len(results) - len(fails)}/{len(results)} 通过 ====')
if fails:
    print('失败项:', ', '.join(fails))
    sys.exit(1)
