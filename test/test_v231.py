# -*- coding: utf-8 -*-
"""v2.3.1 修复项自测：
BUG-A plan_pass 时间戳语义对齐 robocopy /XO /FFT（大小变+时间模糊→复制，备份清单不漏）
BUG-B normalize_config 容错解析（backup_keep='ten' 等不再裸异常；process/exclude 字符串容错）
BUG-C on_close pending 状态（GUI 逻辑，本文件做静态断言：源码含防护分支）
"""
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import tomllib

import 云档 as e

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


# ---------------- BUG-A：plan_pass 时间戳语义 ----------------
tmp = tempfile.mkdtemp(prefix='yd_a_')
src = os.path.join(tmp, 'src')
dst = os.path.join(tmp, 'dst')
os.makedirs(src)
os.makedirs(dst)


def put(base, rel, size, age=None):
    p = os.path.join(base, rel)
    with open(p, 'wb') as f:
        f.write(b'x' * size)
    mt = time.time() - (age or 0)
    os.utime(p, (mt, mt))
    return p


now = time.time()

# 场景1【核心】：大小不同，目标与源的 mtime 差 1.2 秒（2 秒模糊区内，源略新）
put(src, 'a.save', 100, age=0)
put(dst, 'a.save', 80, age=1.2)
# 场景2：大小相同，mtime 差 1.2 秒 → 两端一致
put(src, 'b.save', 50, age=0)
put(dst, 'b.save', 50, age=1.2)
# 场景3：大小不同，源新 5 秒（超出模糊区）→ 复制
put(src, 'c.save', 100, age=0)
put(dst, 'c.save', 80, age=5)
# 场景4：大小不同，源旧 5 秒 → /XO 排除，不复制
put(src, 'd.save', 100, age=5)
put(dst, 'd.save', 80, age=0)
# 场景5：大小相同，源新 5 秒 → 复制
put(src, 'e.save', 50, age=0)
put(dst, 'e.save', 50, age=5)

copy, delete, S, D = e.plan_pass(src, dst, 'timestamp', None)
rels = {r for r, _ in copy}
check('A1 大小变+时间模糊内 → 复制(修复点)', 'a.save' in rels, str(sorted(rels)))
check('A2 大小同+时间模糊内 → 跳过', 'b.save' not in rels)
check('A3 大小变+源更新 → 复制', 'c.save' in rels)
check('A4 大小变+源更旧 → 跳过(/XO)', 'd.save' not in rels)
check('A5 大小同+源更新 → 复制', 'e.save' in rels)
check('A6 时间戳模式不删除多余文件', delete == [])

# 备份清单（推送前 victims 推导，与 sync_pass 相同写法）必须含 a.save
victims = [rel for rel, _ in copy if rel in D]
check('A7 备份清单含被覆盖文件 a.save', 'a.save' in victims, str(victims))

# 镜像模式不受影响：任何差异都复制，多余文件进 delete
copy_m, delete_m, _, _ = e.plan_pass(src, dst, 'mirror', None)
rels_m = {r for r, _ in copy_m}
check('A8 镜像：a~e 有差异的都复制', {'a.save', 'c.save', 'd.save', 'e.save'} <= rels_m, str(sorted(rels_m)))
check('A9 镜像：一致文件跳过', 'b.save' not in rels_m)
check('A10 镜像：无多余文件不误删', delete_m == [])

# 排除规则仍生效（回归）
put(src, 'skip.log', 10)
copy_x, _, _, _ = e.plan_pass(src, dst, 'timestamp', e.Exclude(['*.log']))
check('A11 /XF 排除仍生效', 'skip.log' not in {r for r, _ in copy_x})

# ---------------- BUG-B：normalize_config 容错 ----------------
# 手滑数值
n = e.normalize_config({'general': {'backup_keep': 'ten', 'timeout_launch': '1x0'},
                        'game': [{'name': 'G', 'backup_keep': 'oops',
                                  'sync': [{'local': 'C:\\a', 'net': 'C:\\b'}]}]})
check('B1 backup_keep=字符串数字手滑 → 回退默认10', n['backup_keep'] == 10, str(n['backup_keep']))
check('B2 timeout_launch 非法 → 回退120', n['timeout_launch'] == 120)
check('B3 游戏级 backup_keep 非法 → 继承全局默认', n['games'][0]['backup_keep'] == 10)

n2 = e.normalize_config({'general': {'backup_keep': '20', 'net_wait': 1.9},
                         'game': [{'name': 'G', 'process': 'game.exe', 'exclude': '*.tmp',
                                   'sync': [{'local': 'C:\\a', 'net': 'C:\\b'}]}]})
