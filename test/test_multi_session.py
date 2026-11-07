# -*- coding: utf-8 -*-
"""v2.5.0 多会话并行回归（任意启动：一个游戏运行中可再启动另一个）

引擎层：
  M1 两游戏并行（不同网络目录）→ 双双成功，统计各落一行，互不干扰
  M2 两游戏共用同一网络目录 → 第二个会话被“进程内锁”拒绝（EXIT_PRECHECK），
     且不弹“强制接管”确认框（NoBridge 一旦被询问即计失败）

GUI 层：
  M3 游戏A会话中，游戏B仍可启动（并行启动成功）
  M4 卡片运行徽标出现两个；状态栏聚合显示
  M5 选中B中止 → 仅B中止，A照常完成（统计有A无B），日志按游戏名区分
  M6 两个会话（一个在游戏中）点关闭 → game 模式警告；强制退出 → 两个中止信号都置位
"""
import os
import sys
import tempfile
import time
import threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import 云档 as engine   # noqa: E402
import 云档GUI as gui   # noqa: E402

NT = os.name == 'nt'
GAME_EXE = r'C:\Windows\System32\ping.exe' if NT else '/bin/sleep'
GAME_ARGS = '-n 2 127.0.0.1' if NT else '2'

results = []


def check(name, cond, detail=''):
    results.append(bool(cond))
    print(('PASS' if cond else 'FAIL'), name, detail)


class Collector(engine.Reporter):
    def __init__(self):
        self.lines = []

    def info(self, msg=''):
        self.lines.append(str(msg))

    def phase(self, name, detail=''):
        pass

    def event(self, name, data=None):
        pass


class NoBridge(engine.UiBridge):
    """不应有任何交互确认；一旦被询问记录下来（测试判定失败）。"""
    asked = []

    def ask_option(self, title, message, options, default_index=0):
        NoBridge.asked.append(title)
        return default_index


def mk_raw(base, tag_a='A', tag_b='B', shared_net=False):
    loc_a = os.path.join(base, 'loc_a')
    net_a = os.path.join(base, 'net_a')
    loc_b = os.path.join(base, 'loc_b')
    net_b = net_a if shared_net else os.path.join(base, 'net_b')
    for d in (loc_a, net_a, loc_b, net_b):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(loc_a, 'a.txt'), 'w', encoding='utf-8') as f:
        f.write('A-v1\n')
    with open(os.path.join(loc_b, 'b.txt'), 'w', encoding='utf-8') as f:
        f.write('B-v1\n')
    return {
        'general': {'backup_keep': 2, 'timeout_launch': 30, 'net_wait': 3},
        'game': [
            {'name': f'游戏{tag_a}', 'id': f'gid{tag_a}', 'exe': GAME_EXE, 'args': GAME_ARGS,
             'process': [f'zz_none_{tag_a}'], 'mode': 'timestamp',
             'sync': [{'local': loc_a, 'net': net_a}]},
            {'name': f'游戏{tag_b}', 'id': f'gid{tag_b}', 'exe': GAME_EXE, 'args': GAME_ARGS,
             'process': [f'zz_none_{tag_b}'], 'mode': 'timestamp',
             'sync': [{'local': loc_b, 'net': net_b}]},
        ],
    }


def run_one(cfg, game, key, out):
    try:
        out[key] = engine.run_session(cfg, game, sync_only=False,
                                      bridge=NoBridge(), control=engine.SessionControl(),
                                      reporter=Collector())
    except engine.SessionError as e:
        out[key] = e.code


# ================= 引擎层 =================
tmp1 = tempfile.mkdtemp(prefix='yd_m1_')
cfg1 = engine.normalize_config(mk_raw(tmp1))
csv1 = os.path.join(tmp1, 'stats.csv')
cfg1['stats_csv'] = csv1
out = {}
t_a = threading.Thread(target=run_one, args=(cfg1, cfg1['games'][0], 'A', out))
t_b = threading.Thread(target=run_one, args=(cfg1, cfg1['games'][1], 'B', out))
t0 = time.time()
t_a.start(); t_b.start(); t_a.join(); t_b.join()
elapsed = time.time() - t0
check('M1a 两会话均成功(退出码0)', out.get('A') == 0 and out.get('B') == 0, f'out={out}')
check('M1b 并行执行(总耗时<串行两倍)', elapsed < 8, f'elapsed={elapsed:.1f}s')
check('M1c 统计各落一行', os.path.isfile(csv1) and
      sum(1 for line in open(csv1, encoding='utf-8-sig') if '游戏A' in line or '游戏B' in line) == 2)
check('M1d 双方网络目录都有文件',
      os.path.isfile(os.path.join(tmp1, 'net_a', 'a.txt')) and
      os.path.isfile(os.path.join(tmp1, 'net_b', 'b.txt')))
check('M1e 无交互确认弹出', not NoBridge.asked, f'asked={NoBridge.asked}')

tmp2 = tempfile.mkdtemp(prefix='yd_m2_')
cfg2 = engine.normalize_config(mk_raw(tmp2, 'C', 'D', shared_net=True))
cfg2['stats_csv'] = os.path.join(tmp2, 'stats.csv')
out2 = {}
t_c = threading.Thread(target=run_one, args=(cfg2, cfg2['games'][0], 'C', out2))
t_c.start()
time.sleep(1.2)   # 等第一个拿到锁、进入游戏等待阶段
NoBridge.asked.clear()
t_d = threading.Thread(target=run_one, args=(cfg2, cfg2['games'][1], 'D', out2))
t_d.start()
t_c.join(); t_d.join()
check('M2a 先启动的会话成功', out2.get('C') == 0, f'out={out2}')
check('M2b 共用网络目录的第二个被拒绝(退出码4)', out2.get('D') == engine.EXIT_PRECHECK, f'out={out2}')
check('M2c 未弹强制接管确认框', not NoBridge.asked, f'asked={NoBridge.asked}')

