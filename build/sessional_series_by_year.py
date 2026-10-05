#!/usr/bin/env python3
"""First-pass map of the Sessional Papers 1867-1900: which recurring reports
and other major kinds of paper appear in each session.

Input : ~/sessional_papers/export/session_index.parquet  (reconciled per-session
        "List of Sessional Papers", all 34 sessions, 5,363 entries)
        ~/sessional_papers/export/papers.parquet         (papers extracted so far)
Output: registries/documents/sessional_series_by_year.csv  (one row per catalog entry)
        docs/SESSIONAL_CONTENTS_BY_YEAR.md                 (matrix + per-session summary)
        registries/documents/sessional_series_unclassified.csv (printed, no series rule)

Classification is by ordered regex over subject + title; first match wins.
Series ids are stable keys for the KG's series nodes.
"""
import re, sys
from pathlib import Path
import pandas as pd

SP = Path.home() / 'sessional_papers' / 'export'
ROOT = Path(__file__).resolve().parents[1]

# (series_id, label, group, regex)  -- order matters
RULES = [
 # --- finance
 ('auditor_general',   "Auditor General's report",            'finance', r'auditor[- ]general'),
 ('public_accounts',   'Public Accounts',                     'finance', r'public accounts'),
 ('estimates',         'Estimates (main/supplementary)',      'finance', r'\bestimates?\b.*(sums|service|year)|^estimates|supplementary estimates'),
 ('gg_warrants',       "Governor General's warrants",         'finance', r"governor[- ]general'?s? warrants|warrants issued"),
 ('unforeseen',        'Unforeseen expenses statement',       'finance', r'unforeseen expenses'),
 ('superannuation',    'Superannuation statement',            'finance', r'superannuat'),
 ('banks_shareholders','Chartered banks: shareholders list',  'finance', r'shareholders.*bank|bank.*shareholders'),
 ('banks_unclaimed',   'Chartered banks: unclaimed balances', 'finance', r'dividends remaining unpaid|unclaimed balances'),
 ('banks_other',       'Banks (other statements)',            'finance', r'^(banks?|bank of|montreal bank|chartered banks)|\bbanks?\b.*(statement|return of)'),
 ('insurance',         'Insurance (Superintendent / abstracts)','finance', r'insurance'),
 ('savings_banks',     'Savings banks / Dominion notes',      'finance', r'savings? bank|dominion notes|provincial notes'),
 # --- trade & revenue
 ('trade_navigation',  'Trade and Navigation tables',         'trade',   r'trade and navigation|tables of the trade'),
 ('trade_commerce',    'Dept. of Trade and Commerce report',  'trade',   r'department of trade and commerce'),
 ('inland_revenue',    'Inland Revenue report',               'trade',   r'inland revenue'),
 ('weights_measures',  'Weights, Measures & Gas inspection',  'trade',   r'weights,? measures'),
 ('adulteration',      'Adulteration of Food report',         'trade',   r'adulteration'),
 ('customs',           'Customs (other)',                     'trade',   r'^customs|customs department'),
 # --- agriculture family
 ('experimental_farms','Experimental Farms report',           'agriculture', r'experimental farms?'),
 ('archives',          'Canadian Archives report',            'agriculture', r'archives'),
 ('criminal_stats',    'Criminal Statistics',                 'agriculture', r'criminal statistics'),
 ('mortuary_stats',    'Mortuary / vital statistics',         'agriculture', r'mortuary|vital statistics'),
 ('dairy',             'Dairy Commissioner report',           'agriculture', r'dairy commissioner'),
 ('high_commissioner', 'High Commissioner (London) report',   'agriculture', r'high commissioner'),
 ('immigration',       'Immigration reports/returns',         'agriculture', r'immigra'),
 ('census',            'Census',                              'agriculture', r'\bcensus\b'),
 ('agriculture',       'Minister of Agriculture report',      'agriculture', r'minister of agriculture|department of agriculture|^agriculture'),
 ('exhibitions',       'Exhibitions (Paris, Philadelphia, Colonial…)', 'agriculture', r'exhibition'),
 # --- communications / works
 ('post_office',       'Postmaster General report',           'works',   r'postmaster[- ]general|^post office'),
 ('public_works',      'Public Works report',                 'works',   r'minister of public works|department of public works|^public works'),
 ('railway_statistics','Railway Statistics',                  'works',   r'railway statistics'),
 ('canal_statistics',  'Canal Statistics / canals revenue',   'works',   r'canal statistics|canals revenue'),
 ('railways_canals',   'Railways and Canals report',          'works',   r'railways and canals'),
 ('intercolonial',     'Intercolonial Railway',               'works',   r'intercolonial'),
 ('cpr',               'Canadian Pacific Railway papers',     'works',   r'canadian pacific|pacific railway'),
 ('railways_other',    'Railways (other returns)',            'works',   r'railway'),
 ('canals_other',      'Canals / harbours (other returns)',   'works',   r'canal|harbou?r'),
 # --- marine
 ('steamboat_insp',    'Steamboat Inspection',                'marine',  r'steamboat inspection|board of steamboat'),
 ('fisheries',         'Fisheries report / statements',       'marine',  r'department of fisheries|fisheries statements|fish-?breeding|fisheries of canada|fishing bount|^fisheries|fishery'),
 ('marine_fisheries',  'Marine (and Fisheries) report',       'marine',  r'marine and fisheries|department of marine|^marine'),
 ('harbour_comm',      'Harbour Commissioners',               'marine',  r'harbou?r commissioners|trinity house'),
 ('meteorology',       'Meteorological Service',              'marine',  r'meteorolog'),
 # --- interior / indigenous / north-west
 ('geological_survey', 'Geological Survey report',            'interior', r'geological'),
 ('dominion_lands',    'Dominion Lands',                      'interior', r'dominion lands|homestead|land regulations|public lands'),
 ('interior',          'Dept. of the Interior report',        'interior', r'department of the interior|minister of the interior|^interior'),
 ('indian_affairs',    'Indian Affairs report',               'interior', r'indian affairs|^indians?\b|indian (lands|reserves?|branch)'),
 ('nwmp',              'North-West Mounted Police report',    'interior', r'mounted police'),
 ('nwt',               'North-West Territories papers',       'interior', r'north[- ]west territor|half[- ]breed|rebellion|riel'),
 ('yukon',             'Yukon papers',                        'interior', r'yukon|klondike'),
 ('manitoba_bc',       'Manitoba / British Columbia papers',  'interior', r'manitoba|british columbia'),
 # --- state / justice / defence
 ('civil_service_list','Civil Service List',                  'state',   r'civil service list'),
 ('civil_service',     'Civil Service (examiners, commissions)','state',  r'civil service'),
 ('public_printing',   'Public Printing and Stationery',      'state',   r'public printing|printing and stationery'),
 ('secretary_of_state','Secretary of State report',           'state',   r'secretary of state'),
 ('library',           'Library of Parliament report',        'state',   r'librar'),
 ('penitentiaries',    'Penitentiaries report',               'justice', r'penitentiar'),
 ('justice',           'Justice (other)',                     'justice', r'minister of justice|^justice|judiciary|supreme court|exchequer|court'),
 ('statutes',          'Statutes: consolidation / disallowance / provincial acts','justice', r'statutes|disallowance|provincial (acts|legislation)|acts of the'),
 ('elections',         'Elections returns',                   'state',   r'election'),
 ('militia',           'Militia and Defence report',          'defence', r'militia|defence'),
 ('gg_messages',       "Governor General's messages / despatches",'state', r'governor[- ]general transmits|message from his excellency|despatch'),
 ('treaties_imperial', 'Treaties / Imperial & foreign relations','state', r'treaty|reciprocity|washington|imperial|newfoundland|colonial conference'),
]
RULES_C = [(sid, lab, grp, re.compile(rx, re.I)) for sid, lab, grp, rx in RULES]

