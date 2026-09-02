"""Build the canada50-mcp data index (SQLite + FTS5) from the registries.

    python3 -m canada50_mcp.index [--out site/_data/canada50.sqlite] [--sample]

Inputs: registries/ (entities, documents, annotations, crosswalks),
curation/, the DIA markdown (segment text via char offsets), and
site/index.json + editions.json from build/gen_wiki.py (page URLs). Output is
one SQLite file that the MCP server and CLI read; it is a build artifact
(gitignored under site/).

Entity universe (v2, 2026-09-02) — every registry the KG build produced, not
only the ones with wiki pages:

  agency   agency chains (build_agency_chains) + the agstat identities that
           are agencies (classify_agstat_identities: identity_kind == agency)
           and could not be linked to a chain — county agencies, districts
  reserve  1902 Schedule, with the Wikidata / NRCan succession attached
  band     curated 1902 bands (pages) + census bands (mint_bands_from_census);
           a census band bridged to a curated band (merge_band_registries) is
           the *same* entity, and census identities the aliasing pass merged
           (alias_census_bands) redirect to their canonical id
  person   one per wiki page: LINCS agent URI where the authority owns the
           identity, minted PERSON- id otherwise; minted ids re-anchored to
           LINCS redirect
  school   schools registry

`redirects` maps every superseded id to the id the entity lives under, and
`Store.resolve` follows it, so old mention anchors and observation keys keep
working.

Edges carry a year wherever the attestation is dated (KG_BUILD_PLAN §1,
time-scoping rule): band ADMINISTERED_BY agency (census, per year); reserve
ADMINISTERED_BY agency (Schedule editions); band OCCUPIES reserve (1902);
school IN_AGENCY / ON_RESERVE (School Statements, per year); person
TAUGHT_AT school; person POSTED_TO agency (LINCS postings, per year); person
SIGNED_FOR agency / REPORTED_ON school (letter signatures, per year);
agency MERGED_INTO / SPLIT_INTO / RENAMED (curated); band / reserve
SUCCEEDED_BY a modern Wikidata item.
"""
import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
from build_agency_chains import canonicalize  # noqa: E402

DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
PAGE_RE = re.compile(r"<!-- page (\d+) -->\n?")

SCHEMA = """
CREATE TABLE documents(doc_id TEXT PRIMARY KEY, tag TEXT, report_year INT, title TEXT,
  session_year TEXT, paper_num TEXT, id_status TEXT, url TEXT, n_segments INT);
CREATE TABLE segments(doc_id TEXT, segment_id TEXT, tag TEXT, report_year INT, ordinal INT,
  heading TEXT, kind TEXT, agency_id TEXT, school_id TEXT, attribution TEXT, letter_kind TEXT,
  page_start INT, page_end INT, text_version TEXT, url TEXT, text TEXT,
  PRIMARY KEY(doc_id, segment_id));
CREATE INDEX segments_agency ON segments(agency_id);
CREATE INDEX segments_school ON segments(school_id);
CREATE VIRTUAL TABLE segments_fts USING fts5(heading, text, content='segments',
  content_rowid='rowid', tokenize='unicode61');
CREATE TABLE entities(uri TEXT PRIMARY KEY, type TEXT, id TEXT, name TEXT, url TEXT,
  region TEXT, qid TEXT, kind TEXT, first_year INT, last_year INT, n_years INT,
  summary TEXT, n_mentions INT);
CREATE UNIQUE INDEX entities_type_id ON entities(type, id);
CREATE TABLE redirects(type TEXT, id TEXT, canonical_id TEXT, reason TEXT,
  PRIMARY KEY(type, id));
CREATE TABLE aliases(alias TEXT, type TEXT, id TEXT, n INT, source TEXT);
CREATE INDEX aliases_alias ON aliases(alias);
CREATE INDEX aliases_type_id ON aliases(type, id);
CREATE VIRTUAL TABLE aliases_fts USING fts5(alias_sq, tokenize='trigram');
CREATE TABLE mentions(doc_id TEXT, segment_id TEXT, report_year INT, entity_type TEXT,
  entity_id TEXT, surface TEXT, confidence TEXT, char_start INT, char_end INT);
CREATE INDEX mentions_entity ON mentions(entity_type, entity_id);
CREATE INDEX mentions_seg ON mentions(doc_id, segment_id);
CREATE TABLE edges(src_type TEXT, src_id TEXT, edge TEXT, dst_type TEXT, dst_id TEXT,
  year INT, note TEXT, source TEXT);
CREATE INDEX edges_src ON edges(src_type, src_id);
CREATE INDEX edges_dst ON edges(dst_type, dst_id);
CREATE TABLE observations(entity_type TEXT, entity_id TEXT, series_id TEXT, year INT,
  value REAL, unit TEXT, source_family TEXT, paper_id TEXT, page INT);
CREATE INDEX obs_entity ON observations(entity_type, entity_id, series_id);
CREATE TABLE obs_map(ref_type TEXT, ref_id TEXT, obs_type TEXT, obs_id TEXT);
CREATE INDEX obs_map_ref ON obs_map(ref_type, ref_id);
"""