check('B4 字符串数字可接受', n2['backup_keep'] == 20)
check('B5 浮点取整', n2['net_wait'] == 1)
check('B6 process 字符串 → 单元素列表', n2['games'][0]['process'] == ['game.exe'],
      str(n2['games'][0]['process']))
check('B7 exclude 字符串 → 单元素列表', n2['games'][0]['exclude'] == ['*.tmp'])

# 结构手滑：general/sync/game 写成字符串
n3 = e.normalize_config({'general': 'oops', 'game': [{'name': 'G', 'sync': 'bad'}]})
check('B8 general 为字符串 → 忽略用默认', n3['backup_keep'] == 10)
check('B9 sync 为字符串 → 忽略为空列表', n3['games'][0]['sync'] == [])
n4 = e.normalize_config({'game': 'oops'})
check('B10 game 为字符串 → 空游戏列表', n4['games'] == [])

# load_config 端到端：数值手滑的 TOML 能正常载入
toml_bad = os.path.join(tmp, 'bad.toml')
with open(toml_bad, 'w', encoding='utf-8') as f:
    f.write("[general]\nbackup_keep = 'ten'\n\n[[game]]\nname = '试玩'\n"
            "process = 'game.exe'\nsync = [{ local = 'C:\\\\l', net = 'C:\\\\n' }]\n")
cfg = e.load_config(toml_bad)
check('B11 手滑TOML可载入且回退默认', cfg is not None and cfg['backup_keep'] == 10
      and cfg['games'][0]['process'] == ['game.exe'], str(cfg))

# TOML 语法错误 → 明确 SessionError（非裸 traceback）
toml_syn = os.path.join(tmp, 'syn.toml')
with open(toml_syn, 'w', encoding='utf-8') as f:
    f.write('[general\nbackup_keep = = 3\n')
try:
    e.load_config(toml_syn)
    check('B12 语法错误抛SessionError', False, '未抛异常')
except e.SessionError as ex:
    check('B12 语法错误抛SessionError', ex.code == e.EXIT_CONFIG and '配置' in ex.msg, ex.msg)

# validate 的明确错误仍是 SessionError（回归）
toml_mode = os.path.join(tmp, 'mode.toml')
with open(toml_mode, 'w', encoding='utf-8') as f:
    f.write("[[game]]\nname = 'G'\nmode = 'xxx'\nsync = [{ local = 'C:\\\\l', net = 'C:\\\\n' }]\n")
try:
    e.load_config(toml_mode)
    check('B13 非法mode仍明确报错', False, '未抛异常')
except e.SessionError as ex:
    check('B13 非法mode仍明确报错', ex.code == e.EXIT_CONFIG, ex.msg)

# ---------------- BUG-C：on_close pending 防护（静态断言） ----------------
gui_src = open(os.path.join(BASE, '云档GUI.py'), encoding='utf-8').read()
oc = gui_src.split('def on_close(self):')[1].split('def ')[0]
oc_body = '\n'.join(l for l in oc.splitlines() if not l.strip().startswith('"""'))
check('C1 on_close 首查 _pending_close',
      next(l.strip() for l in oc_body.splitlines() if l.strip()) == 'if self._pending_close:',
      oc[:120])
check('C2 pending 分支直接销毁且不弹框', 'destroy' in oc.split('if not self.running')[0]
      and 'messagebox' not in oc.split('if not self.running')[0])
check('C3 关闭确认改走深色警告窗', "_ask_close_choice('game')" in oc and "_ask_close_choice('sync')" in oc)
cwd_cls = gui_src.split('class CloseWarnDialog')[1].split('class ')[0] if 'class CloseWarnDialog' in gui_src else ''
check('C4 警告窗三选项 强制退出/最小化/取消',
      all(k in cwd_cls for k in ("'强制退出'", "'最小化'", "'取消'")), cwd_cls[:80])
check('C5 警告窗标题与推荐提示', '游戏正在运行' in cwd_cls and '正在同步' in cwd_cls
      and '推荐选' in cwd_cls)
check('C6 不再使用系统 messagebox 确认关闭', 'askyesnocancel' not in oc and 'askyesno' not in oc)

# ---------------- 版本 ----------------
check('V1 版本号 2.3.3', e.VERSION == '2.3.3', e.VERSION)

print(f'\n==== 结果: {ok}/{ok + fail} 通过 ====')
sys.exit(1 if fail else 0)
