#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云档 —— 游戏存档 局域网同步/备份 工具（v2，Python 重构版）

开玩前：网络 -> 本地恢复；退出后：本地 -> 网络推送（推送前自动备份将被覆盖的网络文件）。

三种模式：
  timestamp  时间戳：两端各自保留最新，不删除多余文件（默认，最安全）
  mirror     镜像  ：两端完全一致，会删除另一端多余文件（配合会话标记防中断覆盖）
  archive    归档  ：网络端保存 zip 快照（按份数保留），本地端恢复最新归档

用法:
  云档.py                     单游戏直接开始；多游戏出菜单
  云档.py --game 名称         按名称选择（支持模糊匹配）
  云档.py --sync-only         不启动游戏，仅双向同步（日常备份）
  云档.py --dry-run           演练模式：只显示将发生什么，不写任何文件
  云档.py --list              列出配置中的游戏
  云档.py --stats             查看游玩时长统计
  云档.py --wizard            重新运行配置向导
  云档.py --config 路径.toml  指定配置文件（默认与本程序同目录的 云档.toml）
  云档.py --verbose           控制台同步显示 robocopy 输出
  云档.py --no-pause          结束后不暂停（供脚本/计划任务调用）

退出码: 0 成功 | 1 同步失败 | 2 启动失败 | 3 配置错误 | 4 预检失败 | 5 用户取消 | 9 未预期错误
"""

import argparse
import atexit
import csv
import fnmatch
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler

try:
    import tomllib
except ImportError:  # Python < 3.11
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None

try:
    import psutil
except ImportError:
    psutil = None

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
VERSION = '2.3.3'

EXIT_OK = 0
EXIT_SYNC = 1
EXIT_LAUNCH = 2
EXIT_CONFIG = 3
EXIT_PRECHECK = 4
EXIT_ABORT = 5
EXIT_UNEXPECTED = 9

MARKER_NAME = '云档.ok'          # 会话标记：正常结束的会话才会写入
LOCK_NAME = '.云档.lock'          # 跨机器互斥锁（网络目录内）
BACKUP_DIRNAME = '云档备份'       # 推送前备份目录（网络目录内）
SELF_EXCLUDES = [MARKER_NAME, LOCK_NAME, BACKUP_DIRNAME]

MODES = ('timestamp', 'mirror', 'archive')
MODE_LABEL = {'timestamp': '时间戳', 'mirror': '镜像', 'archive': '归档'}

MTIME_FUZZ = 2.0   # 与 robocopy /FFT 一致：2 秒内视为相同
POLL_SEC = 1

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, '云档.log')

VERBOSE = False
NO_PAUSE = False

# ----------------------------------------------------------------------------
# 日志与输出
# ----------------------------------------------------------------------------
LOG = logging.getLogger('yundang')
LOG.setLevel(logging.INFO)
try:
    _fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(_fh)
except Exception:
    pass

# 会话运行期的活动桥接（GUI/CLI 各自实现），见 run_session
_ACTIVE = {'reporter': None, 'control': None}


class SessionError(Exception):
    """带退出码的流程中止。CLI 捕获后打印并退出；GUI 捕获后弹窗提示。"""

    def __init__(self, msg, code=EXIT_CONFIG):
        super().__init__(msg)
        self.msg = msg
        self.code = code


def say(msg='', also_log=True):
    """输出一行：有活动 Reporter 时交给它（GUI），否则打印并写日志。"""
    rep = _ACTIVE['reporter']
    if rep is not None:
        rep.info(msg)
        return
    print(msg, flush=True)
    if also_log:
        for line in str(msg).splitlines() or ['']:
            LOG.info(line)


def notify_phase(name, detail=''):
    rep = _ACTIVE['reporter']
    if rep is not None:
        rep.phase(name, detail)


def notify_event(name, data=None):
    rep = _ACTIVE['reporter']
    if rep is not None:
        rep.event(name, data)


def _check_abort():
    c = _ACTIVE['control']
    if c is not None and c.abort.is_set():
        raise SessionError('会话已中止（未执行推送）。', EXIT_ABORT)


class Reporter:
    """会话输出。默认：打印 + 写日志。GUI 传入子类改为推送队列。"""

    def info(self, msg=''):
        print(msg, flush=True)
        for line in str(msg).splitlines() or ['']:
            LOG.info(line)

    def phase(self, name, detail=''):
        LOG.info('[阶段] %s %s', name, detail)

    def event(self, name, data=None):
        LOG.info('[事件] %s %s', name, data if data is not None else '')


class UiBridge:
    """交互确认的桥接接口：返回所选选项下标。"""

    def ask_option(self, title, message, options, default_index=0):
        raise NotImplementedError


class ConsoleBridge(UiBridge):
    def ask_option(self, title, message, options, default_index=0):
        say(message)
        for i, o in enumerate(options, 1):
            say(f'  [{i}] {o}')
        try:
            s = input(f'请选择 [1-{len(options)}，回车={default_index + 1}]: ')
        except (EOFError, OSError):
            return default_index
        if s.strip().isdigit() and 1 <= int(s) <= len(options):
            return int(s) - 1
        return default_index


class SessionControl:
    """会话控制信号（线程安全）：stop_wait=提前结束游戏等待并推送；abort=中止整个会话。"""

    def __init__(self):
        self.stop_wait = threading.Event()
        self.abort = threading.Event()


def pause_if_needed():
    if NO_PAUSE:
        return
    try:
        # windowed exe（-w 打包）里 sys.stdin 为 None
        if sys.stdin is not None and sys.stdin.isatty():
            input('\n按回车键退出...')
    except (EOFError, OSError, AttributeError):
        pass


def die(msg, code=EXIT_CONFIG):
    """中止：抛出带退出码的 SessionError。
    （旧版本此处定义了两个 die，后者 sys.exit 会绕过 GUI 的 SessionError 捕获，
    导致窗口版遇到配置错误时静默退出；统一改为抛异常，由入口层统一处理）"""
    raise SessionError(msg, code)


def human(n):
    n = float(n)
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return f'{n:.0f}{u}' if u == 'B' else f'{n:.1f}{u}'
        n /= 1024
    return f'{n:.1f}TB'


def now_ts():
    return datetime.now().strftime('%Y%m%d_%H%M%S')


# ----------------------------------------------------------------------------
# 交互输入
# ----------------------------------------------------------------------------
def ask(prompt, default=None):
    """读一行输入。EOF 时：有默认值返回默认值，否则返回 None（调用方应中止）。"""
    try:
        s = input(prompt)
    except EOFError:
        return default if default is not None else None
    s = s.strip()
    if not s and default is not None:
        return default
    return s


def ask_int(prompt, default, lo=None, hi=None):
    while True:
        s = ask(prompt, str(default))
        if s is None:
            die('输入已结束，操作中止。', EXIT_ABORT)
        if re.fullmatch(r'-?\d+', s):
            v = int(s)
            if (lo is None or v >= lo) and (hi is None or v <= hi):
                return v
        say(f'  请输入 {"%d~%d" % (lo, hi) if lo is not None and hi is not None else "整数"}。')


# ----------------------------------------------------------------------------
# 排除规则
# ----------------------------------------------------------------------------
class Exclude:
    """排除规则匹配：
    - 不含 / 的模式：匹配任意层级的文件/目录名（支持 * ? 通配）
    - 含 / 的模式：匹配相对路径（也匹配路径结尾，如 'saves/tmp' 匹配 'a/saves/tmp'）
    """

    def __init__(self, patterns=()):
        self.names = []
        self.paths = []
        for raw in patterns or ():
            p = str(raw).strip().replace('\\', '/').strip('/')
            if not p:
                continue
            p = p.lower()
            (self.paths if '/' in p else self.names).append(p)
        self.names = sorted(set(self.names))
        self.paths = sorted(set(self.paths))

    def _name(self, name):
        n = name.lower()
        return any(fnmatch.fnmatchcase(n, p) for p in self.names)

    def _path(self, rel):
        r = rel.lower()
        if any(r == p or r.endswith('/' + p) for p in self.paths):
            return True
        return False

    def skip_file(self, name, rel):
        return self._name(name) or self._path(rel)

    def skip_dir(self, name, rel):
        return self._name(name) or self._path(rel)


def make_exclude(game):
    return Exclude(list(game.get('exclude') or []) + SELF_EXCLUDES)


def robocopy_exclude_args(exc):
    args = []
    if exc.names:
        args += ['/XF'] + list(exc.names)   # robocopy 的 /XF 对通配与裸名均匹配任意层级
        args += ['/XD'] + list(exc.names)
    if exc.paths:
        args += ['/XD'] + [p.replace('/', '\\') for p in exc.paths]
    return args


# ----------------------------------------------------------------------------
# 目录扫描与差异规划
# ----------------------------------------------------------------------------
def scan_tree(root, exc):
    """返回 {相对路径(posix): (大小, mtime)}，跳过符号链接/junction 与排除项。"""
    out = {}
    root = os.path.abspath(root)

    def walk(dirpath, rel):
        try:
            entries = list(os.scandir(dirpath))
        except OSError as e:
            LOG.warning('扫描失败 %s: %s', dirpath, e)
            return
        for e in entries:
            name = e.name
            r = f'{rel}/{name}' if rel else name
            try:
                if e.is_symlink():
                    continue
                is_junc = getattr(e, 'is_junction', None)
                if is_junc and is_junc():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if exc and exc.skip_dir(name, r):
                        continue
                    walk(e.path, r)
                elif e.is_file(follow_symlinks=False):
                    if exc and exc.skip_file(name, r):
                        continue
                    st = e.stat(follow_symlinks=False)
                    out[r] = (st.st_size, st.st_mtime)
            except OSError:
                continue

    walk(root, '')
    return out


def plan_pass(src, dst, mode, exc):
    """规划一次 src -> dst 同步。返回 (copy[(rel,size)], delete[rel], src_scan, dst_scan)"""
    S = scan_tree(src, exc)
    D = scan_tree(dst, exc)
    copy = []
    delete = []
    for rel, (sz, mt) in S.items():
        d = D.get(rel)
        if d is None:
            copy.append((rel, sz))
            continue
        dsz, dmt = d
        # “两端一致”用严格小于 2 秒：恰好 2.0 秒整的差（FAT 粒度下常见）交给下方判定，
        # 计划侧宁可多复制多备份，也不让备份清单出现漏网可能（robocopy /FFT 的
        # “相同”边界在不同实现下含 2.0 或不含，取安全侧对齐）
        if dsz == sz and abs(dmt - mt) < MTIME_FUZZ:
            continue  # 两端一致
        if mode == 'mirror':
            copy.append((rel, sz))          # 镜像：任何差异都覆盖
        elif mt >= dmt + MTIME_FUZZ or (dsz != sz and mt >= dmt - MTIME_FUZZ):
            # 时间戳（对齐 robocopy /E /XO /FFT 实际行为）：
            #   · 源更新（不小于 2 秒模糊区外沿）→ 复制
            #   · 大小不同且源不比目标旧 → 复制。修复：游戏快速重写存档时
            #     “大小变了、修改时间仍在模糊区内”，旧判法会漏判为“无变化”，
            #     导致推送前备份清单缺失该文件（robocopy 实际会覆盖网络旧版）。
            #     边界取 >=（含恰好 2.0 秒）：与备份离清单的安全优先取向一致。
            copy.append((rel, sz))
    if mode == 'mirror':
        delete = [rel for rel in D if rel not in S]
    return copy, delete, S, D


def preview_list(title, items, with_size=True):
    if not items:
        return
    say(f'      {title}:')
    for it in items[:12]:
        if with_size:
            rel, sz = it
            say(f'        + {rel}  ({human(sz)})')
        else:
            say(f'        - {it}')
    if len(items) > 12:
        say(f'        ... 共 {len(items)} 项')


# ----------------------------------------------------------------------------
# robocopy 同步引擎
# ----------------------------------------------------------------------------
def _decode(b):
    for enc in ('utf-8', 'gbk'):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return b.decode('utf-8', 'replace')


def robocopy_run(src, dst, mode, exc):
    """执行 robocopy，失败自动重试 2 次。返回 (rc, output)。rc<8 为成功。"""
    cmd = ['robocopy', src, dst]
    cmd += ['/MIR'] if mode == 'mirror' else ['/E', '/XO']
    cmd += ['/FFT', '/COPY:DAT', '/DCOPY:T', '/R:1', '/W:1', '/XJ', '/NP', '/NDL', '/MT:8']
    cmd += robocopy_exclude_args(exc)
    LOG.info('RUN %s', subprocess.list2cmdline(cmd))
    attempts = 3
    rc, out = 16, ''
    for attempt in range(1, attempts + 1):
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            p = subprocess.run(cmd, capture_output=True, creationflags=flags)
            rc = p.returncode
            out = _decode(p.stdout or b'') + _decode(p.stderr or b'')
        except FileNotFoundError:
            say('  [错误] 找不到 robocopy（仅支持 Windows）。')
            return 16, ''
        if out.strip():
            LOG.info('robocopy 输出:\n%s', out)
        if VERBOSE and out.strip():
            print(out, flush=True)
        if rc < 8:
            return rc, out
        if attempt < attempts:
            say(f'  同步失败 (rc={rc})，5 秒后重试 ({attempt}/{attempts - 1})...')
            time.sleep(5)
    return rc, out


def sync_pass(direction, pair, mode, exc, game, dry):
    """执行一次 robocopy 同步。direction: 'restore'(网络→本地) / 'push'(本地→网络)"""
    local, net = pair['local'], pair['net']
    if direction == 'restore':
        src, dst = net, local
        say(f'\n---- 恢复：网络 → 本地 ----')
    else:
        src, dst = local, net
        say(f'\n---- 推送：本地 → 网络 ----')
    say(f'  {src}')
    say(f'  → {dst}')

    copy, delete, _src_scan, D = plan_pass(src, dst, mode, exc)
    total = sum(s for _, s in copy)
    msg = f'  计划：复制 {len(copy)} 个文件（{human(total)}）'
    if delete:
        msg += f'，删除 {len(delete)} 个多余文件'
    say(msg)
    preview_list('将复制', copy)
    preview_list('将删除', delete, with_size=False)

    if dry:
        return True

    backup_path = None
    if direction == 'push':
        # 推送前备份：网络端将被覆盖/删除的文件先存到 云档备份/<时间戳>/
        victims = [(rel, D[rel][0]) for rel, _ in copy if rel in D]
        victims += [(rel, D[rel][0]) for rel in delete]
        if victims:
            backup_path = do_backup(net, victims)
            if backup_path:
                say(f'  已备份 {len(victims)} 个将被覆盖/删除的网络文件')
                say(f'    → {backup_path}')

    rc, _ = robocopy_run(src, dst, mode, exc)
    if rc >= 8:
        say(f'  [错误] robocopy 失败 rc={rc}（已重试 {3 - 1} 次，详情见日志）')
        return False
    say(f'  完成（rc={rc}）')

    if direction == 'push':
        removed = cleanup_backups(net, game.get('backup_keep', 10))
        if removed:
            say(f'  已清理 {removed} 个过期备份')
        write_marker(net, game['name'])
    return True


def do_backup(net, victims):
    ts = now_ts()
    bdir = os.path.join(net, BACKUP_DIRNAME, ts)
    try:
        os.makedirs(bdir, exist_ok=True)
    except OSError as e:
        say(f'  [警告] 无法创建备份目录: {e}')
        return None
    ok = 0
    for rel, _sz in victims:
        s = os.path.join(net, rel)
        d = os.path.join(bdir, rel)
        try:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            ok += 1
        except OSError as e:
            say(f'  [警告] 备份失败 {rel}: {e}')
    try:
        with open(os.path.join(bdir, '清单.json'), 'w', encoding='utf-8') as f:
            json.dump({'time': ts, 'files': [rel for rel, _ in victims]}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return bdir if ok else None


def cleanup_backups(net, keep):
    """按份数清理 云档备份/ 下的旧备份。keep<=0 表示不清理。"""
    broot = os.path.join(net, BACKUP_DIRNAME)
    if keep is None or keep <= 0 or not os.path.isdir(broot):
        return 0
    ds = []
    try:
        for name in os.listdir(broot):
            p = os.path.join(broot, name)
            if os.path.isdir(p) and re.fullmatch(r'\d{8}_\d{6}', name):
                ds.append((name, p))
    except OSError:
        return 0
    ds.sort()
    removed = 0
    for _name, p in ds[:-keep]:
        shutil.rmtree(p, ignore_errors=True)
        removed += 1
    return removed


# ----------------------------------------------------------------------------
# 会话标记
# ----------------------------------------------------------------------------
def write_marker(net_dir, game_name):
    data = {
        'computer': socket.gethostname(),
        'user': os.environ.get('USERNAME', ''),
        'game': game_name,
        'time': datetime.now().isoformat(timespec='seconds'),
    }
    try:
        with open(os.path.join(net_dir, MARKER_NAME), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError as e:
        LOG.warning('写会话标记失败 %s: %s', net_dir, e)


def has_marker(net_dir):
    return os.path.isfile(os.path.join(net_dir, MARKER_NAME))


# ----------------------------------------------------------------------------
# 归档模式（zip 快照）
# ----------------------------------------------------------------------------
def safe_prefix(game_name, idx):
    base = re.sub(r'[\\/:*?"<>|\s]+', '_', game_name).strip('_') or 'game'
    return f'{base}.{idx:02d}'


def list_archives(net_dir, prefix):
    try:
        names = os.listdir(net_dir)
    except OSError:
        return []
    pat = re.compile(re.escape(prefix) + r'_(\d{8}_\d{6})\.zip$', re.IGNORECASE)
    found = []
    for n in names:
        m = pat.fullmatch(n)
        if m and os.path.isfile(os.path.join(net_dir, n)):
            found.append((m.group(1), os.path.join(net_dir, n)))
    found.sort(reverse=True)
    return found


def archive_restore(pair, game, idx, exc, dry):
    local, net = pair['local'], pair['net']
    prefix = safe_prefix(game['name'], idx)
    zips = list_archives(net, prefix)
    say(f'\n---- 恢复：归档 {prefix}_*.zip → 本地 ----')
    if not zips:
        local_has = bool(scan_tree(local, exc)) if os.path.isdir(local) else False
        if local_has:
            say('  尚无网络归档；本地已有文件，退出后将创建首个归档。')
        else:
            say('  尚无网络归档，跳过恢复。')
        return True
    say(f'  可用归档 {len(zips)} 份，最新: {os.path.basename(zips[0][1])}')
    if dry:
        return True
    chosen = None
    for _ts, z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                if zf.testzip() is None:
                    chosen = z
                    break
            say(f'  [警告] 归档损坏，尝试更早的: {os.path.basename(z)}')
        except (zipfile.BadZipFile, OSError) as e:
            LOG.warning('归档读取失败 %s: %s', z, e)
    if not chosen:
        say('  [错误] 所有归档都无法读取。')
        return False
    try:
        with zipfile.ZipFile(chosen) as zf:
            for n in zf.namelist():
                nn = n.replace('\\', '/')
                if nn.startswith('/') or '..' in nn.split('/'):
                    say(f'  [错误] 归档包含可疑路径，拒绝恢复: {n}')
                    return False
            n_files = sum(1 for i in zf.infolist() if not i.is_dir())
            zf.extractall(local)
    except (zipfile.BadZipFile, OSError) as e:
        say(f'  [错误] 解压失败: {e}')
        return False
    say(f'  已恢复 {n_files} 个文件 ← {os.path.basename(chosen)}')
    return True


def archive_push(pair, game, idx, exc, dry):
    local, net = pair['local'], pair['net']
    prefix = safe_prefix(game['name'], idx)
    say(f'\n---- 推送：本地 → 归档 {prefix}_*.zip ----')
    items = sorted(scan_tree(local, exc).items())
    sizes = [(rel, v[0]) for rel, v in items]
    total = sum(sz for _, sz in sizes)
    say(f'  计划：打包 {len(items)} 个文件（{human(total)}）')
    preview_list('将打包', sizes)
    if dry:
        return True
    if not items:
        say('  本地无文件，跳过打包。')
        return True
    try:
        os.makedirs(net, exist_ok=True)
    except OSError as e:
        say(f'  [错误] 网络目录不可用: {e}')
        return False
    ts = now_ts()
    final = os.path.join(net, f'{prefix}_{ts}.zip')
    while os.path.exists(final):        # 同一秒内重复推送：错开 1 秒，避免覆盖旧快照
        time.sleep(1)
        ts = now_ts()
        final = os.path.join(net, f'{prefix}_{ts}.zip')
    tmp = final + '.tmp'
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for i, (rel, _sz) in enumerate(items, 1):
                zf.write(os.path.join(local, rel), rel)
                if i % 500 == 0:
                    say(f'  打包进度 {i}/{len(items)}')
        os.replace(tmp, final)  # 写完再改名：中断不会留下半截归档
    except OSError as e:
        say(f'  [错误] 打包失败: {e}')
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    say(f'  已创建归档 {os.path.basename(final)}')
    keep = game.get('backup_keep', 10)
    archives = list_archives(net, prefix)
    removed = 0
    if keep > 0:
        for _ts2, z in archives[keep:]:
            try:
                os.remove(z)
                removed += 1
            except OSError:
                pass
    if removed:
        say(f'  已清理 {removed} 个过期归档（保留 {keep} 份）')
    return True


# ----------------------------------------------------------------------------
# 锁：跨机器互斥 + 本机单实例
# ----------------------------------------------------------------------------
class NetLock:
    def __init__(self, net_dir):
        self.path = os.path.join(net_dir, LOCK_NAME)

    def acquire(self, bridge):
        for _attempt in range(3):
            try:
                os.makedirs(self.path)  # 已存在则抛 FileExistsError
            except FileExistsError:
                info = self._read() or {}
                # 本机遗留的陈旧锁（上次异常退出、pid 已死）→ 自动清理，不打扰
                if info.get('computer') == socket.gethostname() and self._local_pid_dead(info):
                    say(f'[提示] 检测到本机上次异常退出遗留的同步锁（pid {info.get("pid", "?")} 已不存在），自动清理。')
                    if not self._force_takeover():
                        return False
                    continue
                idx = bridge.ask_option(
                    '同步锁', f'[警告] 该游戏的网络同步锁被占用：{self._holder_text(info)}',
                    ['强制接管（确认没有其他机器正在同步）', '退出'], default_index=1)
                if idx != 0:
                    return False
                if not self._force_takeover():
                    return False
                continue
            except OSError as e:
                say(f'[错误] 无法创建同步锁: {e}')
                return False
            info = {
                'computer': socket.gethostname(),
                'user': os.environ.get('USERNAME', ''),
                'pid': os.getpid(),
                'game': CURRENT_GAME,
                'time': datetime.now().isoformat(timespec='seconds'),
            }
            try:
                with open(os.path.join(self.path, 'info.json'), 'w', encoding='utf-8') as f:
                    json.dump(info, f, ensure_ascii=False)
            except OSError:
                pass
            return True
        say('[错误] 无法获取同步锁。')
        return False

    def _holder_text(self, info):
        if not info:
            return '（无法读取锁信息）'
        host = str(info.get('computer', '?'))
        if host == socket.gethostname():
            host += '（本机）'
        return f"{host}/{info.get('user', '?')}，游戏[{info.get('game', '?')}]，{info.get('time', '?')}"

    def _local_pid_dead(self, info):
        """锁归属本机时判断持锁进程是否还活着（pid 缺失/已退出/被无关程序复用=陈旧）"""
        try:
            pid = int(info.get('pid') or 0)
        except (TypeError, ValueError):
            return True
        if not pid:
            return True
        if psutil is None:
            return False
        try:
            name = (psutil.Process(pid).name() or '').lower()
        except psutil.NoSuchProcess:
            return True
        except psutil.Error:
            return False
        # 进程活着且名字像云档/python → 可能真在运行，交给用户确认
        return not ('云档' in name or name.startswith('python'))

    def _force_takeover(self):
        """清掉旧锁目录。SMB 上删除常因句柄延迟/只读属性而失败：
        先带重试删除（并清只读属性），仍失败则把旧锁改名让路（rename 往往仍可用），再尽力清理。"""
        last_err = None
        for _i in range(4):
            try:
                shutil.rmtree(self.path)
                return True
            except FileNotFoundError:
                return True
            except OSError as e:
                last_err = e
                try:  # 清只读属性后重试
                    os.chmod(self.path, stat.S_IWRITE)
                    for dp, _dns, fns in os.walk(self.path):
                        os.chmod(dp, stat.S_IWRITE)
                        for n in fns:
                            os.chmod(os.path.join(dp, n), stat.S_IWRITE)
                except OSError:
                    pass
                time.sleep(1)
        stale = f'{self.path}.stale.{os.getpid()}.{int(time.time())}'
        try:
            os.rename(self.path, stale)
        except OSError as e:
            say(f'[错误] 强制接管失败：旧锁目录删不掉也移不走。\n'
                f'  锁位置: {self.path}\n  删除失败原因: {last_err}\n  改名失败原因: {e}\n'
                f'  请确认持锁机器的云档已退出、共享目录有删除权限，或手动删除该目录后重试。')
            return False
        say('[提示] 旧锁目录已改名让路（不影响本次同步），正在清理…')
        try:
            shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            pass
        return True

    def _read(self):
        try:
            with open(os.path.join(self.path, 'info.json'), 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def release(self):
        shutil.rmtree(self.path, ignore_errors=True)


_local_guard_path = None


def local_guard():
    """本机单实例防双开（按程序所在目录区分）。"""
    global _local_guard_path
    if psutil is None:
        return
    me = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
    key = hashlib.md5(os.path.abspath(me).lower().encode('utf-8', 'replace')).hexdigest()[:12]
    path = os.path.join(tempfile.gettempdir(), f'云档_{key}.pid')
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                p = psutil.Process(os.getpid())
                create_time = p.create_time()
            except psutil.Error:
                create_time = 0.0
            os.write(fd, f'{os.getpid()} {create_time}'.encode())
            os.close(fd)
            _local_guard_path = path
            atexit.register(_local_guard_release)
            return
        except FileExistsError:
            try:
                with open(path, 'r') as f:
                    pid_s, ct_s = (f.read().split() + ['0'])[:2]
                pid, ct = int(pid_s), float(ct_s)
            except (OSError, ValueError):
                pid, ct = 0, 0.0
            alive = False
            if pid:
                try:
                    p = psutil.Process(pid)
                    alive = p.is_running() and abs(p.create_time() - ct) < 1.0
                except psutil.Error:
                    alive = False
            if alive:
                die(f'本程序已在运行（pid {pid}）。如确认误报，请删除 {path}', EXIT_PRECHECK)
            try:
                os.remove(path)
            except OSError:
                pass
    die(f'无法创建本机实例锁: {path}', EXIT_PRECHECK)


def _local_guard_release():
    if _local_guard_path:
        try:
            os.remove(_local_guard_path)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# 进程管理
# ----------------------------------------------------------------------------
def find_running(names):
    want = {n.lower() for n in names if n}
    if not want:
        return []
    hits = []
    for p in psutil.process_iter(['name']):
        try:
            nm = (p.info.get('name') or '').lower()
            if nm in want:
                hits.append(nm)
        except psutil.Error:
            pass
    return hits


def count_named(names):
    want = {n.lower() for n in names if n}
    c = 0
    for p in psutil.process_iter(['name']):
        try:
            if (p.info.get('name') or '').lower() in want:
                c += 1
        except psutil.Error:
            pass
    return c


def wait_for_start(names, timeout):
    """返回 'started' | 'timeout' | 'stopped'（用户请求提前结束）"""
    t0 = time.time()
    while True:
        if count_named(names) > 0:
            return 'started'
        c = _ACTIVE['control']
        if c is not None and c.stop_wait.is_set():
            return 'stopped'
        if time.time() - t0 >= timeout:
            return 'timeout'
        time.sleep(POLL_SEC)
        el = int(time.time() - t0)
        if el % 15 == 0 and el > 0:
            say(f'  ... 等待进程出现已 {el} 秒')


def wait_for_exit_names(names):
    last_hb = time.time()
    while count_named(names) > 0:
        _check_abort()
        c = _ACTIVE['control']
        if c is not None and c.stop_wait.is_set():
            return
        time.sleep(POLL_SEC)
        if time.time() - last_hb >= 3600:
            last_hb = time.time()
            LOG.info('仍在等待进程退出: %s', ', '.join(names))


def wait_for_exit_tree(root_pid):
    """跟踪根进程及其全部子孙进程，全部退出后返回。若 15 秒内全退返回 True（疑似未启动成功）。"""
    t0 = time.time()
    last_change = time.time()
    tracked = {root_pid}
    while tracked:
        _check_abort()
        c = _ACTIVE['control']
        if c is not None and c.stop_wait.is_set():
            return False
        time.sleep(POLL_SEC)
        still = set()
        for pid in tracked:
            try:
                p = psutil.Process(pid)
                still.add(pid)
                try:
                    still.update(c2.pid for c2 in p.children(recursive=False))
                except psutil.Error:
                    pass
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                still.add(pid)
        if still != tracked:
            tracked = still
            last_change = time.time()
        if not tracked and time.time() - t0 <= 15:
            return True
        if time.time() - last_change >= 3600:
            last_change = time.time()
            LOG.info('进程树仍在运行（已等待 1 小时以上）')
    return time.time() - t0 <= 15


def split_args(s):
    s = (s or '').strip()
    if not s:
        return []
    try:
        toks = shlex.split(s, posix=False)
    except ValueError:
        return s.split()
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'"):
            t = t[1:-1]
        out.append(t)
    return out


def launch_game(game, dry):
    """返回 ('tree', pid) | ('names', [进程名]) | None(dry) | 'FAIL'"""
    exe = game['exe']
    if dry:
        say('  [演练] 跳过启动。')
        return None
    say(f'启动程序: {exe}')
    workdir = game.get('workdir') or os.path.dirname(exe) or '.'
    argsl = split_args(game.get('args'))
    flags = 0
    if os.name == 'nt':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        p = subprocess.Popen([exe] + argsl, cwd=workdir, creationflags=flags)
        return ('tree', p.pid)
    except OSError as e:
        say(f'  直接启动失败 ({e})，改用资源管理器方式启动（可能弹出 UAC 提权窗口）...')
        try:
            os.startfile(exe)  # ShellExecute：兼容 .lnk 与需管理员权限的程序
            names = list(game.get('process') or []) or [os.path.basename(exe)]
            return ('names', names)
        except OSError as e2:
            say(f'  [错误] 无法启动: {e2}')
            return 'FAIL'


# ----------------------------------------------------------------------------
# 游玩统计
# ----------------------------------------------------------------------------
STATS_HEADER = ['日期', '游戏', 'ID', '开始', '结束', '分钟']


def append_stats(csv_path, game, start_dt, end_dt):
    minutes = max(0, int((end_dt - start_dt).total_seconds() // 60))
    newfile = not os.path.isfile(csv_path)
    try:
        with open(csv_path, 'a', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            if newfile:
                w.writerow(STATS_HEADER)
            w.writerow([start_dt.date().isoformat(), game['name'], str(game.get('id') or ''),
                        start_dt.strftime('%H:%M:%S'), end_dt.strftime('%H:%M:%S'), minutes])
    except OSError as e:
        LOG.warning('写统计失败: %s', e)
    return minutes


def load_stats(csv_path):
    rows = []
    if not os.path.isfile(csv_path):
        return rows
    try:
        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except OSError:
        pass
    return rows


def week_start():
    today = date.today()
    return today - timedelta(days=today.weekday())  # 本周一


def stats_summary(csv_path, name_to_id=None, id_to_name=None):
    """汇总游玩统计，按隐藏ID归属（无 ID 的旧行能按名称归入同名游戏）：
    {'week_start': 日期, 'totals': {key: 分钟}, 'week': {key: 分钟}, 'names': {key: 显示名}}
    key 形如 'i:<ID>'（有 ID）或 'n:<名称>'（无 ID 且无同名游戏）"""
    rows = load_stats(csv_path)
    ws = week_start()
    totals, week, names = {}, {}, {}
    for r in rows:
        gid = (r.get('ID') or '').strip()
        gname = (r.get('游戏') or '?').strip() or '?'
        if gid:
            key = 'i:' + gid
            disp = (id_to_name or {}).get(gid) or gname
        elif name_to_id and gname in name_to_id:
            key = 'i:' + name_to_id[gname]
            disp = (id_to_name or {}).get(name_to_id[gname]) or gname
        else:
            key = 'n:' + gname
            disp = gname
        try:
            m = float(r.get('分钟') or 0)
        except ValueError:
            m = 0.0
        totals[key] = totals.get(key, 0.0) + m
        names.setdefault(key, disp)
        try:
            d = date.fromisoformat(r.get('日期') or '')
        except ValueError:
            continue
        if d >= ws:
            week[key] = week.get(key, 0.0) + m
    return {'week_start': ws, 'totals': totals, 'week': week, 'names': names}


def stats_maps(cfg):
    """从配置取 name→id / id→name 映射（供统计聚合）"""
    name_to_id, id_to_name = {}, {}
    for g in (cfg.get('games') if cfg else None) or []:
        gid = str(g.get('id') or '').strip()
        if gid:
            name_to_id[g['name']] = gid
            id_to_name[gid] = g['name']
    return name_to_id, id_to_name


def game_minutes(csv_path, name, gid=None):
    """返回 (总分钟, 本周分钟)。有隐藏ID按 ID 归属；旧行按名称并入同名游戏"""
    name_to_id, id_to_name = ({name: gid}, {gid: name}) if gid else (None, None)
    s = stats_summary(csv_path, name_to_id, id_to_name)
    key = ('i:' + gid) if gid else ('n:' + name)
    return s['totals'].get(key, 0.0), s['week'].get(key, 0.0)


def week_minutes(csv_path, game_name=None, gid=None):
    if game_name is None and not gid:
        s = stats_summary(csv_path)
        return int(sum(s['week'].values()))
    name_to_id, id_to_name = ({game_name: gid}, {gid: game_name}) if (gid and game_name) else (None, None)
    key = ('i:' + gid) if gid else ('n:' + game_name)
    s = stats_summary(csv_path, name_to_id, id_to_name)
    return int(s['week'].get(key, 0.0))


def migrate_stats_ids(csv_path, cfg):
    """旧版统计 CSV 升级（一次性）：
    1) 补齐 ID 列（旧表头 5 列 → 新表头 6 列）；
    2) 无 ID 的旧行按“游戏名 = 当前配置游戏名”盖上对应游戏的隐藏ID。
    之后改名只改配置、不动 CSV，统计仍按 ID 归属。返回是否有改动。"""
    if not os.path.isfile(csv_path):
        return False
    try:
        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
    except OSError:
        return False
    if not rows:
        return False
    header = [h.strip() for h in rows[0]]
    if header[:1] != [STATS_HEADER[0]]:      # 不认识的格式，不动
        return False
    if not set(header) <= set(STATS_HEADER):
        # 含自定义/未知列（如手工加过“备注”）：不理解的结构不迁移，
        # 否则重写会整列丢弃用户数据
        LOG.warning('统计 CSV 含未知列 %r，跳过自动迁移', header)
        return False
    name_to_id = {}
    for g in cfg['games']:
        gid = str(g.get('id') or '').strip()
        if gid:
            name_to_id[g['name']] = gid

    def col(nm):
        return header.index(nm) if nm in header else -1

    c_date, c_name, c_id = col('日期'), col('游戏'), col('ID')
    c_start, c_end, c_min = col('开始'), col('结束'), col('分钟')
    if any(c < 0 for c in (c_date, c_name, c_start, c_end, c_min)):
        return False   # 核心列不全：不动（ID 列允许缺失=旧格式）
    changed = (rows[0] != STATS_HEADER)
    out = [STATS_HEADER]
    for r in rows[1:]:
        if not r or not any(str(c).strip() for c in r):
            continue
        get = lambda i: (str(r[i]).strip() if 0 <= i < len(r) else '')
        gid, name = get(c_id), get(c_name)
        if not gid and name in name_to_id:
            gid = name_to_id[name]
            changed = True
        out.append([get(c_date), name, gid, get(c_start), get(c_end), get(c_min)])
    if not changed:
        return False
    tmp = csv_path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
            csv.writer(f).writerows(out)
        os.replace(tmp, csv_path)
    except OSError as e:
        LOG.warning('统计 CSV 迁移失败: %s', e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    LOG.info('统计 CSV 已升级为带隐藏ID格式')
    return True


def report_stats(csv_path, cfg=None):
    name_to_id, id_to_name = stats_maps(cfg)
    s = stats_summary(csv_path, name_to_id or None, id_to_name or None)
    totals, week, names, ws = s['totals'], s['week'], s['names'], s['week_start']
    say('')
    say('== 游玩时长统计 ==')
    if not totals:
        say('暂无记录。')
        return
    say(f'  {"游戏":<12}{"累计(小时)":>12}{"本周(小时)":>12}')
    gt = gw = 0.0
    for key in sorted(totals, key=lambda k: names.get(k, k)):
        gt += totals[key]
        w = week.get(key, 0.0)
        gw += w
        say(f'  {names.get(key, "?"):<12}{totals[key] / 60:>12.1f}{w / 60:>12.1f}')
    say(f'  {"合计":<12}{gt / 60:>12.1f}{gw / 60:>12.1f}')
    say(f'  （本周自 {ws.isoformat()} 起）')


# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
def default_config_path():
    return os.path.join(APP_DIR, '云档.toml')


def norm_dir(p):
    p = str(p).strip().strip('"')
    while len(p) > 3 and p.endswith(('\\', '/')):
        p = p[:-1]
    return p


def _contains(parent, child):
    p = os.path.normcase(os.path.normpath(parent))
    c = os.path.normcase(os.path.normpath(child))
    return c == p or c.startswith(p.rstrip('\\/') + os.sep)


def new_game_id():
    """游戏隐藏ID：用于统计归属，与名称无关（改名不丢统计）"""
    return secrets.token_hex(5)


def ensure_game_ids(cfg):
    """给缺少隐藏ID的游戏补发ID（去重）。有改动返回 True。"""
    changed = False
    seen = set()
    for g in cfg['games']:
        gid = str(g.get('id') or '').strip()
        if not gid or gid in seen:
            gid = new_game_id()
            while gid in seen:
                gid = new_game_id()
            g['id'] = gid
            changed = True
        elif g.get('id') != gid:
            g['id'] = gid
            changed = True
        seen.add(gid)
    return changed


def _cfg_int(value, default, key=''):
    """容错读取整型配置：手滑写错（如 backup_keep = 'ten'）不再裸抛异常，
    回退默认值并在日志留痕。字符串数字（'20'）/浮点（20.5、'20.5'）均可接受。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        LOG.warning('配置项 %s 数值非法 %r，已回退默认值 %r', key, value, default)
        return default