KIND_RULES = [
 ('return_to_order',   r'return.{0,30}to an order|return to order|return.{0,20}order of the (house|senate)'),
 ('return_to_address', r'return.{0,30}to an address|return to address|address of the (house|senate)'),
 ('return_resolution', r'return.{0,40}(resolution|standing order)'),
 ('message',           r'^message|transmits to the house'),
 ('report_commission', r'royal commission|commissioners? appointed'),
 ('correspondence',    r'^(copies of|copy of|papers|correspondence|further papers|additional papers)'),
 ('statement',         r'^(statement|statements|list of|abstract|tables?)'),
 ('report',            r'^(annual |\w+ annual |first |second |third |preliminary |summary )?report|^reports? of'),
]
KIND_C = [(k, re.compile(rx, re.I)) for k, rx in KIND_RULES]

def clean(s):
    s = re.sub(r'\*+', '', str(s or ''))
    s = re.sub(r'\s+', ' ', s).strip(' .—-')
    return s

LEAD_SPLIT = re.compile(r'(\s*APPENDIX|\s*SUPPLEMENT|:—|\.—|\s—|\.\s*Presented|Presented to|;\s*presented|\s###)', re.I)

def leading(title):
    t = clean(title)
    return LEAD_SPLIT.split(t, 1)[0]

