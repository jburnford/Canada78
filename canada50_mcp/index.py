"""Build the canada50-mcp data index (SQLite + FTS5) from the registries.

    python3 -m canada50_mcp.index [--out site/_data/canada50.sqlite] [--sample]

Inputs: registries/ (entities, documents, annotations), curation/, the DIA
markdown (segment text via char offsets), and site/index.json + editions.json
from build/gen_wiki.py (page URLs). Output is one SQLite file that the MCP
server and CLI read; it is a build artifact (gitignored under site/).

Tables: documents, segments (+ segments_fts), entities, aliases, mentions,
edges. Edge vocabulary (docs/IDENTITY_MODEL_WORKSHOP.md): band OCCUPIES
reserve; reserve ADMINISTERED_BY agency; agency MERGED_INTO / SPLIT_INTO
agency (curated); person SIGNED_FOR agency; band SUCCEEDED_BY wikidata QID.
"""
import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
PAGE_RE = re.compile(r"<!-- page (\d+) -->\n?")

SCHEMA = """
CREATE TABLE documents(doc_id TEXT PRIMARY KEY, tag TEXT, report_year INT, title TEXT,
  session_year TEXT, paper_num TEXT, id_status TEXT, url TEXT, n_segments INT);
CREATE TABLE segments(doc_id TEXT, segment_id TEXT, tag TEXT, report_year INT, ordinal INT,
  heading TEXT, kind TEXT, agency_id TEXT, page_start INT, page_end INT, text_version TEXT,
  url TEXT, text TEXT, PRIMARY KEY(doc_id, segment_id));
CREATE VIRTUAL TABLE segments_fts USING fts5(heading, text, content='segments',
  content_rowid='rowid', tokenize='unicode61');
CREATE TABLE entities(uri TEXT PRIMARY KEY, type TEXT, id TEXT, name TEXT, url TEXT,
  region TEXT, qid TEXT, summary TEXT, n_mentions INT);
CREATE INDEX entities_type_id ON entities(type, id);
CREATE TABLE aliases(alias TEXT, type TEXT, id TEXT, n INT, source TEXT);
CREATE INDEX aliases_alias ON aliases(alias);
CREATE TABLE mentions(doc_id TEXT, segment_id TEXT, report_year INT, entity_type TEXT,
  entity_id TEXT, surface TEXT, confidence TEXT, char_start INT, char_end INT);
CREATE INDEX mentions_entity ON mentions(entity_type, entity_id);
CREATE INDEX mentions_seg ON mentions(doc_id, segment_id);
CREATE TABLE edges(src_type TEXT, src_id TEXT, edge TEXT, dst_type TEXT, dst_id TEXT,
  year INT, note TEXT);
CREATE INDEX edges_src ON edges(src_type, src_id);
CREATE INDEX edges_dst ON edges(dst_type, dst_id);
"""


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def read_body(tag):
    raw = (DIA_MD / f"{tag}.md").read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"')
    return (raw[m.end():] if m else raw), fm