def _cfg_str_list(value, key=''):
    """容错读取字符串列表：手写成单个字符串（process = 'a.exe'）时按
    单元素列表处理，不再被逐字符拆开；None → []。"""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out = [str(x).strip() for x in value if str(x).strip()]
        return out
    LOG.warning('配置项 %s 应为字符串列表，收到 %r，已忽略', key, value)
    return []


def normalize_config(data):
    g = data.get('general') or {}
    if not isinstance(g, dict):
        LOG.warning('配置 [general] 应为表而非 %r，已忽略', g)
        g = {}
    cfg = {
        'backup_keep': _cfg_int(g.get('backup_keep', 10), 10, 'general.backup_keep'),
        'timeout_launch': _cfg_int(g.get('timeout_launch', 120), 120, 'general.timeout_launch'),
        'net_wait': _cfg_int(g.get('net_wait', 60), 60, 'general.net_wait'),
        # 统计 CSV 固定在程序目录（与 云档.toml/云档.log 同级），不再作为配置项
        'stats_csv': os.path.join(APP_DIR, '云档统计.csv'),
        'games': [],
    }
    games = data.get('game') or []
    if isinstance(games, dict):
        games = [games]
    elif not isinstance(games, (list, tuple)):
        LOG.warning('配置 [game] 应为表或表数组而非 %r，已忽略', games)
        games = []
    for gd in games:
        if not isinstance(gd, dict):
            LOG.warning('配置 game 数组内含非表项 %r，已忽略', gd)
            continue
        pairs_in = gd.get('sync') or []
        if isinstance(pairs_in, dict):
            pairs_in = [pairs_in]
        elif not isinstance(pairs_in, (list, tuple)):
            LOG.warning('配置 sync 应为表数组，已忽略（收到 %r）', pairs_in)
            pairs_in = []
        pairs = []
        for p in pairs_in:
            if isinstance(p, dict):
                pairs.append({'local': norm_dir(p.get('local', '')), 'net': norm_dir(p.get('net', ''))})
        cfg['games'].append({
            'id': str(gd.get('id') or '').strip(),
            'name': str(gd.get('name') or '未命名'),
            'exe': str(gd.get('exe') or '').strip(),
            'args': str(gd.get('args') or ''),
            'workdir': norm_dir(gd.get('workdir') or ''),
            'process': _cfg_str_list(gd.get('process'), 'game.process'),
            'mode': str(gd.get('mode') or 'timestamp').strip().lower(),
            'exclude': _cfg_str_list(gd.get('exclude'), 'game.exclude'),
            'backup_keep': _cfg_int(gd.get('backup_keep', cfg['backup_keep']),
                                    cfg['backup_keep'], 'game.backup_keep'),
            'cover': str(gd.get('cover') or ''),
            'sync': pairs,
        })
    return cfg


