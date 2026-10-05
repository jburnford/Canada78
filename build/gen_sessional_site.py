#!/usr/bin/env python3
"""Sessional Papers 1867-1900 viewer — data build (design copied from tropical/viewer).

Reads  : registries/documents/sessional_series_by_year.csv (sessional_series_by_year.py)
         ~/sessional_papers/export/{papers,volumes}.parquet, structured/papers/**/*.md (+ .json sidecars)
         registries/external/canadiana/pages/{tag}.tsv  (fetch_canadiana_manifests.py; optional)
Writes : site_sessional/data/index.json                sessions, series, matrix, embedded matrix
         site_sessional/data/session_<year>.jsonl.gz   one record per catalog entry (+ page thumbs)
         site_sessional/data/text/<paper_id>.<n>.md.gz extracted text in ~400K-char parts
         site_sessional/index.html is the static viewer (hand-written, in build/sessional_viewer.html)

Usage : gen_sessional_site.py [--no-text] [--force]
Serve : python3 -m http.server -d site_sessional 8001
"""
import re, sys, json, gzip, shutil
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SP = Path.home() / 'sessional_papers'
OUT = ROOT / 'site_sessional'
DATA = OUT / 'data'
PAGES = ROOT / 'registries/external/canadiana/pages'
sys.path.insert(0, str(ROOT / 'build'))
from sessional_series_by_year import RULES

LABEL = {sid: lab for sid, lab, grp, rx in RULES}
GROUP = {sid: grp for sid, lab, grp, rx in RULES}
ORDER = [sid for sid, *_ in RULES]
GROUP_LABEL = {'finance': 'Finance', 'trade': 'Trade & revenue', 'agriculture': 'Agriculture family',
               'works': 'Post, works, railways & canals', 'marine': 'Marine & fisheries',
               'interior': 'Interior, Indian Affairs, North-West', 'state': 'State, civil service, Parliament',
               'justice': 'Justice', 'defence': 'Militia & defence'}
PART_CHARS = 400_000
THUMBS = 8

def clean(t):
    t = re.sub(r'\s*###\s*$', '', str(t or ''))
    return re.sub(r'\s+', ' ', t).strip()

_pages_cache = {}
def volume_pages(tag):
    if tag not in _pages_cache:
        f = PAGES / f'{tag}.tsv'
        rows = {}
        if f.exists():
            for line in f.read_text().splitlines():
                p = line.split('\t')
                if len(p) == 3 and p[2]:
                    rows[int(p[0])] = (p[1], p[2])
        _pages_cache[tag] = rows
    return _pages_cache[tag]

def thumbs_for(sources):
    """first THUMBS pages of the primary source, with IIIF ids when the manifest is cached"""
    out, total = [], 0
    for s in sources:
        a, b = int(s.get('page_start') or 0), int(s.get('page_end') or 0)
        if a <= 0: continue
        total += max(0, b - a + 1)
        vp = volume_pages(s['tag'])
        for seq in range(a, b + 1):
            if len(out) >= THUMBS: break
            label, img = vp.get(seq, ('', ''))
            out.append({'tag': s['tag'], 'seq': seq, 'label': label, 'img': img})
    return out, total

