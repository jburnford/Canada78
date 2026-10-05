#!/usr/bin/env python3
"""Fetch the IIIF v3 manifest of each Canadiana sessional-papers volume and keep
one compact TSV per volume: image sequence, printed label, IIIF image-service id.
Thumbnail: {image_id}/full/,300/0/default.jpg ; viewer: https://www.canadiana.ca/view/{tag}/{seq}
Output: registries/external/canadiana/pages/{tag}.tsv   (skips existing files)
"""
import sys, time, json, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'registries/external/canadiana/pages'
ids = [l.strip() for l in (Path.home() / 'sessional_papers/ids.txt').read_text().split() if l.strip().startswith('oocihm')]
done = 0
for tag in ids:
    f = OUT / f'{tag}.tsv'
    if f.exists() and f.stat().st_size > 0:
        continue
    url = f'https://www.canadiana.ca/iiif/{tag}/manifest'
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Canada50-research/1.0 (jic823@usask.ca)'}), timeout=120) as r:
                m = json.load(r)
            break
        except Exception as e:
            print(tag, 'retry', attempt, e, file=sys.stderr); time.sleep(5 * (attempt + 1)); m = None
    if not m:
        continue
    rows = []
    for i, c in enumerate(m.get('items', []), 1):
        label = ' '.join(v[0] for v in c.get('label', {}).values() if v) if isinstance(c.get('label'), dict) else ''
        try:
            svc = c['items'][0]['items'][0]['body']['service'][0]['id']
        except Exception:
            svc = ''
        rows.append(f'{i}\t{label}\t{svc}')
    f.write_text('\n'.join(rows) + '\n')
    done += 1
    if done % 20 == 0: print('fetched', done, flush=True)
    time.sleep(0.5)
print('done; files:', len(list(OUT.glob('*.tsv'))))
