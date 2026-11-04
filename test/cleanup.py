# -*- coding: utf-8 -*-
"""交付前清理 + 最终文件清单"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

for d in ['build', 'test/loc', 'test/net', 'test/loc_a', 'test/net_a', 'test/net_wiz',
          '__pycache__', 'test/__pycache__']:
    shutil.rmtree(d, ignore_errors=True)
for f in ['test/before.json', 'test/resp1.txt', 'test/resp3.txt', 'test/resp_wiz.txt',
          'test/test_wiz.toml', '云档.log', '云档统计.csv', 'test/demo_stats.csv',
          '云档.spec', '云档GUI.spec', 'dist/云档.log']:
    if os.path.exists(f):
        os.remove(f)
shutil.rmtree(os.path.join(ROOT, 'covers'), ignore_errors=True)
shutil.rmtree(os.path.join(ROOT, 'test', 'covers'), ignore_errors=True)

print('== 清理完成，最终文件 ==')
for dp, dns, fns in os.walk('.'):
    dns[:] = [d for d in dns if d != '__pycache__']
    for fn in sorted(fns):
        p = os.path.join(dp, fn)
        print('%9.1f KB  %s' % (os.path.getsize(p) / 1024, os.path.relpath(p)))
