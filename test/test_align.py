# -*- coding: utf-8 -*-
"""mock-robocopy 对齐测试：验证计划层(plan_pass)与 robocopy /E /XO /FFT 语义的
安全不变式 —— 在两种 /FFT 边界实现解读下：
  凡 mock-robocopy 实际复制的文件，必须都在 plan_pass 的复制计划中
  （=> 推送前备份清单永不漏文件，数据安全）
同时走完整 sync_pass 流程验证 备份落盘/清理/标记/幂等。
纯 Linux 可跑（mock 掉 robocopy_run）。
"""
import os
import shutil
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import 云档 as e

ok = fail = 0


def check(name, cond, detail=''):
    global ok, fail
    print(('PASS' if cond else 'FAIL'), name, ('  [' + str(detail) + ']') if (detail and not cond) else '', flush=True)
    if cond:
        ok += 1
    else:
        fail += 1


T0 = 1_800_000_000.0


def put(base, rel, content, mt):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p) or base, exist_ok=True)
    with open(p, 'wb') as f:
        f.write(content if isinstance(content, bytes) else content.encode())
    os.utime(p, (mt, mt))
    return p


def rd(p):
    with open(p, 'rb') as f:
        return f.read()


def make_mock_robocopy(interp_le2):
    """返回模拟 robocopy /E /XO /FFT（+镜像 /MIR）的函数。
    interp_le2=True: “差<=2秒视为同一时间”（文档语义）；False: 严格 <2 秒。
    robocopy 规范行为：
      · 目标缺文件 → 复制；同时间+同大小 → 跳过；同时间+异大小 → 复制；
      · 源更新 → 复制；源更旧 → /XO 排除；镜像：任何差异都复制并删除多余。"""

    def mock_run(src, dst, mode, exc):
        S = e.scan_tree(src, exc)
        D = e.scan_tree(dst, exc)
        copied, deleted = [], []

        def same_t(mt, dmt):
            return (abs(dmt - mt) <= 2.0) if interp_le2 else (abs(dmt - mt) < 2.0)

        for rel, (sz, mt) in sorted(S.items()):
            d = D.get(rel)
            if d is None:
                pass                                   # 复制（下面统一执行）
            else:
                dsz, dmt = d
                if dsz == sz and same_t(mt, dmt):
                    continue                            # 完全一致 → 跳过
                if mode != 'mirror':
                    if mt < dmt and not same_t(mt, dmt):
                        continue                        # /XO：源更旧 → 排除
            s = os.path.join(src, rel)
            t = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(t) or dst, exist_ok=True)
            shutil.copy2(s, t)
            copied.append(rel)
        if mode == 'mirror':
            for rel in sorted(D):
                if rel not in S:
                    os.remove(os.path.join(dst, rel))
                    deleted.append(rel)
        return 0, f'[mock] copied={len(copied)} deleted={len(deleted)}'

    return mock_run


# =====================================================================
# 一、安全不变式：随机化时间戳/大小矩阵 × 两种语义解读
# =====================================================================
print('== 一、plan ⊇ robocopy 安全不变式（两种 /FFT 解读） ==', flush=True)
tmp = tempfile.mkdtemp(prefix='yd_align_')
src = os.path.join(tmp, 'src')
dst = os.path.join(tmp, 'dst')
os.makedirs(src)
os.makedirs(dst)