def nn(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site/_data/canada50.sqlite"))
    ap.add_argument("--site", default=str(ROOT / "site"))
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    site = Path(args.site)
    index_json = json.loads((site / "index.json").read_text())
    editions = json.loads((site / "editions.json").read_text())
    url_of = {(v["type"], v["id"]): v["url"] for v in index_json.values()}
    uri_of = {(v["type"], v["id"]): k for k, v in index_json.items()}

    suffix = "_sample" if args.sample else ""
    men = pd.read_parquet(ROOT / f"registries/annotations/mentions_dia{suffix}.parquet")
    men["report_year"] = men.tag.str.extract(r"(\d{4})").astype(int)
    segs = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet")
    segs["report_year"] = segs.tag.str.extract(r"(\d{4})").astype(int)
    if args.sample:
        segs = segs[segs.tag.isin(set(men.tag))]
    chains = pd.read_parquet(ROOT / "registries/entities/agency_chains.parquet")
    members = pd.read_parquet(ROOT / "registries/entities/agency_chain_members.parquet")
    reserves = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    bands = pd.read_parquet(ROOT / "registries/entities/bands.parquet")
    pm = ROOT / f"registries/entities/persons_minted{suffix}.parquet"
    persons = pd.read_parquet(pm) if pm.exists() else pd.DataFrame()
    lincs = pd.read_parquet(ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    xw = pd.read_csv(ROOT / "registries/crosswalks/dia_sessional.csv").set_index("tag")
    evp = ROOT / "curation/agency_chain_events.csv"
    events = pd.read_csv(evp) if evp.exists() else pd.DataFrame()
    aup = ROOT / "curation/schedule_area_units.csv"
    area_units = dict(pd.read_csv(aup)[["division_norm", "unit"]].values) if aup.exists() else {}

    con = sqlite3.connect(out)
    con.executescript(SCHEMA)

    # ---- documents + segments
    seg_chain = {(m.tag, m.segment_id): m.chain_id for m in members.itertuples()}
    n_doc_segs = segs.groupby("doc_id").size()
    for tag, grp in segs.groupby("tag"):
        body, fm = read_body(tag)
        doc_id = grp.doc_id.iat[0]
        year = int(grp.report_year.iat[0])
        x = xw.loc[tag] if tag in xw.index else None
        con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)", (
            doc_id, tag, year, f"{fm.get('title', 'Annual Report of the Department of Indian Affairs')}, {year}",
            None if x is None else nn(x.session_year), None if x is None else nn(x.paper_num),
            "catalog_ref" if not doc_id.startswith("prov:") else "provisional",
            editions.get(doc_id, {}).get("url"), int(n_doc_segs[doc_id])))
        rows = []
        for s in grp.sort_values("ordinal").itertuples():
            text = body[s.char_start:s.char_end]
            text = PAGE_RE.sub(lambda m: f"\n[p. {m.group(1)}]\n", text)
            rows.append((doc_id, s.segment_id, tag, year, int(s.ordinal), s.heading, s.kind,
                         seg_chain.get((tag, s.segment_id)), int(s.page_start), int(s.page_end),
                         s.text_version, editions.get(doc_id, {}).get("segments", {}).get(s.segment_id),
                         text))
        con.executemany("INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        print(f"  {tag}: {len(rows)} segments")
    con.execute("INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')")

    # ---- mentions
    con.executemany("INSERT INTO mentions VALUES (?,?,?,?,?,?,?,?,?)", [
        (m.doc_id, m.segment_id, int(m.report_year), m.entity_type, m.entity_id, m.surface,
         m.confidence, int(m.char_start), int(m.char_end)) for m in men.itertuples()])
    n_men = men[men.confidence.isin(["high", "medium"])].groupby(["entity_type", "entity_id"]).size()

    # ---- entities + aliases + edges
    ents, aliases, edges = [], [], []

    def add_alias(alias, etype, eid, n=1, source="registry"):
        a = norm(alias)
        if a:
            aliases.append((a, etype, eid, n, source))

    chain_name = {}
    for c in chains.itertuples():
        name = c.canonical.title()
        chain_name[c.chain_id] = name
        provs = ", ".join(reserves[reserves.division_norm == c.canonical].province.dropna().unique())
        summary = (f"{c.unit_type.title()} of the Department of Indian Affairs, attested in annual-report "
                   f"headings {c.first_year}–{c.last_year} ({c.n_variants} name variants)"
                   + (f"; LINCS {c.lincs_uri}" if nn(c.lincs_uri) else ""))
        ents.append((c.uri, "agency", c.chain_id, name, url_of.get(("agency", c.chain_id)),
                     provs or None, None, summary, int(n_men.get(("agency", c.chain_id), 0))))
        add_alias(c.canonical, "agency", c.chain_id)
        bare = re.sub(r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE)\b", "", c.canonical).strip(" -")
        if len(bare) > 4:
            add_alias(bare, "agency", c.chain_id, source="bare")
    for m in members.drop_duplicates(["name", "chain_id"]).itertuples():
        add_alias(m.name, "agency", m.chain_id, source="variant")
    if len(events):
        for e in events.itertuples():
            edges.append(("agency", e.from_chain, e.event_type, "agency", e.to_chain,
                          int(e.year_approx), f"{e.confidence}: {e.evidence}"))

    band_by_norm = {}
    for b in bands.itertuples():
        band_by_norm[b.band_id] = b.band_id
    for bn in reserves.band_norm.dropna().unique():
        if "BAND-" + slug(bn) in set(bands.band_id):
            band_by_norm[bn] = "BAND-" + slug(bn)

    for r in reserves.itertuples():
        no = "" if nn(r.reserve_no) is None else f" No. {r.reserve_no}"
        name = (r.name if nn(r.name) else f"{nn(r.band_norm) or (r.division_norm or '').title()} reserve") + no
        summary = "; ".join(filter(None, [
            f"band {r.band_norm}" if nn(r.band_norm) else None,
            f"{r.division_type} {str(r.division_norm).title()}" if nn(r.division_norm) else None,
            str(r.province).title() if nn(r.province) else None,
            r.location if nn(r.location) else None,
            (f"{r.acres_text} sq. miles (≈ {r.acres * 640:,.0f} acres; printed under an 'Acres' header, "
             f"see curation/schedule_area_units.csv)"
             if nn(r.acres_text) and area_units.get(r.division_norm) == "square_miles"
             else f"{r.acres_text} acres" if nn(r.acres_text) else None),
            f"Schedule of Indian Reserves 1902 p. {r.page}"]))
        ents.append((r.uri, "reserve", r.reserve_id, name, url_of.get(("reserve", r.reserve_id)),
                     nn(r.province), None, summary, int(n_men.get(("reserve", r.reserve_id), 0))))
        if nn(r.name):
            add_alias(r.name, "reserve", r.reserve_id)
            add_alias(f"{r.name} {no.strip()}", "reserve", r.reserve_id) if no else None
        if r.division_type == "agency" and nn(r.division_norm):
            edges.append(("reserve", r.reserve_id, "ADMINISTERED_BY", "agency",
                          "AG-" + slug(r.division_norm), 1902, "Schedule of Indian Reserves 1902"))
        b = band_by_norm.get(r.band_norm)
        if b:
            edges.append(("band", b, "OCCUPIES", "reserve", r.reserve_id, 1902,
                          "Schedule of Indian Reserves 1902"))

    for b in bands.itertuples():
        provs = ", ".join(list(b.provinces) if b.provinces is not None else [])
        summary = (f"Historical band (DIA, 1902 Schedule); {b.n_reserves} reserves"
                   + (f", {b.total_acres:,.0f} acres" if nn(b.total_acres) else "")
                   + (f"; succeeded by {b.wd_label} ({b.succeeded_by_qid})" if nn(b.succeeded_by_qid) else "")
                   + (f"; people: {b.wd_label} ({b.people_qid})" if nn(b.people_qid) and not nn(b.succeeded_by_qid) else "")
                   + (f"; note: {b.notes}" if nn(b.notes) else ""))
        ents.append((b.uri, "band", b.band_id, b.name, url_of.get(("band", b.band_id)),
                     provs or None, nn(b.succeeded_by_qid) or nn(b.people_qid), summary,
                     int(n_men.get(("band", b.band_id), 0))))
        add_alias(b.name, "band", b.band_id)
        if nn(b.wd_label):
            add_alias(b.wd_label, "band", b.band_id, source="wikidata")
        if nn(b.succeeded_by_qid):
            edges.append(("band", b.band_id, "SUCCEEDED_BY", "wikidata", b.succeeded_by_qid, None, b.wd_label))

    agent_ids = set(men[men.entity_type == "agent"].entity_id)
    if len(persons):
        for p in persons.itertuples():
            ents.append((p.uri, "person", p.person_id, p.name.title(), url_of.get(("person",p.person_id)),
                         None, None, f"Minted from {p.n_signatures} report signature(s), {p.first_year}–{p.last_year}; "
                         f"no LINCS match", int(n_men.get(("agent", p.person_id), 0))))
            add_alias(p.name, "person", p.person_id)
            for c in (p.chains if p.chains is not None else []):
                edges.append(("person", p.person_id, "SIGNED_FOR", "agency", c, None, "report signature"))
            agent_ids.discard(p.person_id)
    lincs_label = lincs.groupby("agent").agent_label.agg(lambda s: s.mode().iat[0])
    for aid in sorted(agent_ids):
        acts = lincs[lincs.agent == aid].sort_values("begin")
        postings = "; ".join(f"{str(a.begin)[:4]} {a.place_label} ({a.group_label})" for a in acts.itertuples())
        ents.append((aid, "person", aid, lincs_label.get(aid, aid), url_of.get(("person",aid)), None, None,
                     f"LINCS Indian Affairs Agents; postings: {postings}", int(n_men.get(("agent", aid), 0))))
        add_alias(lincs_label.get(aid, ""), "person", aid, source="lincs")
        for c in men[(men.entity_type == "agent") & (men.entity_id == aid)].context_agency.dropna().unique():
            edges.append(("person", aid, "SIGNED_FOR", "agency", c, None, "report signature"))

    # mention surfaces as aliases (weighted by count, primary confidence only)
    surf = Counter()
    for m in men[men.confidence.isin(["high", "medium"])].itertuples():
        et = "person" if m.entity_type == "agent" else m.entity_type
        surf[(norm(m.surface), et, m.entity_id)] += 1
    for (a, et, eid), n in surf.items():
        if a:
            aliases.append((a, et, eid, n, "mention"))

    con.executemany("INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?,?,?,?)", ents)
    con.executemany("INSERT INTO aliases VALUES (?,?,?,?,?)", aliases)
    con.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?)", edges)
    con.commit()
    for t in ["documents", "segments", "entities", "aliases", "mentions", "edges"]:
        print(f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]:,}")
    con.close()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