def validate_config(cfg):
    errs = []
    for g in cfg['games']:
        n = g['name']
        if g['mode'] not in MODES:
            errs.append(f'[{n}] mode 必须是 {"/".join(MODES)}，当前为 {g["mode"]!r}')
        if not g['sync']:
            errs.append(f'[{n}] 未配置 sync 目录对。')
        for i, p in enumerate(g['sync'], 1):
            if not p['local']:
                errs.append(f'[{n}] 第{i}对: local 为空。')
            if not p['net']:
                errs.append(f'[{n}] 第{i}对: net 为空。')
            if p['local'] and p['net'] and (_contains(p['local'], p['net']) or _contains(p['net'], p['local'])):
                errs.append(f'[{n}] 第{i}对: 本地目录与网络目录相同或互为嵌套。')
        for a in range(len(g['sync'])):
            for b in range(a + 1, len(g['sync'])):
                la, na = g['sync'][a]['local'], g['sync'][a]['net']
                lb, nb = g['sync'][b]['local'], g['sync'][b]['net']
                if na and nb and (_contains(na, nb) or _contains(nb, na)):
                    errs.append(f'[{n}] 第{a + 1}与第{b + 1}对: 网络目录互相嵌套。')
                if la and lb and (_contains(la, lb) or _contains(lb, la)):
                    errs.append(f'[{n}] 第{a + 1}与第{b + 1}对: 本地目录互相嵌套。')
        if g['mode'] == 'archive' and not g['process'] and g['exe'].lower().endswith('.lnk'):
            errs.append(f'[{n}] lnk 启动且为归档模式时，建议配置 process 列表用于等待。')
    if errs:
        # 抛 SessionError 而非 sys.exit：GUI 侧可捕获后弹窗提示（sys.exit 会被 Tk 静默吞掉）
        raise SessionError('\n'.join(f'[配置错误] {e}' for e in errs), EXIT_CONFIG)