# 构造覆盖所有边界组合的文件矩阵
cases = [
    # (rel, src大小, src时间偏移, dst大小, dst时间偏移, 说明)
    ('f_new_big',    100, +300.0, 100, 0,     '明确更新'),
    ('f_new_eq',     100, +300.0,  80, 0,     '更新+变大小'),
    ('f_old_big',    100, -300.0, 100, 0,     '明确更旧(/XO)'),
    ('f_old_eq',     100, -300.0,  80, 0,     '更旧+变大小'),
    ('f_same',       100,    0.0, 100, 0,     '完全一致'),
    ('f_fuzz_eq',    100,  +1.5,  100, 0,     '等大小+模糊区内'),
    ('f_buga',       100,  +1.5,   80, 0,     'BUG-A: 异大小+模糊区内'),
    ('f_buga_old',   100,  -1.5,   80, 0,     'BUG-A反向: 异大小+源略旧'),
    ('f_edge_p2_eq', 100,  +2.0,  100, 0,     '等大小+恰好新2.0'),
    ('f_edge_m2_eq', 100,  -2.0,  100, 0,     '等大小+恰好旧2.0'),
    ('f_edge_p2_df', 100,  +2.0,   80, 0,     '异大小+恰好新2.0'),
    ('f_edge_m2_df', 100,  -2.0,   80, 0,     '异大小+恰好旧2.0'),
    ('f_dst_only',   None, None,  60, 0,      '仅目标存在'),
]
for rel, ssz, soff, dsz, doff, _desc in cases:
    if ssz is not None:
        put(src, rel, b'a' * ssz, T0 + soff)
    put(dst, rel, b'b' * dsz, T0 + doff)

for interp_le2 in (True, False):
    tag = '≤2s解读' if interp_le2 else '<2s解读'
    # 每轮重置 dst 到初始矩阵
    shutil.rmtree(dst)
    os.makedirs(dst)
    for rel, ssz, soff, dsz, doff, _desc in cases:
        if ssz is not None:
            pass
        put(dst, rel, b'b' * dsz, T0 + doff)
    mock = make_mock_robocopy(interp_le2)
    e.robocopy_run = mock                      # 注入 mock
    plan_copy, plan_del, _S, _D = e.plan_pass(src, dst, 'timestamp', e.Exclude(e.SELF_EXCLUDES))
    plan_rels = {r for r, _ in plan_copy}
    mock(src, dst, 'timestamp', e.Exclude(e.SELF_EXCLUDES))
    # mock 实际复制了哪些：与初始 dst 内容对比（b 前缀 = 旧内容）
    actually = set()
    for rel, ssz, _so, _ds, _do, _d in cases:
        if ssz is None:
            continue
        if rd(os.path.join(dst, rel)) == b'a' * ssz:
            actually.add(rel)
    missed = actually - plan_rels
    check(f'不变式[{tag}] 实复制 ⊆ 计划(备份不漏)', not missed, f'漏了 {sorted(missed)}')
    extra = plan_rels - actually
    print(f'  [信息][{tag}] 计划={len(plan_rels)} 实际={len(actually)} 多备份={sorted(extra)}', flush=True)
    # dst_only 不能被时间戳模式删除
    check(f'不变式[{tag}] 时间戳模式不删目标独有', os.path.isfile(os.path.join(dst, 'f_dst_only')))

# 镜像模式不变式
shutil.rmtree(dst)
os.makedirs(dst)
for rel, ssz, soff, dsz, doff, _d in cases:
    put(dst, rel, b'b' * dsz, T0 + doff)
mock = make_mock_robocopy(True)
e.robocopy_run = mock
plan_copy, plan_del, _S, _D = e.plan_pass(src, dst, 'mirror', e.Exclude(e.SELF_EXCLUDES))
plan_rels = {r for r, _ in plan_copy}
mock(src, dst, 'mirror', e.Exclude(e.SELF_EXCLUDES))
actually = {rel for rel, ssz, _s, _d, _o, _x in cases
            if ssz is not None and rd(os.path.join(dst, rel)) == b'a' * ssz}
check('不变式[镜像] 实复制 ⊆ 计划', not (actually - plan_rels), str(sorted(actually - plan_rels)))
check('不变式[镜像] 目标独有被删且在删除计划中',
      not os.path.exists(os.path.join(dst, 'f_dst_only')) and 'f_dst_only' in plan_del)