if all(results):
    print('== 引擎层多会话检查通过 ==')
else:
    print('== 引擎层多会话检查存在失败 ==')
    sys.exit(1)

# ================= GUI 层 =================
tmp3 = tempfile.mkdtemp(prefix='yd_m3_')
cfg3 = engine.normalize_config(mk_raw(tmp3))
csv3 = os.path.join(tmp3, 'stats.csv')
cfg3['stats_csv'] = csv3
app = gui.App(os.path.join(tmp3, 'unused.toml'), cfg3)
app.ask_confirm = lambda *a, **k: True          # 中止确认直接放行
state = {'a_done': False, 'b_done': False, 'both_seen': False, 'tagged': False}


def both_running():
    return len(app.sessions) == 2


def watch():
    if both_running() and not state['both_seen']:
        state['both_seen'] = True     # 并行态已捕捉；日志标签在会话结束后统一断言
    if not app.sessions and state['both_seen']:
        state['a_done'] = state['b_done'] = True
        state['final_log'] = app.txt_log.get('1.0', 'end')
        finish()
        return
    if time.time() - T0 > 40:
        finish()
        return
    app.after(200, watch)


def finish():
    try:
        app.destroy()
    except Exception:
        pass


def flow():
    """主流程：并行启动 → 观察并行期 → 中止B → 等A完成"""
    app.select_game(0)                       # 游戏A
    app.start_session(False)                 # 启动A
    check('G1 A会话已注册', len(app.sessions) == 1)
    app.select_game(1)                       # 游戏B —— 核心特性：A运行中仍可选/可启动
    check('G2 会话中可切换选中', app.selected == 1)
    check('G3 其他游戏的开始按钮可用', str(app.btn_play.cget('state')) == 'normal')
    app.start_session(False)                 # 启动B（并行）
    check('G4 双会话并行注册', len(app.sessions) == 2)
    check('G5 两个卡片都有运行徽标', len(app._card_badges) == 2)
    # 徽标必须真实可见（pack 顺序错误会落到底部空腔被 72px 固定高裁掉）
    app.update_idletasks()
    app.update()
    vis = []
    for gid, bd in app._card_badges.items():
        try:
            vis.append(bd.winfo_ismapped() and bd.winfo_rooty() > 0)
        except Exception:
            vis.append(False)
    check('G5b 徽标已映射可见', all(vis), f'vis={vis}')
    watch()                                  # 立即开始观察（赶在会话结束前捕捉并行态）
    app.after(1200, abort_b)


def abort_b():
    if 'gidB' in app.sessions:
        app.on_abort()                       # 当前选中=游戏B → 仅中止B
    check('G6 仅B被中止', 'gidA' not in app.sessions or not app.sessions['gidA'].control.abort.is_set(),
          'A的abort不应置位') if 'gidA' in app.sessions else None
    if 'gidB' in app.sessions:
        check('G6b B的abort已置位', app.sessions['gidB'].control.abort.is_set())
    app.select_game(0)
    check('G7 A运行中：开始按钮变为「运行中…」且禁用（按游戏粒度）',
          str(app.btn_play.cget('state')) == 'disabled' and '运行中' in str(app.btn_play.cget('text')),
          f"state={app.btn_play.cget('state')} text={app.btn_play.cget('text')}")


# M6 关闭警告（逻辑层，注入答案）
answers = {}
calls = {'game': 0, 'sync': 0}


def fake_ask(mode):
    calls[mode] = calls.get(mode, 0) + 1
    return answers.get(mode)


real_ask = app._ask_close_choice


def m6():
    # 两个会话：A 在游戏中(launch_time)，B 在同步阶段
    app.sessions.clear()
    app.sessions['x1'] = gui.GameSession('x1', '游戏X', {'name': '游戏X', 'sync': []}, False, 'both')
    app.sessions['x1'].launch_time = time.time()
    app.sessions['x2'] = gui.GameSession('x2', '游戏Y', {'name': '游戏Y', 'sync': []}, True, 'down')
    app._ask_close_choice = fake_ask
    answers['game'] = None
    app.on_close()
    check('G8 任一游戏在运行→game模式警告', calls['game'] == 1 and app.winfo_exists())
    answers['game'] = 'force'
    app.on_close()
    both_abort = all(s.control.abort.is_set() for s in app.sessions.values())
    check('G9 强制退出→两个会话中止信号都置位', both_abort and app._pending_close)
    app._ask_close_choice = real_ask
    app.sessions.clear()
    app._pending_close = False


T0 = time.time()
app.after(600, m6)
app.after(900, flow)
app.mainloop()

check('G10 并行期间两个会话都出现过', state['both_seen'])
final_log = state.get('final_log', '')
check('G11 日志按游戏名区分([游戏A]/[游戏B])', '[游戏A]' in final_log and '[游戏B]' in final_log)
rows = [line for line in open(csv3, encoding='utf-8-sig') if '游戏A' in line or '游戏B' in line] \
    if os.path.isfile(csv3) else []
check('G12 统计有A无B(B被中止)', any('游戏A' in r for r in rows) and not any('游戏B' in r for r in rows),
      f'rows={len(rows)}')
check('G13 A会话完成后注册表清空', state['a_done'])

print(f"\n==== 结果: {sum(results)}/{len(results)} 通过 ====")
if not all(results):
    print('失败项索引:', [i for i, ok in enumerate(results) if not ok])
    sys.exit(1)