def load_config(path):
    if tomllib is None:
        die('需要 Python 3.11+（内置 tomllib），或 pip install tomli。', EXIT_CONFIG)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    except SessionError:
        raise
    except Exception as e:
        die(f'读取配置失败 {path}: {e}', EXIT_CONFIG)
    try:
        cfg = normalize_config(data)
        validate_config(cfg)
    except SessionError:
        raise                       # validate_config 的明确错误原样上抛
    except Exception as e:
        # 解析阶段的任何意外异常（数值/类型手滑等）统一包装为明确提示，
        # 不再让裸 traceback 穿透（CLI 刷屏、GUI 静默闪退）
        die(f'配置解析失败 {path}: {type(e).__name__}: {e}', EXIT_CONFIG)
    return cfg


def _q(s):
    """TOML 字符串。注意：单引号字面量（'...'）不支持 '' 转义，
    旧写法遇到名称含单引号时会生成无法解析的配置；此类值改用转义的基本字符串"""
    s = str(s)
    if "'" not in s and '\n' not in s and '\r' not in s:
        return "'" + s + "'"
    return json.dumps(s, ensure_ascii=False)


def save_toml(path, cfg):
    L = []
    L.append('# 云档 配置文件（由向导生成，可手工编辑）')
    L.append('# mode 可选: timestamp=时间戳 / mirror=镜像 / archive=归档(zip快照)')
    L.append('')
    L.append('[general]')
    L.append(f'backup_keep = {cfg["backup_keep"]}   # 推送前备份保留份数（归档模式下同时是 zip 保留份数，0=不清理）')
    L.append(f'timeout_launch = {cfg["timeout_launch"]}   # 等待进程出现的超时秒数')
    L.append(f'net_wait = {cfg["net_wait"]}   # 网络目录不可达时的等待秒数')
    L.append('')
    for g in cfg['games']:
        L.append('[[game]]')
        L.append(f'name = {_q(g["name"])}')
        if g.get('id'):
            L.append(f'id = {_q(g["id"])}   # 隐藏ID（自动生成，统计归属用；请勿修改或删除）')
        L.append(f'exe = {_q(g["exe"])}')
        if g.get('args'):
            L.append(f'args = {_q(g["args"])}')
        if g.get('workdir'):
            L.append(f'workdir = {_q(g["workdir"])}')
        if g.get('process'):
            L.append('process = [' + ', '.join(_q(x) for x in g['process']) + ']')
        L.append(f'mode = {_q(g["mode"])}')
        if g.get('cover'):
            L.append(f'cover = {_q(g["cover"])}')
        if g.get('exclude'):
            L.append('exclude = [' + ', '.join(_q(x) for x in g['exclude']) + ']')
        if g.get('backup_keep') != cfg['backup_keep']:
            L.append(f'backup_keep = {g["backup_keep"]}')
        L.append('sync = [')
        for p in g['sync']:
            L.append(f'  {{ local = {_q(p["local"])}, net = {_q(p["net"])} }},')
        L.append(']')
        L.append('')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))