GENERIC_KINDS = {"contents", "title_page", "head_report"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def squash(s):
    return norm(s).replace(" ", "")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def nn(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, str) and v.strip() in ("", "nan", "None"):
        return None
    return v


def listify(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x for x in v.split("|") if x and x != "nan"]
    if isinstance(v, float):
        return []
    return [x for x in list(v) if x]


def read_body(tag):
    raw = (DIA_MD / f"{tag}.md").read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"')
    return (raw[m.end():] if m else raw), fm


def rd(path, **kw):
    p = ROOT / path
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, **kw) if p.suffix == ".csv" else pd.read_parquet(p)


def year_of(v):
    try:
        y = pd.to_datetime(v, errors="coerce")
        return None if pd.isna(y) else int(y.year)
    except Exception:
        return None


class Builder:
    def __init__(self, con, url_of, uri_of):
        self.con = con
        self.url_of = url_of
        self.uri_of = uri_of
        self.ents = {}          # (type, id) -> dict
        self.redir = {}         # (type, id) -> canonical id
        self.aliases = []
        self.edges = []

    # ---- helpers
    def canon(self, etype, eid):
        seen = set()
        while (etype, eid) in self.redir and eid not in seen:
            seen.add(eid)
            eid = self.redir[(etype, eid)]
        return eid

    def add_entity(self, etype, eid, name, uri=None, url=None, region=None, qid=None,
                   kind=None, first_year=None, last_year=None, n_years=None, summary=""):
        key = (etype, eid)
        if key in self.ents:
            return self.ents[key]
        uri = uri or self.uri_of.get(key) or f"{etype}:{eid}"
        url = url or self.url_of.get(key)
        d = dict(uri=uri, type=etype, id=eid, name=name, url=url, region=region, qid=qid,
                 kind=kind, first_year=first_year, last_year=last_year, n_years=n_years,
                 summary=summary, n_mentions=0)
        self.ents[key] = d
        return d

    def redirect(self, etype, old, new, reason):
        if old != new:
            self.redir[(etype, old)] = new

    def alias(self, alias, etype, eid, n=1, source="registry"):
        a = norm(alias)
        if a and len(a) >= 2:
            self.aliases.append((a, etype, eid, n, source))

    def edge(self, st, sid, edge, dt, did, year=None, note=None, source=None):
        self.edges.append((st, sid, edge, dt, did, year, note, source))


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
    name_of = {(v["type"], v["id"]): v["name"] for v in index_json.values()}

    suffix = "_sample" if args.sample else ""
    men = rd(f"registries/annotations/mentions_dia{suffix}.parquet")
    if not args.sample:
        t2 = rd("registries/annotations/mentions_dia_tier2.parquet")
        if len(t2):
            men = pd.concat([men, t2], ignore_index=True)
    men["report_year"] = men.tag.str.extract(r"(\d{4})").astype(int)
    segs = rd("registries/documents/dia_segments.parquet")
    segs["report_year"] = segs.tag.str.extract(r"(\d{4})").astype(int)
    if args.sample:
        segs = segs[segs.tag.isin(set(men.tag))]
    chains = rd("registries/entities/agency_chains.parquet")
    members = rd("registries/entities/agency_chain_members.parquet")
    reserves = rd("registries/entities/reserves.parquet")
    bands = rd("registries/entities/bands.parquet")
    persons = rd(f"registries/entities/persons_minted{suffix}.parquet")
    teachers = rd("registries/entities/persons_teachers.parquet")
    lincs = rd("registries/external/lincs_ia_activities_dedup.parquet")
    xw = rd("registries/crosswalks/dia_sessional.csv").set_index("tag")
    events = rd("curation/agency_chain_events.csv")
    au = rd("curation/schedule_area_units.csv")
    area_units = dict(au[["division_norm", "unit"]].values) if len(au) else {}
    attribution = rd("registries/annotations/segment_attribution.parquet")
    schools = rd("registries/entities/schools.parquet")
    school_att = rd("registries/annotations/school_attestations.parquet")
    teacher_att = rd("registries/annotations/teacher_attestations.parquet")
    officer_att = rd("registries/annotations/officer_attestations.parquet")
    band_att = rd("registries/annotations/band_attestations.parquet")
    bands_census = rd("registries/entities/bands_census_grounded.parquet")
    band_canonical = rd("registries/crosswalks/band_canonical.csv")
    band_modern = rd("registries/crosswalks/band_modern.csv")
    agstat = rd("registries/entities/agencies_agstat.parquet")
    chain_canonical = rd("registries/crosswalks/agency_chain_canonical.csv")
    reserve_wd = rd("registries/crosswalks/reserve_wikidata.csv")
    reserve_modern = rd("registries/crosswalks/reserve_modern.csv")
    reserve_att = rd("registries/annotations/reserve_attestations.parquet")
    obs = rd("registries/annotations/observations.parquet") if not args.sample else pd.DataFrame()

    con = sqlite3.connect(out)
    con.executescript(SCHEMA)
    B = Builder(con, url_of, uri_of)

    # ------------------------------------------------------------ agencies
    canon2chain = dict(zip(members.canonical, members.chain_id))
    canon2chain.update(dict(zip(chains.canonical, chains.chain_id)))
    chain_ids = set(chains.chain_id)

    def chain_for(name):
        """Agency string as printed in a table → chain id, via the heading
        canonicaliser (handles '- Con.', district prefixes, OCR fixes)."""
        if not nn(name):
            return None
        s = str(name)
        for cand in (s, s + " AGENCY"):
            c, _ = canonicalize(cand)
            if c and c in canon2chain:
                return canon2chain[c]
        return None

    chain_alias = (dict(zip(chain_canonical.chain_id, chain_canonical.canonical_chain_id))
                   if len(chain_canonical) else {})
    for cid, target in chain_alias.items():
        B.redirect("agency", cid, target, "OCR/printer variant chain (alias_agency_chains)")
    for c in chains.itertuples():
        if c.chain_id in chain_alias:
            continue
        name = name_of.get(("agency", c.chain_id)) or c.canonical.title()
        provs = ", ".join(reserves[reserves.division_norm == c.canonical].province.dropna().unique()) \
            if len(reserves) else ""
        summary = (f"{c.unit_type.title()} of the Department of Indian Affairs, attested in "
                   f"annual-report headings {c.first_year}–{c.last_year} ({c.n_variants} name variants)"
                   + (f"; LINCS {c.lincs_uri}" if nn(c.lincs_uri) else ""))
        frags = [f for f, t in chain_alias.items() if t == c.chain_id]
        fy = min([int(c.first_year)] + [int(chains[chains.chain_id == f].first_year.iat[0]) for f in frags])
        ly = max([int(c.last_year)] + [int(chains[chains.chain_id == f].last_year.iat[0]) for f in frags])
        ny = int(c.n_years) + sum(int(chains[chains.chain_id == f].n_years.iat[0]) for f in frags)
        if frags:
            summary += "; variant chains merged: " + ", ".join(
                chains[chains.chain_id == f].canonical.iat[0] for f in frags)
        B.add_entity("agency", c.chain_id, name, uri=c.uri, region=provs or None,
                     kind=c.unit_type, first_year=fy, last_year=ly, n_years=ny, summary=summary)
        for cname in [c.canonical] + [chains[chains.chain_id == f].canonical.iat[0] for f in frags]:
            B.alias(cname, "agency", c.chain_id)
            bare = re.sub(r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE)\b", "", cname).strip(" -")
            if len(bare) > 4:
                B.alias(bare, "agency", c.chain_id, source="bare")
    for m in members.drop_duplicates(["name", "chain_id"]).itertuples():
        B.alias(m.name, "agency", m.chain_id, source="variant")
    for e in events.itertuples() if len(events) else []:
        B.edge("agency", e.from_chain, e.event_type, "agency", e.to_chain,
               int(e.year_approx), f"{e.confidence}: {e.evidence}", "curation/agency_chain_events.csv")

    # agstat identities: agencies without a chain of their own; the rest redirect
    agstat_key = {}
    dl_links = rd("registries/crosswalks/agstat_chain_dateline_links.csv")
    dl_link = ({r.agency_id: r.chain_id for r in dl_links.itertuples()
                if not nn(r.other_chains) and r.n_datelines >= 2} if len(dl_links) else {})
    if len(agstat):
        if "identity_kind" not in agstat.columns:
            agstat["identity_kind"] = "agency"
        # "Halifax" / "Halifax County" / "(t)Halifax County" are one county unit:
        # same key within a province pool → the longest-attested identity
        agu = agstat[agstat.identity_kind == "agency"].copy()
        agu["_key"] = [squash(re.sub(r"\b(agency|ag'cy|superintendency|superintend'cy|inspectorate|"
                                     r"indian|county|counties|co)\b", " ", str(n), flags=re.I))
                       for n in agu.name]
        dup_target = {}
        for (k, pool), g in agu.groupby(["_key", agu.pool.fillna("")]):
            if len(g) > 1 and len(k) >= 5:
                best = g.sort_values("n_years", ascending=False).agency_id.iat[0]
                for aid in g.agency_id:
                    if aid != best:
                        dup_target[aid] = best
        n_dup = 0
        for r in agu.itertuples():
            names = [r.name] + listify(r.name_variants)
            if nn(getattr(r, "name_clean", None)):
                names.append(r.name_clean)
            target = r.chain_id if nn(r.link_method) and r.chain_id in chain_ids else None
            if target is None and dl_link.get(r.agency_id) in chain_ids:
                target = dl_link[r.agency_id]   # named beside the chain in letter datelines
            if target is None and r.agency_id in dup_target:
                target = dup_target[r.agency_id]
                n_dup += 1
            if target is None:
                for n in names:
                    target = chain_for(n)
                    if target:
                        break
            if target:
                B.redirect("agency", r.agency_id, target, "agstat identity linked to chain")
                for n in names:
                    B.alias(n, "agency", target, source="agstat")
            else:
                display = nn(getattr(r, "name_clean", None)) or r.name
                pool = nn(r.pool)
                B.add_entity("agency", r.agency_id, display, region=pool, kind="agstat_unit",
                             first_year=int(r.first_year), last_year=int(r.last_year),
                             n_years=int(r.n_years),
                             summary=f"Reporting unit of the Agricultural and Industrial Statistics "
                                     f"tables, {r.first_year}–{r.last_year}"
                                     + (f", {pool}" if pool else "")
                                     + "; no narrative-report heading of its own")
                for n in names:
                    B.alias(n, "agency", r.agency_id, source="agstat")
            for n in names:
                k = squash(re.sub(r"\b(agency|ag'cy|superintendency|superintend'cy|"
                                  r"inspectorate|indian)\b", " ", str(n), flags=re.I))
                if len(k) >= 5:
                    agstat_key.setdefault(k, set()).add(target or r.agency_id)

    print(f"agstat duplicate-key units folded: {n_dup}")

    def agency_for(name):
        """Table agency string → chain, else an agstat unit (unique key)."""
        c = chain_for(name)
        if c:
            return c
        k = squash(re.sub(r"\b(agency|ag'cy|superintendency|inspectorate|indian|con)\b", " ",
                          str(name), flags=re.I))
        t = agstat_key.get(k)
        if t and len(t) == 1:
            return next(iter(t))
        return None

    # ------------------------------------------------------------ reserves
    def rk(n, p, no, b):
        return (norm(n or ""), norm(p or ""), norm(no or ""), norm(b or ""))

    reserve_by_key = defaultdict(list)
    for r in reserves.itertuples():
        reserve_by_key[rk(nn(r.name), nn(r.province), nn(r.reserve_no), nn(r.band_norm))].append(r.reserve_id)
    reserve_qid, reserve_modern_name = {}, {}
    for r in reserve_wd.itertuples() if len(reserve_wd) else []:
        ids = reserve_by_key.get(rk(nn(r.hist_name), nn(r.hist_province), nn(r.hist_reserve_no), nn(r.hist_band)), [])
        if len(ids) == 1 and nn(r.qid):
            reserve_qid[ids[0]] = (r.qid, nn(r.wd_label), nn(r.clss_code), nn(r.coord))
    for r in reserve_modern.itertuples() if len(reserve_modern) else []:
        ids = reserve_by_key.get(rk(nn(r.hist_name), nn(r.hist_province), nn(r.hist_reserve_no), nn(r.hist_band)), [])
        if len(ids) == 1:
            reserve_modern_name[ids[0]] = (nn(r.modern_name), nn(r.clss_code), nn(r.tier))
    band_by_norm = {b.band_id: b.band_id for b in bands.itertuples()}
    for bn in reserves.band_norm.dropna().unique():
        if "BAND-" + slug(bn) in band_by_norm:
            band_by_norm[bn] = "BAND-" + slug(bn)
    reserve_name = {}
    for r in reserves.itertuples():
        no = "" if nn(r.reserve_no) is None else f" No. {r.reserve_no}"
        name = name_of.get(("reserve", r.reserve_id)) or (
            (r.name if nn(r.name) else f"{nn(r.band_norm) or (r.division_norm or '').title()} reserve") + no)
        reserve_name[r.reserve_id] = name
        q = reserve_qid.get(r.reserve_id)
        mod = reserve_modern_name.get(r.reserve_id)
        summary = "; ".join(filter(None, [
            f"band {r.band_norm}" if nn(r.band_norm) else None,
            f"{r.division_type} {str(r.division_norm).title()}" if nn(r.division_norm) else None,
            str(r.province).title() if nn(r.province) else None,
            r.location if nn(r.location) else None,
            (f"{r.acres_text} sq. miles (≈ {r.acres * 640:,.0f} acres; printed under an 'Acres' header, "
             f"see curation/schedule_area_units.csv)"
             if nn(r.acres_text) and area_units.get(r.division_norm) == "square_miles"
             else f"{r.acres_text} acres" if nn(r.acres_text) else None),
            f"Schedule of Indian Reserves 1902 p. {r.page}",
            f"succeeded by modern reserve {mod[0]} (CLSS {mod[1]})" if mod and mod[0] else None,
            f"Wikidata {q[0]} {q[1] or ''}".strip() if q else None]))
        B.add_entity("reserve", r.reserve_id, name, uri=r.uri, region=nn(r.province),
                     qid=q[0] if q else None, kind=nn(r.division_type), first_year=1902,
                     last_year=1902, n_years=1, summary=summary)
        if nn(r.name):
            B.alias(r.name, "reserve", r.reserve_id)
            if no:
                B.alias(f"{r.name} {no.strip()}", "reserve", r.reserve_id)
        if q and q[1]:
            B.alias(q[1], "reserve", r.reserve_id, source="wikidata")
        if mod and mod[0]:
            B.alias(mod[0], "reserve", r.reserve_id, source="modern")
        if r.division_type == "agency" and nn(r.division_norm):
            B.edge("reserve", r.reserve_id, "ADMINISTERED_BY", "agency", "AG-" + slug(r.division_norm),
                   1902, "Schedule of Indian Reserves 1902", "schedule_1902")
        b = band_by_norm.get(r.band_norm)
        if b:
            B.edge("band", b, "OCCUPIES", "reserve", r.reserve_id, 1902,
                   "Schedule of Indian Reserves 1902", "schedule_1902")
        if q:
            B.edge("reserve", r.reserve_id, "SUCCEEDED_BY", "wikidata", q[0], None,
                   q[1] or mod[0] if mod else q[1], "reserve_wikidata.csv")
    # earlier Schedule editions: reserve → agency per edition year
    if len(reserve_att):
        seen = set()
        for a in reserve_att.itertuples():
            if int(a.edition) == 1902 or not nn(a.reserve_id) or not nn(a.division):
                continue
            c = chain_for(a.division)
            if c and (a.reserve_id, c, int(a.edition)) not in seen:
                seen.add((a.reserve_id, c, int(a.edition)))
                B.edge("reserve", a.reserve_id, "ADMINISTERED_BY", "agency", c, int(a.edition),
                       f"Schedule of Indian Reserves {a.edition} ({a.match_tier})", "schedule_editions")

    # ------------------------------------------------------------ bands
    for b in bands.itertuples():
        provs = ", ".join(listify(b.provinces))
        summary = (f"Historical band (DIA, 1902 Schedule); {b.n_reserves} reserves"
                   + (f", {b.total_acres:,.0f} acres" if nn(b.total_acres) else "")
                   + (f"; succeeded by {b.wd_label} ({b.succeeded_by_qid})" if nn(b.succeeded_by_qid) else "")
                   + (f"; people: {b.wd_label} ({b.people_qid})" if nn(b.people_qid) and not nn(b.succeeded_by_qid) else "")
                   + (f"; note: {b.notes}" if nn(getattr(b, 'notes', None)) else ""))
        B.add_entity("band", b.band_id, b.name, uri=b.uri, region=provs or None,
                     qid=nn(b.succeeded_by_qid) or nn(b.people_qid), kind="curated_1902",
                     first_year=1902, last_year=1902, n_years=1, summary=summary)
        B.alias(b.name, "band", b.band_id)
        if nn(b.wd_label):
            B.alias(b.wd_label, "band", b.band_id, source="wikidata")
        if nn(b.succeeded_by_qid):
            B.edge("band", b.band_id, "SUCCEEDED_BY", "wikidata", b.succeeded_by_qid, None,
                   b.wd_label, "bands.parquet (adjudicated)")

    # census bands: aliasing (band_canonical) then bridge to curated ids
    canon_census = dict(zip(band_canonical.band_id, band_canonical.canonical_band_id)) \
        if len(band_canonical) else {}
    census_info = {r.band_id: r for r in bands_census.itertuples()} if len(bands_census) else {}
    curated_of = {r.band_id: r.curated_band_id for r in bands_census.itertuples()
                  if len(bands_census) and nn(r.curated_band_id)}
    modern_of = {r.band_id: r for r in band_modern.itertuples()} if len(band_modern) else {}

    def census_target(bid):
        """Where a census band id lives: its canonical census id, or the
        curated 1902 band that canonical is bridged to."""
        c = canon_census.get(bid, bid)
        cur = curated_of.get(c) or curated_of.get(bid)
        return cur or c

    for bid, r in census_info.items():
        target = census_target(bid)
        names = [r.name] + listify(r.name_variants)
        if target != bid:
            reason = ("bridged to curated 1902 band" if target.startswith("BAND-")
                      else "aliased by alias_census_bands")
            B.redirect("band", bid, target, reason)
        if target.startswith("BAND-"):
            e = B.ents.get(("band", target))
            if e is not None:
                e["first_year"] = min(e["first_year"] or 9999, int(r.first_year))
                e["last_year"] = max(e["last_year"] or 0, int(r.last_year))
                e["n_years"] = (e["n_years"] or 0) + int(r.n_years)
                if "census" not in (e["kind"] or ""):
                    e["kind"] = "curated_1902+census"
            for n in names:
                B.alias(n, "band", target, source="census")
            continue
        if target != bid:
            for n in names:
                B.alias(n, "band", target, source="census")
            continue
        m = modern_of.get(bid)
        members_ = [x for x, c in canon_census.items() if c == bid and x != bid]
        span_first = min([int(r.first_year)] + [int(census_info[x].first_year) for x in members_ if x in census_info])
        span_last = max([int(r.last_year)] + [int(census_info[x].last_year) for x in members_ if x in census_info])
        n_years = int(r.n_years) + sum(int(census_info[x].n_years) for x in members_ if x in census_info)
        summary = (f"Band as listed in the DIA census tables {span_first}–{span_last} "
                   f"({n_years} report years), agency {r.agency}"
                   + (f", {r.province_pool}" if nn(r.province_pool) else "")
                   + (f"; population {int(r.population_first)} → {int(r.population_last)}"
                      if nn(r.population_first) and nn(r.population_last) else "")
                   + (f"; succeeded by {m.modern_name} (band no. {m.band_no}, {m.qid})"
                      if m is not None and nn(m.qid) else "")
                   + (f"; merged identities: {', '.join(members_)}" if members_ else ""))
        B.add_entity("band", bid, r.name, uri=nn(r.uri), region=nn(r.province_pool),
                     qid=nn(m.qid) if m is not None else None, kind="census",
                     first_year=span_first, last_year=span_last, n_years=n_years, summary=summary)
        for n in names:
            B.alias(n, "band", bid, source="census")
        if m is not None and nn(m.qid):
            B.alias(m.modern_name, "band", bid, source="wikidata")
            B.edge("band", bid, "SUCCEEDED_BY", "wikidata", m.qid, None,
                   f"{m.modern_name} (band no. {m.band_no}; {m.tier})", "band_modern.csv")

    # band → agency per census year
    if len(band_att):
        seen = set()
        n_hit = 0
        for a in band_att.dropna(subset=["agency"]).itertuples():
            target = agency_for(a.agency)
            if not target:
                continue
            bid = B.canon("band", a.band_id)
            key = (bid, target, int(a.year))
            if key in seen:
                continue
            seen.add(key)
            n_hit += 1
            B.edge("band", bid, "ADMINISTERED_BY", "agency", target, int(a.year),
                   f"census table: {a.agency}", "band_attestations")
        print(f"band ADMINISTERED_BY edges: {n_hit:,}")

    # ------------------------------------------------------------ schools
    school_name = {}
    if len(schools):
        for s in schools.itertuples():
            summary = "; ".join(filter(None, [
                f"{s.school_type} school" if nn(s.school_type) else "school",
                nn(s.province_pool),
                f"agency {s.agencies.replace('|', ', ')}" if nn(s.agencies) else None,
                f"reserve {s.reserves.replace('|', ', ')}" if nn(s.reserves) else None,
                nn(s.denominations).replace("|", ", ") if nn(s.denominations) else None,
                f"attested {s.first_year}–{s.last_year} in the School Statements"]))
            school_name[s.school_id] = s.name
            B.add_entity("school", s.school_id, s.name, uri=f"school:{s.school_id}",
                         region=nn(s.province_pool), kind=nn(s.school_type),
                         first_year=int(s.first_year), last_year=int(s.last_year),
                         n_years=int(s.n_years), summary=summary)
            B.alias(s.name, "school", s.school_id)
            for v in listify(s.name_variants):
                B.alias(v, "school", s.school_id, source="variant")
    reserve_by_name = defaultdict(set)
    for r in reserves.itertuples():
        if nn(r.name):
            reserve_by_name[norm(r.name)].add(r.reserve_id)
    if len(school_att):
        seen = set()
        for a in school_att.itertuples():
            y = int(a.year)
            if nn(a.agency):
                t = agency_for(a.agency)
                if t and (a.school_id, t, y) not in seen:
                    seen.add((a.school_id, t, y))
                    B.edge("school", a.school_id, "IN_AGENCY", "agency", t, y,
                           f"School Statement: {a.agency}", "school_attestations")
            if nn(a.reserve):
                k = norm(re.sub(r"\b(reserve|i\.?r\.?)\b", " ", str(a.reserve), flags=re.I))
                ids = reserve_by_name.get(k, set())
                if len(ids) == 1:
                    rid = next(iter(ids))
                    if (a.school_id, rid, y) not in seen:
                        seen.add((a.school_id, rid, y))
                        B.edge("school", a.school_id, "ON_RESERVE", "reserve", rid, y,
                               f"School Statement: {a.reserve}", "school_attestations")

    # ------------------------------------------------------------ persons
    to_lincs = {}
    if len(persons) and "lincs_agent" in persons.columns:
        to_lincs.update({p: a for p, a in zip(persons.person_id, persons.lincs_agent) if nn(a)})
    if len(teachers) and "lincs_agent" in teachers.columns:
        to_lincs.update({p: a for p, a in zip(teachers.person_id, teachers.lincs_agent) if nn(a)})
    for p, a in to_lincs.items():
        B.redirect("person", p, a, "re-anchored to LINCS agent")
    lincs_label = (lincs.groupby("agent").agent_label.agg(lambda s: s.mode().iat[0]).to_dict()
                   if len(lincs) else {})
    person_pages = {k: v for k, v in url_of.items() if k[0] == "person"}
    off_by = ({a: g for a, g in officer_att[officer_att.lincs_agent.fillna("") != ""].groupby("lincs_agent")}
              if len(officer_att) and "lincs_agent" in officer_att.columns else {})
    if len(teacher_att):
        teacher_att = teacher_att.assign(cid=teacher_att.person_id.map(lambda x: to_lincs.get(x, x)))
    teach_by = {c: g for c, g in teacher_att.groupby("cid")} if len(teacher_att) else {}
    pm = persons.set_index("person_id") if len(persons) else pd.DataFrame()
    tr = teachers.set_index("person_id") if len(teachers) else pd.DataFrame()
    sig_by = {a: g for a, g in men[men.entity_type == "agent"].assign(
        cid=men[men.entity_type == "agent"].entity_id.map(lambda x: to_lincs.get(x, x))).groupby("cid")}
    # every LINCS agent is an entity, attested here or not: the authority owns
    # the identity and the postings give it POSTED_TO edges
    all_person_ids = (set(k[1] for k in person_pages) | set(off_by) | set(teach_by) | set(sig_by)
                      | set(lincs_label))
    for pid in sorted(all_person_ids):
        name = name_of.get(("person", pid)) or lincs_label.get(pid)
        parts, kinds, years = [], [], []
        if pid in lincs_label:
            acts = lincs[lincs.agent == pid]
            if len(acts):
                y0 = [year_of(v) for v in acts.begin]
                y0 = [y for y in y0 if y]
                years += y0
                groups = acts.group_label.dropna().unique().tolist()
                parts.append(f"LINCS Indian Affairs agent; postings: {', '.join(groups[:6])}"
                             + (" …" if len(groups) > 6 else ""))
            kinds.append("officer")
        o = off_by.get(pid)
        if o is not None:
            desig = o.designation.dropna().value_counts()
            years += [int(y) for y in o.year]
            parts.append(f"Return A {int(o.year.min())}–{int(o.year.max())} ({len(o)} rows"
                         + (f"; {', '.join(desig.index[:3])}" if len(desig) else "") + ")")
            kinds.append("officer")
            if not name:
                name = o.name_as_printed.mode().iat[0]
        g = teach_by.get(pid)
        if g is not None:
            years += [int(y) for y in g.year]
            sch = g.school_id.unique()
            parts.append(f"taught {int(g.year.min())}–{int(g.year.max())} at {len(sch)} school(s): "
                         + ", ".join(school_name.get(s, s) for s in sch[:4]))
            kinds.append("teacher")
            if not name and pid in tr.index:
                name = tr.loc[pid, "name"]
        sg = sig_by.get(pid)
        if sg is not None:
            years += [int(y) for y in sg.report_year]
            parts.append(f"signed/mentioned in {sg.drop_duplicates(['tag', 'segment_id']).shape[0]} report letters")
            kinds.append("signatory")
            if not name and pid in pm.index:
                name = str(pm.loc[pid, "name"]).title()
        if not name:
            name = pid
        uri = uri_of.get(("person", pid)) or (pid if pid.startswith("http") else
                                             (pm.loc[pid, "uri"] if pid in pm.index else
                                              tr.loc[pid, "uri"] if pid in tr.index else f"person:{pid}"))
        B.add_entity("person", pid, name, uri=uri, kind="/".join(sorted(set(kinds))) or None,
                     first_year=min(years) if years else None, last_year=max(years) if years else None,
                     n_years=len(set(years)) if years else None,
                     summary="; ".join(parts) + ("" if pid.startswith("http") else "; minted identity (no LINCS match)"))
        B.alias(name, "person", pid)
        if pid in lincs_label:
            B.alias(lincs_label[pid], "person", pid, source="lincs")
        if o is not None:
            for n_ in o.name_as_printed.dropna().unique():
                B.alias(n_, "person", pid, source="return_a")
        if g is not None:
            for n_ in g.teacher_as_printed.dropna().unique():
                B.alias(n_, "person", pid, source="school_statement")
            for r_ in g.drop_duplicates(["year", "school_id"]).itertuples():
                B.edge("person", pid, "TAUGHT_AT", "school", r_.school_id, int(r_.year),
                       r_.teacher_as_printed, "teacher_attestations")
        for old in [p for p, a in to_lincs.items() if a == pid]:
            if old in pm.index:
                B.alias(pm.loc[old, "name"], "person", pid, source="signature")
            if old in tr.index:
                B.alias(tr.loc[old, "name"], "person", pid, source="school_statement")
        if pid in pm.index:
            B.alias(pm.loc[pid, "name"], "person", pid, source="signature")
        if pid in tr.index:
            B.alias(tr.loc[pid, "name"], "person", pid, source="school_statement")

    # LINCS postings → per-year POSTED_TO edges
    lincs2chain = {u: c for c, u in zip(chains.chain_id, chains.lincs_uri) if nn(u)}
    # groups LINCS never linked to a chain, matched to a unit by name (Lorette,
    # Seven Islands, The Pas… are agstat units; "Six Nations" a chain)
    for g_, label in lincs.drop_duplicates("group")[["group", "group_label"]].itertuples(index=False) if len(lincs) else []:
        if g_ in lincs2chain or not nn(label):
            continue
        t = agency_for(label)
        if t:
            lincs2chain[g_] = t
    if len(lincs):
        n_posted = 0
        for a in lincs.itertuples():
            c = lincs2chain.get(a.group)
            if not c:
                continue
            y0, y1 = year_of(a.begin), year_of(a.end)
            if not y0:
                continue
            y1 = y1 or y0
            for y in range(y0, min(y1, y0 + 45) + 1):
                B.edge("person", a.agent, "POSTED_TO", "agency", c, y,
                       f"LINCS posting {y0}–{y1}: {a.group_label}, {a.place_label}", "lincs")
                n_posted += 1
        print(f"person POSTED_TO edges: {n_posted:,}")

    # ------------------------------------------------------------ documents + segments
    attr, letter_kind = {}, {}
    if len(attribution):
        for r in attribution.itertuples():
            if "letter_kind" in attribution.columns:
                letter_kind[(r.tag, r.segment_id)] = r.letter_kind
            # a single place mention is a hint for review, not an attribution
            if nn(r.entity_id) and r.confidence != "low":
                attr[(r.tag, r.segment_id)] = (r.entity_type, r.entity_id, r.method)
    seg_chain = {(m.tag, m.segment_id): m.chain_id for m in members.itertuples()}
    n_doc_segs = segs.groupby("doc_id").size()
    seg_agency = {}
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
            agency_id = seg_chain.get((tag, s.segment_id))
            agency_id = B.canon("agency", agency_id) if agency_id else None
            school_id, method = None, "heading" if agency_id else None
            a = attr.get((tag, s.segment_id))
            if a:
                if a[0] == "agency" and not agency_id:
                    agency_id, method = B.canon("agency", a[1]), a[2]
                elif a[0] == "school":
                    school_id, method = a[1], a[2]
            if agency_id:
                seg_agency[(doc_id, s.segment_id)] = agency_id
            rows.append((doc_id, s.segment_id, tag, year, int(s.ordinal), s.heading, s.kind,
                         agency_id, school_id, method, letter_kind.get((tag, s.segment_id)),
                         int(s.page_start), int(s.page_end),
                         s.text_version, editions.get(doc_id, {}).get("segments", {}).get(s.segment_id),
                         text))
            if school_id:
                B.edge("school", school_id, "REPORTED_IN", "segment", f"{doc_id} / {s.segment_id}",
                       year, s.heading, f"attribution:{method}")
        con.executemany("INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')")
    print(f"segments with an agency: {len(seg_agency):,} of {len(segs):,}")

    # ------------------------------------------------------------ mentions
    men = men.copy()
    seg_kind = dict(zip(zip(segs.doc_id, segs.segment_id), segs.kind))
    men["entity_type"] = men.entity_type.replace({"agent": "person"})
    men["entity_id"] = [B.canon(t, i) for t, i in zip(men.entity_type, men.entity_id)]
    # a lower-case common-noun surface ("fishing station") on a contents or
    # title page is a table-of-contents hit, not a mention of the reserve
    generic = (men.surface == men.surface.str.lower()) & \
        men.apply(lambda m: seg_kind.get((m.doc_id, m.segment_id)) in GENERIC_KINDS, axis=1)
    men.loc[generic, "confidence"] = "low"
    con.executemany("INSERT INTO mentions VALUES (?,?,?,?,?,?,?,?,?)", [
        (m.doc_id, m.segment_id, int(m.report_year), m.entity_type, m.entity_id, m.surface,
         m.confidence, int(m.char_start), int(m.char_end)) for m in men.itertuples()])
    prim = men[men.confidence.isin(["high", "medium"])]
    n_men = prim.groupby(["entity_type", "entity_id"]).size()
    for (t, i), n in n_men.items():
        e = B.ents.get((t, i))
        if e is not None:
            e["n_mentions"] = int(n)
    # signatures → SIGNED_FOR / REPORTED_ON per report year
    seen = set()
    for m in prim[prim.entity_type == "person"].itertuples():
        a = seg_agency.get((m.doc_id, m.segment_id))
        ctx = m.context_agency if nn(m.context_agency) else None
        target = a or ctx
        key_ = (m.doc_id, m.segment_id)
        if target and ("agency", m.entity_id, target, int(m.report_year)) not in seen:
            seen.add(("agency", m.entity_id, target, int(m.report_year)))
            B.edge("person", m.entity_id, "SIGNED_FOR", "agency", target, int(m.report_year),
                   f"signature in {m.doc_id} / {m.segment_id}", "mentions")
        sch = con.execute("SELECT school_id FROM segments WHERE doc_id=? AND segment_id=?", key_).fetchone()
        if sch and sch[0] and ("school", m.entity_id, sch[0], int(m.report_year)) not in seen:
            seen.add(("school", m.entity_id, sch[0], int(m.report_year)))
            B.edge("person", m.entity_id, "REPORTED_ON", "school", sch[0], int(m.report_year),
                   f"signature in {m.doc_id} / {m.segment_id}", "attribution")
    surf = Counter()
    for m in prim.itertuples():
        surf[(norm(m.surface), m.entity_type, m.entity_id)] += 1
    for (a, et, eid), n in surf.items():
        if a:
            B.aliases.append((a, et, eid, n, "mention"))

    # ------------------------------------------------------------ observations
    if len(obs):
        con.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)", [
            (o.entity_type, o.entity_id, o.series_id, int(o.year),
             None if pd.isna(o.value) else float(o.value), nn(o.unit),
             o.source_family, nn(o.paper_id), None if pd.isna(o.page) else int(o.page))
            for o in obs.itertuples()])
        obs_keys = obs.drop_duplicates(["entity_type", "entity_id"])[["entity_type", "entity_id"]]
        maps = set()
        for t, i in obs_keys.itertuples(index=False):
            c = B.canon(t, i)
            if c != i:
                maps.add((t, c, t, i))
        con.executemany("INSERT INTO obs_map VALUES (?,?,?,?)", sorted(maps))
        print(f"obs_map rows: {len(maps):,}")

    # ------------------------------------------------------------ write
    # resolve edge endpoints through redirects; drop edges to unknown entities
    known = set(B.ents)
    rows, dropped = [], Counter()
    for st, sid, edge, dt, did, year, note, source in B.edges:
        sid, did = B.canon(st, sid), (B.canon(dt, did) if dt in ("agency", "reserve", "band", "person", "school") else did)
        if (st, sid) not in known or (dt in ("agency", "reserve", "band", "person", "school") and (dt, did) not in known):
            dropped[edge] += 1
            continue
        rows.append((st, sid, edge, dt, did, year, note, source))
    con.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?,?)", sorted(set(rows), key=lambda r: (r[0], r[1], r[2], r[4], r[5] or 0)))
    if dropped:
        print("edges dropped (unknown endpoint):", dict(dropped))
    used = set()
    ent_rows = []
    for e in B.ents.values():
        uri = e["uri"]
        if uri in used:   # a minted uri that another identity already owns
            uri = f"{e['type']}:{e['id']}"
        used.add(uri)
        ent_rows.append((uri, e["type"], e["id"], e["name"], e["url"], e["region"], e["qid"], e["kind"],
                         e["first_year"], e["last_year"], e["n_years"], e["summary"], e["n_mentions"]))
    con.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ent_rows)
    con.executemany("INSERT INTO redirects VALUES (?,?,?,?)",
                    [(t, i, B.canon(t, i), "") for (t, i) in B.redir])
    al = Counter()
    for a, t, i, n, src in B.aliases:
        i = B.canon(t, i)
        if (t, i) in known:
            al[(a, t, i, src)] += n
    alias_rows = [(a, t, i, n, src) for (a, t, i, src), n in al.items()]
    con.executemany("INSERT INTO aliases VALUES (?,?,?,?,?)", alias_rows)
    con.executemany("INSERT INTO aliases_fts(rowid, alias_sq) VALUES (?,?)",
                    [(k + 1, a.replace(" ", "")) for k, (a, *_r) in enumerate(alias_rows)])
    con.commit()
    for t in ["documents", "segments", "entities", "redirects", "aliases", "mentions", "edges",
              "observations", "obs_map"]:
        print(f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]:,}")
    print("entities by type:", dict(con.execute("select type,count(*) from entities group by 1").fetchall()))
    print("edges by type:", dict(con.execute("select edge,count(*) from edges group by 1").fetchall()))
    con.close()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