def split_parts(text, n=PART_CHARS):
    parts, i = [], 0
    while i < len(text):
        j = min(len(text), i + n)
        if j < len(text):
            k = text.rfind('\n\n', i + n // 2, j)
            if k > 0: j = k
        parts.append(text[i:j]); i = j
    return parts or ['']

def main():
    force, no_text = '--force' in sys.argv, '--no-text' in sys.argv
    df = pd.read_csv(ROOT / 'registries/documents/sessional_series_by_year.csv', dtype={'paper_num': str})
    df['disposition'] = df.disposition.fillna('unknown')
    df['paper_id'] = df.session_year + '_' + df.paper_num
    papers = pd.read_parquet(SP / 'export/papers.parquet').set_index('paper_id')
    vols = pd.read_parquet(SP / 'export/volumes.parquet')
    # papers cut from the volumes but not matched to any entry of the session list
    extra = papers[~papers.index.isin(df.paper_id)].reset_index()
    if len(extra):
        add = pd.DataFrame({'seq': extra.seq, 'session_year': extra.session_year, 'paper_num': extra.paper_num.astype(str),
                            'subject': extra.subject, 'title': extra.title.fillna('(paper not in the session list — self-numbered from its caption)'),
                            'disposition': 'unknown', 'kind': 'uncatalogued', 'series_id': None, 'contains': None,
                            'extracted': True, 'paper_id': extra.paper_id})
        df = pd.concat([df, add], ignore_index=True)
    years = list(dict.fromkeys(df.sort_values('seq').session_year))
    DATA.mkdir(parents=True, exist_ok=True); (DATA / 'text').mkdir(exist_ok=True)
    shutil.copy(ROOT / 'build/sessional_viewer.html', OUT / 'index.html')

    sidecar = {}
    for pid, p in papers.iterrows():
        j = SP / Path(p.md_path).with_suffix('.json')
        sidecar[pid] = json.loads(j.read_text()) if j.exists() else {}

    sessions, matrix, embedded = [], {}, {}
    n_text = 0
    for y in years:
        g = df[df.session_year == y].sort_values('seq')
        seq = int(g.seq.iloc[0])
        recs = []
        for r in g.itertuples():
            pid = r.paper_id
            ext = bool(r.extracted) and pid in papers.index
            contains = [c for c in str(r.contains).split(';') if c in LABEL] if isinstance(r.contains, str) else []
            rec = {'id': pid, 'seq': seq, 'year': y, 'num': r.paper_num, 'subject': clean(r.subject) if isinstance(r.subject, str) else '',
                   'title': clean(r.title), 'disposition': r.disposition, 'kind': r.kind,
                   'series': r.series_id if isinstance(r.series_id, str) else None, 'contains': contains,
                   'extracted': ext, 'chars': 0, 'parts': 0, 'sources': [], 'thumbs': [], 'n_pages': 0,
                   'department': None, 'presented': None, 'mover': None}
            if ext:
                p = papers.loc[pid]; sc = sidecar.get(pid, {})
                srcs = sc.get('sources') or [{'tag': t, 'page_start': int(p.page_start), 'page_end': int(p.page_end), 'method': ''} for t in str(p.source_tags).split(';')]
                rec['sources'] = [{'tag': s['tag'], 'page_start': s.get('page_start'), 'page_end': s.get('page_end'), 'method': s.get('method', '')} for s in srcs]
                rec['thumbs'], rec['n_pages'] = thumbs_for(rec['sources'])
                rec['chars'] = int(p.chars)
                rec['department'] = p.department if isinstance(p.department, str) else None
                rec['presented'] = p.presented if isinstance(p.presented, str) else None
                rec['mover'] = p.mover if isinstance(p.mover, str) else None
                src = SP / p.md_path
                if src.exists():
                    first = DATA / 'text' / f'{pid}.1.md.gz'
                    if no_text and first.exists():
                        rec['parts'] = len(list((DATA / 'text').glob(f'{pid}.*.md.gz')))
                    elif not no_text:
                        if force or not first.exists() or first.stat().st_mtime < src.stat().st_mtime:
                            for old in (DATA / 'text').glob(f'{pid}.*.md.gz'): old.unlink()
                            parts = split_parts(src.read_text(encoding='utf-8', errors='replace'))
                            for i, part in enumerate(parts, 1):
                                with gzip.open(DATA / 'text' / f'{pid}.{i}.md.gz', 'wt', encoding='utf-8', compresslevel=6) as fh:
                                    fh.write(part)
                            rec['parts'] = len(parts); n_text += 1
                            if n_text % 100 == 0: print('  text', n_text, flush=True)
                        else:
                            rec['parts'] = len(list((DATA / 'text').glob(f'{pid}.*.md.gz')))
            recs.append(rec)
            cell = [r.paper_num, ext, r.disposition == 'not_printed', clean(r.title)[:140]]
            if rec['series']:
                matrix.setdefault(rec['series'], {}).setdefault(y, []).append(cell)
            if r.kind in ('report', 'statement', 'other'):
                for c in contains:
                    embedded.setdefault(c, {}).setdefault(y, []).append(cell)
        with gzip.open(DATA / f'session_{y}.jsonl.gz', 'wt', encoding='utf-8') as fh:
            for rec in recs: fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        printed = [r for r in recs if r['disposition'] != 'not_printed']
        vv = vols[vols.seq == seq].sort_values(['volume', 'part'])
        sessions.append({'year': y, 'seq': seq, 'entries': len(recs), 'printed': len(printed),
                         'not_printed': len(recs) - len(printed), 'extracted': sum(r['extracted'] for r in recs),
                         'chars': sum(r['chars'] for r in recs),
                         'reports': sum(1 for r in printed if r['series'] or r['kind'] in ('report', 'statement')),
                         'returns': sum(1 for r in printed if not r['series'] and r['kind'] in ('return_to_order', 'return_to_address')),
                         'volumes': [{'tag': v.tag, 'volume': int(v.volume), 'part': None if pd.isna(v.part) else int(v.part), 'pages': int(v.num_pages)} for v in vv.itertuples()]})
    index = {'sessions': sessions, 'years': years,
             'series': [{'id': s, 'label': LABEL[s], 'group': GROUP[s]} for s in ORDER if s in matrix or s in embedded],
             'groups': GROUP_LABEL, 'matrix': matrix, 'embedded': embedded,
             'totals': {'entries': len(df), 'extracted': int(df.extracted.sum()), 'volumes_ingested': int(len(vols)), 'volumes_total': 372}}
    (DATA / 'index.json').write_text(json.dumps(index, ensure_ascii=False))
    print(f'index: {len(sessions)} sessions, {len(index["series"])} series; text parts written for {n_text} papers')

if __name__ == '__main__':
    main()