# ----------------------------------------------------------------------------
# 配置向导
# ----------------------------------------------------------------------------
def ask_dir(prompt):
    while True:
        d = ask(prompt)
        if d is None:
            die('输入已结束，操作中止。', EXIT_ABORT)
        d = norm_dir(d)
        if not d:
            say('  不能为空。')
            continue
        if os.path.isdir(d):
            return d
        say('  目录不存在。')


def wizard(path):
    say('')
    say('=' * 46)
    say('             云 档 配 置 向 导')
    say('=' * 46)
    if os.path.isfile(path):
        c = ask('配置文件已存在，重新生成将覆盖，继续? [y/N]: ', 'n')
        if c.lower() not in ('y', 'yes'):
            say('已取消。')
            return None
    games = []
    while True:
        say('')
        say(f'---- 第 {len(games) + 1} 个游戏 ----')
        exe = ask('1. 程序完整路径（exe/lnk，留空=仅同步不启动）: ').strip().strip('"')
        while exe and not os.path.isfile(exe):
            say('  文件不存在，请重新输入。')
            exe = ask('   程序完整路径: ').strip().strip('"')
        defname = os.path.splitext(os.path.basename(exe))[0] if exe else ''
        name = ask(f'2. 游戏名称 [{defname or "游戏"}]: ', defname or '游戏')
        say('3. 同步模式: 1.时间戳(两端保留最新,默认)  2.镜像(完全一致,删除多余)  3.归档(网络端存zip快照)')
        mode = None
        while not mode:
            m = ask('   选择 [1/2/3]: ', '1')
            mode = {'1': 'timestamp', '2': 'mirror', '3': 'archive'}.get(m)
            if not mode:
                say('  请输入 1/2/3。')
        cnt = ask_int('4. 同步目录对数 [1]: ', 1, 1, 50)
        pairs = []
        for i in range(1, cnt + 1):
            say(f'  ---- 第 {i} 对目录 ----')
            loc = ask_dir('   本地目录: ')
            net = None
            while net is None:
                cand = ask('   网络目录: ')
                if cand is None:
                    die('输入已结束，操作中止。', EXIT_ABORT)
                cand = norm_dir(cand)
                if not cand:
                    say('  不能为空。')
                    continue
                if not os.path.isdir(cand):
                    c = ask('   网络目录不存在，是否创建? [y/N]: ', 'n')
                    if c.lower() in ('y', 'yes'):
                        try:
                            os.makedirs(cand, exist_ok=True)
                        except OSError as e:
                            say(f'   创建失败: {e}')
                            continue
                    else:
                        continue
                net = cand
            pairs.append({'local': loc, 'net': net})
        excl_raw = ask('5. 排除规则（逗号分隔，可空，如 *.tmp, shadercache/）: ')
        excl = [x.strip() for x in excl_raw.replace('，', ',').split(',') if x.strip()]
        games.append({'name': name, 'exe': exe, 'args': '', 'workdir': '', 'process': [],
                      'mode': mode, 'exclude': excl, 'backup_keep': 10, 'sync': pairs})
        c = ask('\n继续添加下一个游戏? [y/N]: ', 'n')
        if c.lower() not in ('y', 'yes'):
            break
    cfg = {'backup_keep': 10, 'timeout_launch': 120, 'net_wait': 60,
           'stats_csv': os.path.join(APP_DIR, '云档统计.csv'), 'games': games}
    try:
        save_toml(path, cfg)
    except OSError as e:
        die(f'保存配置失败: {e}', EXIT_CONFIG)
    say('')
    say(f'配置已保存: {path}')
    say('提示：backup_keep / timeout_launch / process 等高级参数可直接编辑该文件。')
    return load_config(path)