# =====================================================================
# 二、完整 sync_pass 流程（mock robocopy）：备份/清理/标记/幂等
# =====================================================================
print('== 二、sync_pass 完整流程 ==', flush=True)
loc = os.path.join(tmp, 'loc')
net = os.path.join(tmp, 'net')
os.makedirs(loc)
os.makedirs(net)
game = {'name': '对齐游戏', 'id': 'aln01', 'backup_keep': 3}
pair = {'local': loc, 'net': net}
exc = e.make_exclude(game)
put(net, 'save.dat', b'OLD', T0 - 500)
put(net, 'netonly.txt', b'EXTRA', T0 - 500)
put(loc, 'save.dat', b'NEW-LONGER', T0)
ret = e.sync_pass('push', pair, 'timestamp', exc, game, dry=False)
check('F1 推送返回成功', ret is True)
check('F2 网络端已更新', rd(os.path.join(net, 'save.dat')) == b'NEW-LONGER')
check('F3 标记已写入', os.path.isfile(os.path.join(net, e.MARKER_NAME)))
broot = os.path.join(net, e.BACKUP_DIRNAME)
check('F4 备份目录存在且含旧版', os.path.isfile(os.path.join(broot, os.listdir(broot)[0], 'save.dat'))
      and rd(os.path.join(broot, os.listdir(broot)[0], 'save.dat')) == b'OLD')
check('F5 时间戳模式不删 netonly', os.path.isfile(os.path.join(net, 'netonly.txt')))

# 幂等：再推一次 → 0 计划 + 无新增备份
n_before = len(os.listdir(broot))
plan2, _, _, _ = e.plan_pass(loc, net, 'timestamp', exc)
ret2 = e.sync_pass('push', pair, 'timestamp', exc, game, dry=False)
check('F6 幂等：二次推送计划为空', plan2 == [], str(plan2))
check('F7 幂等：不新增备份目录', len(os.listdir(broot)) == n_before, f'{n_before} -> {len(os.listdir(broot))}')

# 备份按份数清理（此时 broot 内有 1 个真实备份 + 5 个手工时间戳目录 = 6）
n_all = len(os.listdir(broot))
for i in range(5):
    ts = f'2026010{i + 1}_000000'
    os.makedirs(os.path.join(broot, ts))
n_all = len(os.listdir(broot))
rm = e.cleanup_backups(net, 3)
n_left = len([n for n in os.listdir(broot) if __import__('re').fullmatch(r'\d{8}_\d{6}', n)])
check('F8 备份清理保留最新3份', rm == n_all - 3 and n_left == 3,
      f'before={n_all} rm={rm} left={n_left}')

# 恢复方向：网络新 → 本地
put(net, 'pull.dat', b'NET-NEW', T0 + 100)
put(loc, 'pull.dat', b'LOC-OLD', T0 - 100)
ret3 = e.sync_pass('restore', pair, 'timestamp', exc, game, dry=False)
check('F9 恢复方向成功且本地更新', ret3 is True and rd(os.path.join(loc, 'pull.dat')) == b'NET-NEW')

# 镜像删除走备份（注：恢复步骤已把 netonly.txt 拉回本地，故镜像只删 goner.txt）
put(net, 'goner.txt', b'GONE', T0)
ret4 = e.sync_pass('push', pair, 'mirror', exc, game, dry=False)
check('F10 镜像推送成功', ret4 is True)
check('F11 镜像删除多余文件', not os.path.exists(os.path.join(net, 'goner.txt')))
backed = set()
for n in os.listdir(broot):
    for root, _d, fs in os.walk(os.path.join(broot, n)):
        backed.update(fs)
check('F12 被删文件先进备份(goner)', 'goner.txt' in backed, str(sorted(backed)))
check('F13 备份目录与标记不被镜像误删', os.path.isdir(broot)
      and os.path.isfile(os.path.join(net, e.MARKER_NAME)))

print(f'\n==== mock-robocopy 对齐测试: {ok}/{ok + fail} 通过 ====', flush=True)
sys.exit(1 if fail else 0)