def first_rule(text):
    for sid, lab, grp, rx in RULES_C:
        if rx.search(text):
            return sid
    return None

RETURN_SERIES = {'gg_warrants','unforeseen','superannuation','banks_shareholders','banks_unclaimed',
                 'cpr','intercolonial','immigration','nwt','yukon','elections','civil_service','statutes'}

def classify(subject, title):
    subj, lead, full = clean(subject), leading(title), clean(subject) + ' | ' + clean(title)
    kind = 'other'
    for k, rx in KIND_C:
        if rx.search(clean(title)) or (k in ('return_to_order','return_to_address') and rx.search(full)):
            kind = k; break
    is_return = kind in ('return_to_order', 'return_to_address')
    # primary series: the subject heading (pre-1885 lists) wins, then the leading
    # clause of the title, then the whole text.  Titles concatenate a paper with
    # its appendices/sub-reports, so whole-text matching alone mislabels e.g.
    # "Report of the Minister of Agriculture ... Report on Archives" as archives.
    series = None
    for text in ((subj, lead, full) if not is_return else (subj, full)):
        if not text: continue
        sid = first_rule(text)
        if sid and (not is_return or sid in RETURN_SERIES):
            series = sid; break
        if sid and is_return:
            break
    # everything else the entry contains (sub-reports, appendices, supplements)
    contains = [sid for sid, lab, grp, rx in RULES_C if rx.search(full) and sid != series]
    return kind, series, contains