# ----------------------------------------------------------------------------
# 游戏选择
# ----------------------------------------------------------------------------
def choose_game(cfg, want):
    games = cfg['games']
    if want:
        exact = [g for g in games if g['name'] == want]
        cand = exact or [g for g in games if want.lower() in g['name'].lower()]
        if not cand:
            die(f'找不到名称包含「{want}」的游戏（可用 --list 查看）。', EXIT_CONFIG)
        if len(cand) == 1:
            return cand[0]
        games = cand
    if len(games) == 1:
        return games[0]
    while True:
        say('')
        say('请选择要启动的游戏：')
        for i, g in enumerate(games, 1):
            say(f'  {i}. {g["name"]}  [{MODE_LABEL.get(g["mode"], g["mode"])}]  ({g["exe"] or "仅同步"})')
        say('  q. 退出')
        s = ask('输入编号: ')
        if s is None:
            die('已取消。', EXIT_ABORT)
        if s.lower() == 'q':
            die('已取消。', EXIT_ABORT)
        if s.isdigit() and 1 <= int(s) <= len(games):
            return games[int(s) - 1]
        m = [g for g in games if s.lower() in g['name'].lower()]
        if len(m) == 1:
            return m[0]
        say('  输入无效，请重试。')


def list_games(cfg):
    say('')
    say(f'== 已配置 {len(cfg["games"])} 个游戏 ==')
    for i, g in enumerate(cfg['games'], 1):
        say(f'  {i}. {g["name"]}  [{MODE_LABEL.get(g["mode"], g["mode"])}]')
        say(f'     程序: {g["exe"] or "（仅同步）"}')
        for p in g['sync']:
            say(f'     {p["local"]}')
            say(f'      ↔ {p["net"]}')


