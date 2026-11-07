# -*- coding: utf-8 -*-
"""同步锁回归：本机陈旧锁自动清理 / 远端锁强制接管 / SMB删除失败改名让路 / 彻底失败报错"""
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 自动定位源码根目录
sys.path.insert(0, BASE)
import 云档 as engine  # noqa: E402

results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print('PASS' if cond else 'FAIL', name, detail)


class Bridge:
    def __init__(self, answer=0):
        self.answer = answer
        self.asked = []

    def ask_option(self, title, message, options, default_index=0):
        self.asked.append(message)
        return self.answer


def make_lock(net, info=None):
    os.makedirs(os.path.join(net, engine.LOCK_NAME), exist_ok=True)
    if info is not None:
        with open(os.path.join(net, engine.LOCK_NAME, 'info.json'), 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False)


def read_info(net):
    try:
        with open(os.path.join(net, engine.LOCK_NAME, 'info.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


NET = tempfile.mkdtemp(prefix='yundang_lock_')
HOST = socket.gethostname()

# ---- 1) 本机陈旧锁（A机崩溃后A机重开）：pid 已死 → 自动清理，不弹框 ----
make_lock(NET, {'computer': HOST, 'user': 'u', 'pid': 99999999, 'game': 'g', 'time': '2026-01-01T00:00:00'})
b = Bridge()
buf = io.StringIO()
with redirect_stdout(buf):
    ok = engine.NetLock(NET).acquire(b)
check('1 本机陈旧锁自动接管', ok and not b.asked and read_info(NET)['computer'] == HOST,
      f'ok={ok} asked={len(b.asked)}')
check('1b 有自动清理提示', '自动清理' in buf.getvalue())
engine.NetLock(NET).release()   # 真实应用会话结束时必然释放（同时清进程内登记表）
shutil.rmtree(NET, ignore_errors=True)

# ---- 2) 本机锁但 pid 活着（自己这台正开着另一个云档）→ 询问，选退出 ----
make_lock(NET, {'computer': HOST, 'user': 'u', 'pid': os.getpid(), 'game': 'g', 'time': '2026-01-01T00:00:00'})
b = Bridge(answer=1)
with redirect_stdout(io.StringIO()):
    ok = engine.NetLock(NET).acquire(b)
check('2 本机活锁→询问→退出', ok is False and len(b.asked) == 1)
shutil.rmtree(NET, ignore_errors=True)

# ---- 3) 远端机器锁（B机视角）+ 强制接管：正常删除路径 ----
make_lock(NET, {'computer': 'OTHER-PC', 'user': 'u', 'pid': 1234, 'game': 'g', 'time': '2026-01-01T00:00:00'})
b = Bridge(answer=0)
with redirect_stdout(io.StringIO()):
    ok = engine.NetLock(NET).acquire(b)
check('3 远端锁强制接管成功', ok and len(b.asked) == 1 and read_info(NET)['computer'] == HOST,
      f'ok={ok}')
engine.NetLock(NET).release()
shutil.rmtree(NET, ignore_errors=True)

# ---- 4) SMB 删除失败模拟：rmtree 对锁路径总失败 → 改名让路成功 ----
make_lock(NET, {'computer': 'OTHER-PC', 'user': 'u', 'pid': 1234, 'game': 'g', 'time': '2026-01-01T00:00:00'})
lock_path = os.path.join(NET, engine.LOCK_NAME)
real_rmtree = shutil.rmtree


def stub_rmtree(path, *a, **k):
    if os.path.normcase(str(path)) == os.path.normcase(lock_path):
        raise OSError(5, 'Access is denied（模拟SMB句柄未释放）', str(path))
    return real_rmtree(path, *a, **k)


shutil.rmtree = stub_rmtree
try:
    b = Bridge(answer=0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok = engine.NetLock(NET).acquire(b)
    stale_left = [n for n in os.listdir(NET) if n.startswith(engine.LOCK_NAME + '.stale')]
    check('4 删除失败→改名让路成功', ok and read_info(NET)['computer'] == HOST and not stale_left,
          f'ok={ok} stale={stale_left}')
    check('4b 有改名让路提示', '改名让路' in buf.getvalue())
finally:
    shutil.rmtree = real_rmtree
engine.NetLock(NET).release()   # 恢复真 rmtree 后再释放，清掉登记表
shutil.rmtree(NET, ignore_errors=True)

# ---- 5) 删除+改名都失败 → 明确报错并退出码语义（acquire=False）----
make_lock(NET, {'computer': 'OTHER-PC', 'user': 'u', 'pid': 1234, 'game': 'g', 'time': '2026-01-01T00:00:00'})
lock_path = os.path.join(NET, engine.LOCK_NAME)
real_rename = os.rename


def stub_rename(src, dst, *a, **k):
    if os.path.normcase(str(src)) == os.path.normcase(lock_path):
        raise OSError(5, 'Access is denied（模拟改名也失败）', str(src))
    return real_rename(src, dst, *a, **k)


shutil.rmtree = stub_rmtree
os.rename = stub_rename
try:
    b = Bridge(answer=0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok = engine.NetLock(NET).acquire(b)
    check('5 删除改名全失败→False+明确指引', ok is False and '强制接管失败' in buf.getvalue()
          and '手动删除' in buf.getvalue())
finally:
    shutil.rmtree = real_rmtree
    os.rename = real_rename
shutil.rmtree(NET, ignore_errors=True)

print('====', 'ALL PASS' if all(results) else f'FAILED {results}', '====')
sys.exit(0 if all(results) else 1)