def main():
    si = pd.read_parquet(SP / 'session_index.parquet')
    papers = pd.read_parquet(SP / 'papers.parquet')
    extracted = set(papers.paper_id)
    rows = []
    for r in si.itertuples(index=False):
        kind, series, contains = classify(r.subject, r.title)
        pid = f'{r.session_year}_{r.paper_num}'
        rows.append(dict(seq=r.seq, session_year=r.session_year, paper_num=r.paper_num,
                         subject=clean(r.subject), title=clean(r.title)[:200],
                         disposition=r.disposition or 'unknown', kind=kind, series_id=series, contains=';'.join(contains),
                         extracted=pid in extracted))
    df = pd.DataFrame(rows)
    label = {sid: lab for sid, lab, grp, rx in RULES}
    group = {sid: grp for sid, lab, grp, rx in RULES}
    df['series_label'] = df.series_id.map(label)
    df['series_group'] = df.series_id.map(group)
    out = ROOT / 'registries' / 'documents'
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / 'sessional_series_by_year.csv', index=False)

    printed = df[df.disposition != 'not_printed']
    unc = printed[printed.series_id.isna() & ~printed.kind.isin(['return_to_order','return_to_address'])]
    unc.to_csv(out / 'sessional_series_unclassified.csv', index=False)

    # ---------- markdown
    years = list(dict.fromkeys(df.sort_values('seq').session_year))
    short = {y: (y[2:] if '-' not in y else y[2:4]+'/'+y[-2:]) for y in years}
    L = []
    L.append('# Sessional Papers 1867–1900: reports and major sections by session\n')
    L.append(f'_Generated by `build/sessional_series_by_year.py` from the reconciled per-session '
             f'List of Sessional Papers ({len(df):,} catalog entries, 34 sessions) and the '
             f'papers extracted so far ({len(extracted):,}, from 235 of 372 volumes). First pass: '
             f'classification is regex over subject+title; review `registries/documents/sessional_series_unclassified.csv`._\n')

    # per-session summary
    L.append('## 1. Per-session summary\n')
    L.append('| seq | session | catalog entries | printed | not printed | annual-report family (printed) | returns to order/address (printed) | other printed | extracted so far |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for seq, g in df.groupby('seq'):
        p = g[g.disposition != 'not_printed']
        np_ = (g.disposition == 'not_printed').sum()
        rep = p[p.kind.isin(['report','statement']) | p.series_id.notna()]
        ret = p[p.kind.isin(['return_to_order','return_to_address']) & p.series_id.isna()]
        oth = len(p) - len(rep) - len(ret)
        L.append(f'| {seq} | {g.session_year.iloc[0]} | {len(g)} | {len(p)} | {np_} | {len(rep)} | {len(ret)} | {oth} | {p.extracted.sum()} |')
    L.append('')
    L.append('"Printed" = disposition is not `not_printed` (the list marks unprinted returns; '
             'pre-1885 lists give no disposition for printed papers, so those are counted as printed). '
             '"Extracted" counts papers already cut from the 235-volume ingest.\n')

    # matrix of series x year
    L.append('## 2. Recurring series by session (cell = paper number(s))\n')
    L.append('Columns are sessions (two-digit year of the session; 80/81 = 1880–81, 96ii = second 1896 session). '
             'A paper number means that session\'s list carries the series (`*` = the list marks it not printed, often a disposition-parse error on annual reports); `·` means no entry matched.\n')
    ser = df[df.series_id.notna()].copy()
    ser['cell'] = ser.paper_num.astype(str) + ser.disposition.eq('not_printed').map({True: '*', False: ''})
    order = [sid for sid, *_ in RULES]
    L.append('| series | group | ' + ' | '.join(short[y] for y in years) + ' | sessions |')
    L.append('|---|---|' + '---|' * len(years) + '---|')
    for sid in order:
        g = ser[ser.series_id == sid]
        if g.empty: continue
        cells = []
        for y in years:
            nums = g[g.session_year == y].cell.tolist()
            cells.append(','.join(nums[:3]) + ('…' if len(nums) > 3 else '') if nums else '·')
        L.append(f'| {label[sid]} | {group[sid]} | ' + ' | '.join(cells) + f' | {g.session_year.nunique()} |')
    L.append('')

    # sub-reports embedded in other papers (department reorganisations show up here)
    L.append('## 2b. Series that appear as sub-reports/appendices inside another paper\n')
    L.append('Catalog titles concatenate a paper with its appendices. Cells give the host paper number. '
             'This is where the Indian Branch (inside Secretary of State for the Provinces 1869–73, Interior 1874–79), '
             'Fisheries (inside Marine), Steamboat Inspection, Criminal Statistics and Archives (inside Agriculture) live before they became separate papers.\n')
    ex = df[df.contains.fillna('') != ''].copy()
    ex['contains'] = ex.contains.str.split(';')
    ex = ex.explode('contains')
    ex = ex[ex.kind.isin(['report','statement','other'])]
    L.append('| embedded series | ' + ' | '.join(short[y] for y in years) + ' |')
    L.append('|---|' + '---|' * len(years))
    for sid in order:
        g = ex[ex.contains == sid]
        if g.session_year.nunique() < 2: continue
        cells = []
        for y in years:
            nums = g[g.session_year == y].paper_num.astype(str).tolist()
            cells.append(','.join(nums[:2]) + ('…' if len(nums) > 2 else '') if nums else '·')
        L.append(f'| {label[sid]} | ' + ' | '.join(cells) + ' |')
    L.append('')

    # unclassified printed, non-return
    L.append('## 3. Printed papers without a series rule (excluding returns to order/address)\n')
    L.append(f'{len(unc)} entries, listed by session in `registries/documents/sessional_series_unclassified.csv`. '
             'Sample of the most common opening words:\n')
    first = unc.title.str.extract(r'^((?:\S+\s+){0,4}\S+)')[0].str.lower()
    for w, n in first.value_counts().head(25).items():
        L.append(f'- {n} × "{w}"')
    L.append('')
    (ROOT / 'docs' / 'SESSIONAL_CONTENTS_BY_YEAR.md').write_text('\n'.join(L))
    print(df.kind.value_counts()); print()
    print('printed with series:', ser.shape[0], 'unclassified printed non-return:', len(unc))
    print(ser.series_id.value_counts().head(60))

if __name__ == '__main__':
    main()