# ----------------------------------------------------------------------------
# 会话主流程
# ----------------------------------------------------------------------------
CURRENT_GAME = '?'


def precheck(cfg, game, launching, dry, bridge):
    # 1) 目录检查与网络可达性
    for i, pair in enumerate(game['sync'], 1):
        loc, net = pair['local'], pair['net']
        if not os.path.isdir(loc):
            if dry:
                say(f'[预检] 第{i}对本地目录不存在: {loc}')
            else:
                idx = bridge.ask_option(
                    '目录检查', f'[预检] 第{i}对本地目录不存在：\n{loc}',
                    ['创建该目录', '中止'], default_index=1)
                if idx != 0:
                    raise SessionError(f'本地目录不存在: {loc}', EXIT_PRECHECK)
                try:
                    os.makedirs(loc, exist_ok=True)
                except OSError as e:
                    raise SessionError(f'创建本地目录失败: {e}', EXIT_PRECHECK)
        if dry:
            if not os.path.isdir(net):
                say(f'[预检] 第{i}对网络目录不可达: {net}')
            continue
        t0 = time.time()
        while not os.path.isdir(net):
            waited = int(time.time() - t0)
            if waited >= cfg['net_wait']:
                raise SessionError(f'网络目录不可达（已等待 {waited} 秒）: {net}', EXIT_PRECHECK)
            say(f'[预检] 网络目录暂不可达，{cfg["net_wait"]} 秒内重试... ({waited}s)')
            time.sleep(3)
    # 2) 目标进程已在运行？
    if not dry:
        names = list(game.get('process') or [])
        if not names and game['exe']:
            names = [os.path.basename(game['exe'])]
        hits = find_running(names)
        if hits:
            raise SessionError(f'检测到 {",".join(sorted(set(hits)))} 已在运行，请先退出再运行本程序。', EXIT_PRECHECK)
    # 3) 磁盘空间
    for i, pair in enumerate(game['sync'], 1):
        loc = pair['local']
        drive = os.path.splitdrive(os.path.abspath(loc))[0] + os.sep
        try:
            free = shutil.disk_usage(drive).free
        except OSError:
            continue
        if free < 1 << 30:
            raise SessionError(f'第{i}对本地目录所在磁盘剩余空间不足 1GB: {drive}（{human(free)}）', EXIT_PRECHECK)
        try:
            nfree = shutil.disk_usage(os.path.abspath(pair['net'])).free
            if nfree < 2 << 30:
                say(f'[预检][警告] 第{i}对网络目录所在磁盘剩余空间较小: {human(nfree)}')
        except OSError:
            pass


def run_session(cfg, game, dry_run=False, sync_only=False, bridge=None, control=None, reporter=None, direction='both'):
    """执行一次完整会话。CLI/GUI 共用：
    - bridge: 交互确认桥（缺省 ConsoleBridge）
    - control: 取消/中止信号（缺省 None=不可取消）
    - reporter: 输出桥（缺省 None=打印+写日志）
    - direction: 'both'=恢复+推送（默认，可启动游戏）；'down'=仅恢复（网络→本地）；'up'=仅推送（本地→网络）
    """
    global CURRENT_GAME
    CURRENT_GAME = game['name']
    if bridge is None:
        bridge = ConsoleBridge()
    prev_active = dict(_ACTIVE)
    _ACTIVE['reporter'] = reporter
    _ACTIVE['control'] = control
    try:
        return _session_body(cfg, game, dry_run, (not sync_only) and direction == 'both', bridge, direction)
    finally:
        _ACTIVE.update(prev_active)


