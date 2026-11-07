# -*- coding: utf-8 -*-
"""v2.4.0 “仅收录（不同步）”功能回归测试（平台无关，Linux/Windows 均可跑）

覆盖：
  E1  normalize：nosync 布尔容错（true/'yes'/缺省）
  E2  validate：仅收录游戏无目录对合法；普通游戏无目录对仍报错
  E3  save→load 往返：nosync 落盘为 nosync = true、sync = []，重读语义不变
  E4  会话：仅收录游戏请求同步（sync_only/down/up）→ SessionError(EXIT_CONFIG)
  E5  会话：仅收录游戏“开始游戏”dry-run 正常完成，输出含[仅收录]
  E6  CLI 展示：game_label 对仅收录游戏显示“不同步”
  G1  编辑器含“启用存档同步”开关与 _on_sync_toggle
  G2  on_save：nosync 分支（不强制目录对）
  G3  卡片副行与详情面板按 nosync 显示“不同步”
  G4  按钮：仅收录游戏禁用 仅下载/仅上传/打开存档目录（_apply_action_states）
  G5  start_session / on_open_dir 的 nosync 防护
  G6  to_raw 往返保留 nosync 字段
  R1  回归：关闭警告窗三选项（强制退出/最小化/取消）未被本次改动破坏
  R2  回归：BUG-C pending 状态防护仍在
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
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


def make_cfg(games):
    return {'backup_keep': 10, 'timeout_launch': 120, 'net_wait': 1,
            'stats_csv': os.path.join(tempfile.mkdtemp(prefix='yd_ns_'), '统计.csv'),
            'games': games}


NOSYNC_GAME = {'id': 'ns01', 'name': '只收录', 'exe': r'C:\Games\x.exe', 'args': '',
               'workdir': '', 'process': [], 'mode': 'timestamp', 'exclude': [],
               'backup_keep': 10, 'cover': '', 'nosync': True, 'sync': []}
SYNC_GAME = {'id': 'sy01', 'name': '要同步', 'exe': r'C:\Games\y.exe', 'args': '',
             'workdir': '', 'process': [], 'mode': 'timestamp', 'exclude': [],
             'backup_keep': 10, 'cover': '', 'nosync': False,
             'sync': [{'local': os.path.join(tempfile.mkdtemp(prefix='yd_l_'), ''),
                       'net': os.path.join(tempfile.mkdtemp(prefix='yd_n_'), '')}]}

# ---------------- E1 normalize 容错 ----------------
cfg = e.normalize_config({'game': [
    dict(NOSYNC_GAME, nosync=True),
    dict(NOSYNC_GAME, name='手写yes', nosync='yes'),
    dict(NOSYNC_GAME, name='手滑乱写', nosync='banana'),
    dict(SYNC_GAME),
]})
check('E1a nosync=true', cfg['games'][0]['nosync'] is True)
check('E1b nosync="yes" 容错为 True', cfg['games'][1]['nosync'] is True)
check('E1c 无法识别按 False', cfg['games'][2]['nosync'] is False)
check('E1d 缺省为 False', cfg['games'][3]['nosync'] is False)

# ---------------- E2 validate ----------------
try:
    e.validate_config(make_cfg([dict(NOSYNC_GAME)]))
    check('E2a 仅收录无目录对合法', True)
except e.SessionError as err:
    check('E2a 仅收录无目录对合法', False, err.msg)
try:
    e.validate_config(make_cfg([dict(SYNC_GAME, sync=[])]))
    check('E2b 普通游戏无目录对仍报错', False, '应抛 SessionError')
except e.SessionError:
    check('E2b 普通游戏无目录对仍报错', True)

# ---------------- E3 save→load 往返 ----------------
tmp = tempfile.mkdtemp(prefix='yd_e3_')
tpath = os.path.join(tmp, '云档.toml')
e.save_toml(tpath, make_cfg([dict(NOSYNC_GAME), dict(SYNC_GAME)]))
body = open(tpath, encoding='utf-8').read()
check('E3a 落盘含 nosync = true', 'nosync = true' in body)
check('E3b 落盘含 sync = []', 'sync = []' in body)
cfg2 = e.load_config(tpath)
check('E3c 重读 nosync=True', cfg2['games'][0]['nosync'] is True)
check('E3d 重读 nosync=False', cfg2['games'][1]['nosync'] is False)

# ---------------- E4 仅收录请求同步 → 拒绝 ----------------
for direction in ('both', 'down', 'up'):
    try:
        with redirect_stdout(io.StringIO()):
            e.run_session(make_cfg([dict(NOSYNC_GAME)]), dict(NOSYNC_GAME),
                          dry_run=True, sync_only=True, direction=direction)
        check(f'E4 {direction} 同步被拒', False, '应抛 SessionError')
    except e.SessionError as err:
        check(f'E4 {direction} 同步被拒', err.code == e.EXIT_CONFIG and '仅收录' in err.msg, err.msg)

# ---------------- E5 仅收录开始游戏（dry）→ 正常 ----------------
buf = io.StringIO()
try:
    with redirect_stdout(buf):
        code = e.run_session(make_cfg([dict(NOSYNC_GAME)]), dict(NOSYNC_GAME),
                             dry_run=True, sync_only=False, direction='both')
    out = buf.getvalue()
    check('E5a dry-run 退出码 OK', code == e.EXIT_OK, f'rc={code}')
    check('E5b 输出含[仅收录]说明', '[仅收录]' in out)
    check('E5c 不出现同步标题', '开始同步' not in out)
    check('E5d 不获取同步锁', '同步锁' not in out)
except Exception as err:
    check('E5 dry-run 仅收录会话', False, repr(err))

# ---------------- E6 CLI 展示 ----------------
check('E6 game_label=不同步', e.game_label(dict(NOSYNC_GAME)) == '不同步')
check('E6b game_label=模式', e.game_label(dict(SYNC_GAME)) == '时间戳')

# ---------------- E7 nosync 游戏保留目录对 → 仍绝不触碰 ----------------
NS_PAIRS = dict(NOSYNC_GAME, name='带遗留目录对的仅收录',
                sync=[{'local': r'C:\不\存在\的\本地目录', 'net': r'\\不存在\网络\共享'}])
try:
    e.validate_config(make_cfg([dict(NS_PAIRS)]))
    check('E7a 带目录对的仅收录合法（目录对不作校验）', True)
except e.SessionError as err:
    check('E7a 带目录对的仅收录合法', False, err.msg)
try:
    with redirect_stdout(io.StringIO()) as buf:
        code = e.run_session(make_cfg([dict(NS_PAIRS)]), dict(NS_PAIRS),
                             dry_run=True, sync_only=False, direction='both')
    out = buf.getvalue()
    check('E7b 带目录对仍正常 dry-run', code == e.EXIT_OK, f'rc={code}')
    check('E7c 不触碰目录（无目录告警/无同步标题/无锁）',
          '不存在' not in out and '开始同步' not in out and '同步锁' not in out, out[:120])
except Exception as err:
    check('E7 带目录对的仅收录会话', False, repr(err))
try:
    with redirect_stdout(io.StringIO()):
        e.run_session(make_cfg([dict(NS_PAIRS)]), dict(NS_PAIRS),
                      dry_run=True, sync_only=True, direction='both')
    check('E7d 带目录对仍拒绝同步', False, '应抛 SessionError')
except e.SessionError:
    check('E7d 带目录对仍拒绝同步', True)

# ---------------- E8 保存往返保留 nosync 游戏的目录对 ----------------
tmp2 = os.path.join(tempfile.mkdtemp(prefix='yd_e8_'), '云档.toml')
e.save_toml(tmp2, make_cfg([dict(NS_PAIRS), dict(SYNC_GAME)]))
body2 = open(tmp2, encoding='utf-8').read()
check('E8a nosync 落盘且目录对保留', 'nosync = true' in body2 and '不' in body2 and '\\\\' in body2)
cfg3 = e.load_config(tmp2)
check('E8b 重读 nosync+目录对完整', cfg3['games'][0]['nosync'] is True
      and len(cfg3['games'][0]['sync']) == 1)

# ---------------- GUI 静态断言 ----------------
gui = open(os.path.join(BASE, '云档GUI.py'), encoding='utf-8').read()
check('G1a 编辑器有存档同步开关', 'sw_sync' in gui and 'CTkSwitch' in gui)
check('G1b 开关切换处理存在', 'def _on_sync_toggle' in gui)
check('G1c 关闭=仅收录提示', '关闭 = 仅收录' in gui)
check('G2 on_save nosync 分支', 'nosync = not self.sw_sync.get()' in gui)
check('G3a 卡片显示不同步', "'不同步' if g.get('nosync')" in gui)
check('G3b 详情面板 nosync 分支', '仅收录进游戏库，不做存档同步' in gui)
check('G4 按钮禁用逻辑', 'def _apply_action_states' in gui and '_selection_nosync' in gui)
check('G5a start_session 防护', "game.get('nosync') and sync_only" in gui)
check('G5b on_open_dir 防护', '该游戏未启用存档同步，没有本地存档目录' in gui)
check('G6 to_raw 保留 nosync', "if g.get('nosync'):\n            d['nosync'] = True" in gui)

# ---------------- 回归：关闭警告窗与 BUG-C ----------------
check('R1 警告窗三选项', "'强制退出'" in gui and "'最小化'" in gui and "'取消'" in gui)
check('R2a pending 直接销毁', 'self._pending_close' in gui.split('def on_close')[1].split('def ')[0])
check('R2b 看门狗仍在', '_dialog_watchdog' in gui)

print(f'\n==== 结果: {ok}/{ok + fail} 通过 ====')
if fail:
    print('失败项见上')
    sys.exit(1)
