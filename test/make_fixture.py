# -*- coding: utf-8 -*-
"""创建测试夹具：本地/网络目录 + 可控 mtime 的文件"""
import os, shutil, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
LOC = os.path.join(BASE, 'loc')
NET = os.path.join(BASE, 'net')


def w(path, content, mtime_offset=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    st = os.stat(path)
    os.utime(path, (st.st_atime + mtime_offset, st.st_mtime + mtime_offset))


def clean():
    for d in (LOC, NET):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(LOC, exist_ok=True)
    os.makedirs(NET, exist_ok=True)


if __name__ == '__main__':
    clean()
    now = time.time()
    # a.txt: 网络端更新 100 秒 -> 恢复时应 net->loc
    w(os.path.join(NET, 'a.txt'), 'net-newer\n', +100)
    w(os.path.join(LOC, 'a.txt'), 'loc-old\n', -100)
    # b.txt: 本地端更新 100 秒 -> 推送时应 loc->net
    w(os.path.join(LOC, 'b.txt'), 'loc-newer\n', +100)
    w(os.path.join(NET, 'b.txt'), 'net-old\n', -100)
    # same.txt: 两端一致（复制对齐 mtime）
    w(os.path.join(NET, 'same.txt'), 'same\n', 0)
    st = os.stat(os.path.join(NET, 'same.txt'))
    w(os.path.join(LOC, 'same.txt'), 'same\n', 0)
    os.utime(os.path.join(LOC, 'same.txt'), (st.st_atime, st.st_mtime))
    # 单端独有文件
    w(os.path.join(NET, 'netonly.txt'), 'netonly\n')
    w(os.path.join(LOC, 'loconly.txt'), 'loconly\n')
    # 排除项
    w(os.path.join(NET, 'junk.tmp'), 'tmp\n')
    w(os.path.join(NET, 'shadercache', 'x.bin'), 'bin\n')
    print('fixture ok:', LOC, NET)