def _session_body(cfg, game, dry, launching, bridge, direction='both'):
    code = EXIT_OK

    if launching and not game['exe'] and not dry:
        raise SessionError('该游戏未配置 exe，无法启动。如只需同步请使用仅同步模式。', EXIT_CONFIG)
    if launching and not dry and game['exe'].lower().endswith('.lnk') and not game.get('process'):
        raise SessionError('使用 .lnk 启动时必须在配置中填写 process 列表（用于判断游戏退出）。', EXIT_CONFIG)

    if dry:
        say('')
        say('=' * 46)
        say('  DRY-RUN 演练模式：不会写入/删除任何文件')
        say('=' * 46)

    # ---- 预检 ----
    notify_phase('预检')
    say('')
    say('== 预检 ==')
    precheck(cfg, game, launching, dry, bridge)
    say('  通过。')
    _check_abort()

    # ---- 跨机器互斥锁 ----
    lock = None
    if not dry:
        lock = NetLock(game['sync'][0]['net'])
        notify_phase('锁定')
        say('')
        say('== 获取同步锁 ==')
        if not lock.acquire(bridge):
            return EXIT_PRECHECK
        say('  已锁定。')

    try:
        eff = game['mode']
        # ---- 镜像模式：会话标记检查 ----
        if eff == 'mirror':
            missing = [p['net'] for p in game['sync'] if not has_marker(p['net'])]
            real_missing = []
            for net_dir in missing:
                if scan_tree(net_dir, make_exclude(game)):
                    real_missing.append(net_dir)
                else:
                    say(f'[提示] {net_dir} 为空目录，视为首次使用。')
            if real_missing:
                say('[警告] 以下网络目录缺少“上次成功同步”标记（上次会话可能未正常结束）:')
                for m in real_missing:
                    say(f'  {m}')
                if dry:
                    say('  [演练] 实际运行时将要求确认是否继续镜像。')
                else:
                    idx = bridge.ask_option(
                        '会话标记检查',
                        '[警告] 以下网络目录缺少“上次成功同步”标记（上次会话可能未正常结束）：\n' +
                        '\n'.join(f'  {m}' for m in real_missing),
                        ['仍按镜像恢复', '本次改用时间戳模式', '退出'], default_index=2)
                    if idx == 1:
                        eff = 'timestamp'
                        say('  本次按时间戳模式执行。')
                    elif idx != 0:
                        return EXIT_ABORT

        # ---- 恢复阶段（down / both）----
        exc = make_exclude(game)
        ok = True
        if direction in ('both', 'down'):
            notify_phase('恢复', MODE_LABEL.get(eff, eff))
            say('')
            say('=' * 46)
            say(f'  开始同步 [{MODE_LABEL.get(eff, eff)}]：{game["name"]}')
            say('=' * 46)
            for i, pair in enumerate(game['sync'], 1):
                _check_abort()
                if eff == 'archive':
                    # 本地存在比最新归档更新的文件？提示防覆盖
                    if not dry:
                        zips = list_archives(pair['net'], safe_prefix(game['name'], i))
                        if zips and os.path.isdir(pair['local']):
                            zt = os.path.getmtime(zips[0][1])
                            newer = [rel for rel, (_s, mt) in scan_tree(pair['local'], exc).items() if mt > zt + 60]
                            if newer:
                                idx = bridge.ask_option(
                                    '归档恢复确认',
                                    f'[警告] 第{i}对本地存在比最新归档更新的文件（上次可能未归档成功）：{len(newer)} 个。\n'
                                    '从归档恢复会覆盖这些文件。',
                                    ['仍要从归档恢复', '中止'], default_index=1)
                                if idx != 0:
                                    return EXIT_ABORT
                    ok = archive_restore(pair, game, i, exc, dry) and ok
                else:
                    ok = sync_pass('restore', pair, eff, exc, game, dry) and ok
            if not ok:
                return EXIT_SYNC

        # ---- 启动与等待 ----
        start_dt = None
        if launching:
            _check_abort()
            notify_phase('启动')
            say('')
            say('=' * 46)
            say('  启动程序')
            say('=' * 46)
            r = launch_game(game, dry)
            if r == 'FAIL':
                return EXIT_LAUNCH
            if r is not None:
                start_dt = datetime.now()
                if r[0] == 'tree':
                    notify_event('launched', {'pid': r[1]})
                    say(f'  已启动 (pid {r[1]})，正在跟踪进程树，等待退出...')
                    notify_phase('等待退出')
                    quick = wait_for_exit_tree(r[1])
                    if quick:
                        say('  [提示] 进程树很快退出——若游戏实际未运行，请检查 exe/参数。')
                else:
                    names = r[1]
                    say(f'  等待 {"、".join(names)} 启动（超时 {cfg["timeout_launch"]} 秒）...')
                    notify_phase('等待启动')
                    res = wait_for_start(names, cfg['timeout_launch'])
                    if res == 'timeout':
                        raise SessionError(f'等待 {"、".join(names)} 启动超时。', EXIT_LAUNCH)
                    if res == 'started':
                        notify_event('launched', {'names': names})
                        say('  检测到进程，等待退出...')
                        notify_phase('等待退出')
                        wait_for_exit_names(names)
                    else:
                        say('  已请求提前结束，跳过等待。')
                end_dt = datetime.now()
                notify_event('exited')
                say(f'  程序已退出（{start_dt.strftime("%H:%M")} ~ {end_dt.strftime("%H:%M")}）。')

        # ---- 推送阶段（up / both）----
        if direction in ('both', 'up'):
            _check_abort()
            notify_phase('推送')
            ok = True
            for i, pair in enumerate(game['sync'], 1):
                _check_abort()
                if eff == 'archive':
                    ok = archive_push(pair, game, i, exc, dry) and ok
                else:
                    ok = sync_pass('push', pair, eff, exc, game, dry) and ok
            if not ok:
                return EXIT_SYNC

        # ---- 统计 ----
        if start_dt and not dry:
            minutes = append_stats(cfg['stats_csv'], game, start_dt, end_dt)
            wk = week_minutes(cfg['stats_csv'], game['name'], game.get('id'))
            say('')
            say(f'  本次游玩 {minutes} 分钟 | 本游戏本周累计 {wk} 分钟')

        if dry:
            say('')
            say('== 演练结束，未改动任何文件 ==')
        else:
            notify_phase('完成')
            say('')
            say('=' * 46)
            say('  全部操作完成')
            say('=' * 46)
            say(f'  日志: {LOG_PATH}')
            say(f'  统计: {cfg["stats_csv"]}')
        return code
    finally:
        if lock is not None:
            lock.release()
            LOG.info('同步锁已释放')


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(prog='云档', description='游戏存档 局域网同步/备份 工具')
    ap.add_argument('-g', '--game', help='按名称选择游戏（支持模糊匹配）')
    ap.add_argument('--sync-only', action='store_true', help='不启动游戏，仅双向同步')
    ap.add_argument('--down', action='store_true', help='仅下载：网络 → 本地（不启动游戏、不推送）')
    ap.add_argument('--up', action='store_true', help='仅上传：本地 → 网络（不启动游戏、不恢复）')
    ap.add_argument('--dry-run', action='store_true', help='演练模式，不写任何文件')
    ap.add_argument('--list', action='store_true', help='列出配置中的游戏')
    ap.add_argument('--stats', action='store_true', help='查看游玩时长统计')
    ap.add_argument('--wizard', action='store_true', help='重新运行配置向导')
    ap.add_argument('-c', '--config', help='配置文件路径（默认: 本程序同目录 云档.toml）')
    ap.add_argument('-v', '--verbose', action='store_true', help='控制台显示 robocopy 详细输出')
    ap.add_argument('--no-pause', action='store_true', help='结束后不暂停（供脚本调用）')
    return ap


def main(argv=None):
    global VERBOSE, NO_PAUSE
    args = build_parser().parse_args(argv)
    VERBOSE = args.verbose
    NO_PAUSE = args.no_pause

    say(f'云档 v{VERSION} —— 游戏存档同步/备份', also_log=False)
    if os.name != 'nt':
        die('本工具仅支持 Windows（依赖 robocopy）。', EXIT_CONFIG)
    if psutil is None:
        die('缺少依赖 psutil，请先执行: pip install psutil', EXIT_CONFIG)

    cpath = args.config or default_config_path()
    if args.wizard or not os.path.isfile(cpath):
        if not args.wizard:
            say(f'未找到配置文件: {cpath}，进入配置向导。')
        cfg = wizard(cpath)
        if cfg is None:
            return EXIT_ABORT
    else:
        cfg = load_config(cpath)

    # 一次性迁移：给游戏补发隐藏ID，并把旧统计 CSV 升级为带 ID 格式
    if cfg is not None and cfg['games']:
        changed = ensure_game_ids(cfg)
        changed = migrate_stats_ids(cfg['stats_csv'], cfg) or changed
        if changed:
            try:
                save_toml(cpath, cfg)
            except OSError as e:
                LOG.warning('隐藏ID迁移后写回配置失败: %s', e)

    if args.list:
        list_games(cfg)
        return EXIT_OK
    if args.stats:
        report_stats(cfg['stats_csv'], cfg)
        return EXIT_OK

    if not cfg['games']:
        die('配置中还没有游戏：请在启动器中“+ 添加游戏”，或删除 云档.toml 后重新配置。', EXIT_CONFIG)

    if args.down and args.up:
        die('--down 与 --up 不能同时使用。', EXIT_CONFIG)
    direction = 'down' if args.down else ('up' if args.up else 'both')
    sync_only = args.sync_only or args.down or args.up

    game = choose_game(cfg, args.game)

    try:
        if not args.dry_run:
            local_guard()
        return run_session(cfg, game, dry_run=args.dry_run, sync_only=sync_only, direction=direction,
                           bridge=ConsoleBridge(), control=SessionControl())
    except SessionError as e:
        say(f'[错误] {e.msg}')
        pause_if_needed()
        return e.code


if __name__ == '__main__':
    try:
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
            if not sys.stdin.isatty():
                sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
        _rc = main()
    except KeyboardInterrupt:
        print(flush=True)
        say('[中断] 已取消。若同步未完成，下次运行会按“未正常结束”处理。')
        _rc = EXIT_ABORT
    except SessionError as e:
        say(f'[错误] {e.msg}')
        pause_if_needed()
        _rc = e.code
    except SystemExit:
        raise
    except Exception:
        LOG.exception('未预期的异常')
        import traceback
        traceback.print_exc()
        pause_if_needed()
        _rc = EXIT_UNEXPECTED
    sys.exit(_rc)
