# -*- coding: utf-8 -*-
"""快照对比工具: snap.py save | snap.py check"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def snap(root):
    s = {}
    for dp, _dns, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            st = os.stat(p)
            s[os.path.relpath(p, BASE).replace('\\', '/')] = [st.st_size, int(st.st_mtime)]
    return s


if __name__ == '__main__':
    cur = {'loc': snap(os.path.join(BASE, 'loc')), 'net': snap(os.path.join(BASE, 'net'))}
    if sys.argv[1] == 'save':
        json.dump(cur, open(os.path.join(BASE, 'before.json'), 'w'))
        print('snapshot saved')
    else:
        prev = json.load(open(os.path.join(BASE, 'before.json')))
        print('NO-CHANGE:', 'PASS' if cur == prev else 'FAIL')
        if cur != prev:
            for side in ('loc', 'net'):
                if cur[side] != prev[side]:
                    only_old = set(prev[side]) - set(cur[side])
                    only_new = set(cur[side]) - set(prev[side])
                    changed = {k for k in set(cur[side]) & set(prev[side]) if cur[side][k] != prev[side][k]}
                    print(side, 'removed:', sorted(only_old), 'added:', sorted(only_new), 'changed:', sorted(changed))
