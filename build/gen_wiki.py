#!/usr/bin/env python3
"""Wiki generation v1 — deterministic static pages over the registries.

Pages (all under --out, default site/):
  agencies/{slug}/          one per agency chain
  reserves/{reserve_id}/    one per 1902-Schedule reserve
  bands/{slug}/             one per historical band
  persons/{slug}/           minted signatories + LINCS-matched agents
  reports/{year}/           one per DIA annual-report issue (TOC + citation)
  reports/{year}/{segment_id}/   full segment text, mentions rendered as links
  {facet}/index.html        facet indexes; index.html home
  index.json                entity → page map (for canada50-mcp)
  editions.json             (doc_id, segment_id) → URL map (docs/URL_SCHEME.md)

Local evaluation build only: DIA/reserve/band pages are NOT deployed
(DESIGN.md, publication constraint). Serve with
`python3 -m http.server -d site 8000`.

Mention rendering follows gate decision 1: high+medium mentions are primary;
low-confidence mentions render in an "unverified" fold and are not linked
inline in segment text.
"""
import argparse
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

import series_render

ROOT = Path(__file__).resolve().parent.parent
DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
SP_IDS = Path.home() / "sessional_papers/ids.txt"
ANN = ROOT / "registries/annotations"
ENT = ROOT / "registries/entities"

PRIMARY = {"high", "medium"}
PROV_SHORT = {"ONTARIO": "ON", "QUEBEC": "QC", "NOVA SCOTIA": "NS",
              "NEW BRUNSWICK": "NB", "PRINCE EDWARD ISLAND": "PE",
              "MANITOBA": "MB", "BRITISH COLUMBIA": "BC", "ALBERTA": "AB",
              "SASKATCHEWAN": "SK", "NORTH-WEST TERRITORIES": "NWT",
              "NORTHWEST TERRITORIES": "NWT", "YUKON": "YT"}
KIND_LABEL = {"agency_letter": "agency letter", "tabular_statement": "table",
              "thematic_section": "section", "return": "return",
              "appendix": "appendix", "presentation_letter": "presentation letter",
              "contents": "contents", "title_page": "title page",
              "head_report": "report"}

CSS = """
  body { font-family: -apple-system, "Segoe UI", sans-serif; max-width: 860px;
         margin: 2em auto; padding: 0 1em; line-height: 1.55; color: #222; }
  h1 { border-bottom: 1px solid #ddd; padding-bottom: .3em; margin-bottom: .5em; }
  h2 { margin-top: 1.6em; } h3 { margin-top: 1.2em; }
  table { border-collapse: collapse; margin: .5em 0; font-size: .95em; }
  th, td { border: 1px solid #ddd; padding: 3px 9px; vertical-align: top; }
  th { background: #f5f5f5; text-align: left; }
  .crumbs { font-size: .9em; color: #666; margin-bottom: .5em; }
  .meta { background: #f8f9fa; padding: .7em 1em; border-left: 3px solid #0066cc;
          margin: 1em 0; font-size: .92em; }
  .meta dl { display: grid; grid-template-columns: max-content 1fr; gap: .15em 1em; margin: 0; }
  .meta dt { font-weight: 600; } .meta dd { margin: 0; }
  a { color: #0055aa; } a.m-high, a.m-medium { text-decoration: none;
      border-bottom: 1px dotted #0055aa; }
  a.m-medium { border-bottom-color: #c90; } span.m-low { background: #fff7e0; }
  .q { color: #333; } .q b { background: #ffec99; font-weight: 600; }
  .cite { font-size: .85em; color: #666; }
  pre.txt { white-space: pre-wrap; font-family: Georgia, serif; font-size: .93em;
            line-height: 1.45; background: #fcfcfa; padding: 1em; border: 1px solid #eee; }
  .pg { display: block; color: #999; font-size: .8em; margin: .8em 0 .2em;
        border-top: 1px dashed #ddd; padding-top: .2em; font-family: sans-serif; }
  details { margin: .6em 0; } summary { cursor: pointer; color: #555; }
  footer { margin-top: 3em; font-size: .8em; color: #666; border-top: 1px solid #ddd;
           padding-top: 1em; }
  ul.compact { columns: 2; } ul.compact li { break-inside: avoid; }
  .badge { font-size: .75em; padding: 0 .4em; border-radius: 3px; background: #eee;
           color: #444; margin-left: .3em; }
"""
CSS += series_render.CSS

# --------------------------------------------------------------------------- utils

def esc(s):
    return html.escape("" if s is None or (isinstance(s, float) and pd.isna(s))
                       else str(s), quote=True)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def ws(s):
    return re.sub(r"\s+", " ", s).strip()


def is_null(v):
    return v is None or (isinstance(v, float) and pd.isna(v))


def listify(v):
    if is_null(v):
        return []
    if isinstance(v, str):
        return [v]
    return [x for x in list(v) if not is_null(x)]


def title_case(s):
    small = {"of", "the", "and", "de", "du", "des", "la", "le", "at", "on"}
    out = []
    for i, w in enumerate(str(s).split()):
        lw = w.lower()
        if i and lw in small:
            out.append(lw)
        elif re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]?", w):   # initials: G.H.
            out.append(w.upper())
        elif w.isupper() and len(w) > 1 or w.islower():
            out.append(w[:1].upper() + w[1:].lower())
        else:
            out.append(w)
    return " ".join(out)


class Site:
    def __init__(self, out, base):
        self.out = Path(out)
        self.base = base.rstrip("/")
        self.n = 0

    def url(self, *parts):
        path = "/".join(p.strip("/") for p in parts if p)
        return self.base + "/" + (path + "/" if path else "")

    def write(self, rel, body, title, crumbs, description=""):
        p = self.out / rel / "index.html" if not rel.endswith(".html") else self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        crumb_html = " › ".join(
            f'<a href="{esc(u)}">{esc(t)}</a>' if u else esc(t) for t, u in crumbs)
        p.write_text(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — Canada50</title>
<meta name="description" content="{esc(description[:300])}">
<style>{CSS}</style></head>
<body><article>
<div class="crumbs">{crumb_html}</div>
{body}
</article>
<footer>Canada50 wiki · local evaluation build (not for deployment — see
DESIGN.md, publication constraint). Generated by <code>build/gen_wiki.py</code>
from the registries; corrections go to <code>curation/</code>.</footer>
</body></html>""", encoding="utf-8")
        self.n += 1


# --------------------------------------------------------------------------- data

def load(sample):
    d = {}
    suffix = "_sample" if sample else ""
    d["mentions"] = pd.read_parquet(ANN / f"mentions_dia{suffix}.parquet")
    t2 = ANN / "mentions_dia_tier2.parquet"
    if not sample and t2.exists():   # Qwen Tier-2 residual decisions (build/apply_residual_decisions.py)
        d["mentions"] = pd.concat([d["mentions"], pd.read_parquet(t2)], ignore_index=True)
    d["segments"] = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet")
    d["segments"]["report_year"] = d["segments"].tag.str.extract(r"(\d{4})").astype(int)
    if sample:
        tags = set(d["mentions"].tag)
        d["segments"] = d["segments"][d["segments"].tag.isin(tags)]
    d["chains"] = pd.read_parquet(ENT / "agency_chains.parquet")
    d["members"] = pd.read_parquet(ENT / "agency_chain_members.parquet")
    d["reserves"] = pd.read_parquet(ENT / "reserves.parquet")
    d["bands"] = pd.read_parquet(ENT / "bands.parquet")
    pm = ENT / f"persons_minted{suffix}.parquet"
    d["persons"] = pd.read_parquet(pm) if pm.exists() else pd.DataFrame(
        columns=["person_id", "name", "first_year", "last_year", "n_signatures",
                 "chains", "uri"])
    d["lincs"] = pd.read_parquet(ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    d["dia_sessional"] = pd.read_csv(ROOT / "registries/crosswalks/dia_sessional.csv")
    ev = ROOT / "curation/agency_chain_events.csv"
    d["events"] = pd.read_csv(ev) if ev.exists() else pd.DataFrame()
    au = ROOT / "curation/schedule_area_units.csv"
    d["area_units"] = dict(pd.read_csv(au)[["division_norm", "unit"]].values) if au.exists() else {}
    at = ROOT / "registries/annotations/reserve_attestations.parquet"
    d["attestations"] = pd.read_parquet(at) if at.exists() else pd.DataFrame()
    d["obs"] = series_render.load()
    bg = ENT / "bands_census_grounded.parquet"
    d["bands_grounded"] = pd.read_parquet(bg) if bg.exists() else pd.DataFrame()
    sc = ENT / "schools.parquet"
    d["schools"] = pd.read_parquet(sc) if sc.exists() else pd.DataFrame()
    sa = ANN / "school_attestations.parquet"
    d["school_att"] = pd.read_parquet(sa) if sa.exists() else pd.DataFrame()
    oa = ANN / "officer_attestations.parquet"
    d["officer_att"] = pd.read_parquet(oa) if oa.exists() else pd.DataFrame()
    ta = ANN / "teacher_attestations.parquet"
    d["teacher_att"] = pd.read_parquet(ta) if ta.exists() else pd.DataFrame()
    pt = ENT / "persons_teachers.parquet"
    d["persons_teachers"] = pd.read_parquet(pt) if pt.exists() else pd.DataFrame()
    se = ANN / "school_events.parquet"
    d["school_events"] = pd.read_parquet(se) if se.exists() else pd.DataFrame()
    d["sp_ids"] = set(SP_IDS.read_text().split()) if SP_IDS.exists() else set()
    # v2 (2026-09-02): letter attribution, census-band registry, alias crosswalks
    at = ANN / "segment_attribution.parquet"
    d["attribution"] = pd.read_parquet(at) if at.exists() else pd.DataFrame()
    ba = ANN / "band_attestations.parquet"
    d["band_att"] = pd.read_parquet(ba) if ba.exists() else pd.DataFrame()
    xw = ROOT / "registries/crosswalks"
    d["band_canonical"] = pd.read_csv(xw / "band_canonical.csv") if (xw / "band_canonical.csv").exists() else pd.DataFrame()
    d["band_modern"] = pd.read_csv(xw / "band_modern.csv") if (xw / "band_modern.csv").exists() else pd.DataFrame()
    d["chain_canonical"] = (pd.read_csv(xw / "agency_chain_canonical.csv")
                            if (xw / "agency_chain_canonical.csv").exists() else pd.DataFrame())
    return d


def read_body(tag):
    raw = (DIA_MD / f"{tag}.md").read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"')
    return (raw[m.end():] if m else raw), fm


PAGE_RE = re.compile(r"<!-- page (\d+) -->")


def chain_slug(chain_id):
    return chain_id[3:] if chain_id.startswith("AG-") else slug(chain_id)


def band_slug(band_id):
    return band_id[5:] if band_id.startswith("BAND-") else slug(band_id)


def person_slug(entity_id):
    if entity_id.startswith("PERSON-"):
        return entity_id[7:]
    if "lincsproject.ca/" in entity_id:
        return "lincs-" + entity_id.rsplit("/", 1)[1]
    if "viaf.org" in entity_id:
        return "viaf-" + entity_id.rstrip("/").rsplit("/", 1)[1]
    if "wikidata.org" in entity_id:
        return "wd-" + entity_id.rsplit("/", 1)[1]
    return slug(entity_id)


# --------------------------------------------------------------------------- build

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--base", default="", help="URL prefix, e.g. /canada50")
    ap.add_argument("--sample", action="store_true",
                    help="use *_sample annotations (gate volumes only)")
    ap.add_argument("--max-passages", type=int, default=4,
                    help="passages shown per entity per report year")
    args = ap.parse_args()
    site = Site(args.out, args.base)
    d = load(args.sample)
    obs = d["obs"]
    # build/merge_band_registries.py carries the curated 1902 band ids onto the
    # census registry the observations are keyed by; invert it so a band page
    # can find its own series
    census_band_of = {}
    if len(d["bands_grounded"]):
        for r in d["bands_grounded"].itertuples():
            if r.curated_band_id:
                census_band_of.setdefault(r.curated_band_id, r.band_id)

    def series_for(entity_id):
        return series_render.series_section(obs, entity_id) if len(obs) and entity_id else ""
    men, segs = d["mentions"], d["segments"]
    print(f"mentions {len(men):,} | segments {len(segs):,} | "
          f"volumes {segs.tag.nunique()}")

    # ---- resolve entity → (page url, display name, type)
    chains = d["chains"].set_index("chain_id")
    chain_alias = (dict(zip(d["chain_canonical"].chain_id, d["chain_canonical"].canonical_chain_id))
                   if len(d["chain_canonical"]) else {})

    def canon_chain(cid):
        return chain_alias.get(cid, cid)
    chain_name = {c: title_case(chains.loc[canon_chain(c), "canonical"]) if canon_chain(c) in chains.index
                  else title_case(r.canonical) for c, r in chains.iterrows()}
    # OCR-variant chains (alias_agency_chains) fold into their canonical chain:
    # mentions, letters and signers move; no page is written for the fragment
    men = men.copy()
    is_ag = men.entity_type == "agency"
    men.loc[is_ag, "entity_id"] = men.loc[is_ag, "entity_id"].map(canon_chain)
    men["context_agency"] = men.context_agency.map(lambda c: canon_chain(c) if isinstance(c, str) else c)
    d["members"] = d["members"].assign(chain_id=d["members"].chain_id.map(canon_chain))
    reserves = d["reserves"].set_index("reserve_id")
    bands = d["bands"].set_index("band_id")
    band_name = {b: r["name"] for b, r in bands.iterrows()}
    band_by_norm = {}
    for b in bands.index:
        band_by_norm[b] = b
    for bn in d["reserves"].band_norm.dropna().unique():
        cand = "BAND-" + slug(bn)
        if cand in bands.index:
            band_by_norm[bn] = cand
    persons = d["persons"].set_index("person_id") if len(d["persons"]) else pd.DataFrame()
    lincs = d["lincs"]
    lincs_name = lincs.groupby("agent").agent_label.agg(lambda s: s.mode().iat[0])

    # ---- unified person identity: LINCS URI when the authority owns it,
    # the minted PERSON-id otherwise. Signatories re-anchored by
    # link_signatories_lincs.py and teachers by link_teachers.py alias to
    # their agent so one person gets one page.
    teachers_reg = (d["persons_teachers"].set_index("person_id")
                    if len(d["persons_teachers"]) else pd.DataFrame())
    officer_att = d["officer_att"]
    teacher_att = d["teacher_att"]
    alias = {}
    if len(persons) and "lincs_agent" in persons.columns:
        alias.update({p: a for p, a in persons.lincs_agent.items() if a})
    if len(teachers_reg):
        alias.update({p: a for p, a in teachers_reg.lincs_agent.items() if a})

    def canon_person(eid):
        return alias.get(eid, eid)

    rev_alias = defaultdict(list)
    for p, a in alias.items():
        rev_alias[a].append(p)
    officer_label = (officer_att.dropna(subset=["lincs_agent"])
                     .groupby("lincs_agent").lincs_label
                     .agg(lambda s: s.mode().iat[0]).to_dict()
                     if len(officer_att) else {})

    def ent_url(etype, eid):
        if etype == "agency":
            return site.url("agencies", chain_slug(canon_chain(eid)))
        if etype == "reserve":
            return site.url("reserves", eid)
        if etype == "band":
            return site.url("bands", band_slug(eid))
        if etype == "agent":
            return site.url("persons", person_slug(canon_person(eid)))
        return None

    def ent_name(etype, eid):
        if etype == "agency":
            return chain_name.get(eid, title_case(eid[3:].replace("-", " ")))
        if etype == "reserve":
            r = reserves.loc[eid] if eid in reserves.index else None
            if r is None:
                return eid
            no = "" if is_null(r.reserve_no) else f" No. {r.reserve_no}"
            if is_null(r["name"]):   # unnamed row in the Schedule: band + number
                who = r.band_norm if not is_null(r.band_norm) else title_case(r.division_norm or "")
                return f"{who} reserve{no}".strip()
            return f"{r['name']}{no}"
        if etype == "band":
            return band_name.get(eid, eid)
        if etype == "agent":
            cid = canon_person(eid)
            if cid in lincs_name.index:
                return lincs_name[cid]
            if cid in officer_label:
                return officer_label[cid]
            if cid in persons.index:
                return title_case(persons.loc[cid, "name"])
            if len(teachers_reg) and cid in teachers_reg.index:
                return teachers_reg.loc[cid, "name"]
            return eid
        return eid

    # ---- segment bodies + text, per volume
    bodies, fms = {}, {}
    for tag in sorted(segs.tag.unique()):
        bodies[tag], fms[tag] = read_body(tag)
    seg_by_key = {(s.tag, s.segment_id): s for s in segs.itertuples()}
    seg_url = {k: site.url("reports", str(s.report_year), s.segment_id)
               for k, s in seg_by_key.items()}
    report_url = {y: site.url("reports", str(y)) for y in segs.report_year.unique()}
    seg_chain = {(m.tag, m.segment_id): m.chain_id for m in d["members"].itertuples()}
    seg_school, seg_method = {}, {}
    if len(d["attribution"]):   # build/attribute_segments.py: dateline / signature / school
        for a in d["attribution"].itertuples():
            if not isinstance(a.entity_id, str) or a.confidence == "low":
                continue
            if a.entity_type == "agency" and (a.tag, a.segment_id) not in seg_chain:
                seg_chain[(a.tag, a.segment_id)] = canon_chain(a.entity_id)
                seg_method[(a.tag, a.segment_id)] = a.method
            elif a.entity_type == "school":
                seg_school[(a.tag, a.segment_id)] = a.entity_id
                seg_method[(a.tag, a.segment_id)] = a.method
    chain_segments = defaultdict(list)
    school_segments = defaultdict(list)
    for (tag, sid), ch in seg_chain.items():
        if (tag, sid) in seg_by_key:
            chain_segments[ch].append(seg_by_key[(tag, sid)])
    for (tag, sid), sc in seg_school.items():
        if (tag, sid) in seg_by_key:
            school_segments[sc].append(seg_by_key[(tag, sid)])

    def seg_text(s):
        return bodies[s.tag][s.char_start:s.char_end]

    # ---- passages per entity (snippet around mention), grouped by year
    men = men.copy()
    men["primary"] = men.confidence.isin(PRIMARY)
    men["report_year"] = men.tag.str.extract(r"(\d{4})").astype(int)
    passages = defaultdict(lambda: defaultdict(list))  # (etype,eid) -> year -> [dict]
    seg_mentions = defaultdict(list)
    for m in men.itertuples():
        seg_mentions[(m.tag, m.segment_id)].append(m)
    for key, ms in seg_mentions.items():
        s = seg_by_key.get(key)
        if s is None:
            continue
        text = seg_text(s)
        for m in ms:
            a, b = m.char_start, m.char_end
            pre = ws(PAGE_RE.sub("", text[max(0, a - 160):a]))
            post = ws(PAGE_RE.sub("", text[b:b + 160]))
            if a - 160 > 0:
                pre = "…" + pre
            if b + 160 < len(text):
                post = post + "…"
            page = s.page_start + text[:a].count("<!-- page")  # printed page offset
            passages[(m.entity_type, m.entity_id)][m.report_year].append(dict(
                pre=pre, surf=text[a:b], post=post, seg=s, conf=m.confidence,
                primary=m.primary, page=page, context=m.context_agency))

    def render_passages(etype, eid, max_per_year):
        years = passages.get((etype, eid), {})
        if not years:
            return "<p><em>No mentions linked in the annual reports.</em></p>"
        out, low_out = [], []
        n_primary = sum(1 for y in years.values() for p in y if p["primary"])
        n_low = sum(1 for y in years.values() for p in y if not p["primary"])
        out.append(f"<p class=cite>{n_primary} mention{'s' if n_primary != 1 else ''} "
                   f"(high/medium confidence)"
                   + (f"; {n_low} unverified (low confidence) in the fold below" if n_low else "")
                   + ".</p>")
        for year in sorted(years):
            ps = years[year]
            prim = [p for p in ps if p["primary"]]
            low = [p for p in ps if not p["primary"]]
            if prim:
                out.append(f"<h3><a href='{report_url[year]}'>{year} report</a>"
                           f" <span class=badge>{len(prim)}</span></h3><ul>")
                for p in prim[:max_per_year]:
                    out.append(passage_li(p))
                if len(prim) > max_per_year:
                    more = {}
                    for p in prim[max_per_year:]:
                        more.setdefault(p["seg"].segment_id, p["seg"])
                    links = ", ".join(
                        f"<a href='{seg_url[(s.tag, s.segment_id)]}'>{esc(s.heading)}</a>"
                        for s in more.values())
                    out.append(f"<li class=cite>… {len(prim) - max_per_year} more in: {links}</li>")
                out.append("</ul>")
            for p in low[:max_per_year]:
                low_out.append(f"<li><b>{year}</b> " + passage_li(p)[4:])
        html_ = "\n".join(out)
        if low_out:
            html_ += (f"<details><summary>Unverified mentions ({n_low}) — low confidence, "
                      f"not yet reviewed</summary><ul>{''.join(low_out)}</ul></details>")
        return html_

    def passage_li(p):
        s = p["seg"]
        u = seg_url[(s.tag, s.segment_id)]
        kind = KIND_LABEL.get(s.kind, s.kind)
        ctx = f" · {esc(chain_name.get(p['context'], ''))}" if p["context"] else ""
        return (f"<li><span class=q>{esc(p['pre'])}<b>{esc(p['surf'])}</b>{esc(p['post'])}</span>"
                f" <span class=cite>— <a href='{u}'>{esc(s.heading)}</a>, p.&nbsp;{p['page']}"
                f" ({kind}{ctx}) <span class=badge>{p['conf']}</span></span></li>")

    def meta_box(rows):
        dl = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows if v)
        return f"<div class=meta><dl>{dl}</dl></div>"

    index_json = {}

    # ======================================================== agency pages
    print("agencies…")
    reserves_by_chain = defaultdict(list)
    for rid, r in reserves.iterrows():
        if r.division_type == "agency" and not is_null(r.division_norm):
            reserves_by_chain[canon_chain("AG-" + slug(r.division_norm))].append(rid)
    ev = d["events"]

    # ---- hub inputs: bands / schools / staff per chain with years
    sys.path.insert(0, str(ROOT / "build"))
    from build_agency_chains import canonicalize as _canon_name
    canon2chain = dict(zip(d["members"].canonical, d["members"].chain_id))
    canon2chain.update({r.canonical: canon_chain(cid_) for cid_, r in chains.iterrows()})

    def chain_for_string(name):
        if is_null(name):
            return None
        for cand in (str(name), str(name) + " AGENCY"):
            c_, _ = _canon_name(cand)
            if c_ and c_ in canon2chain:
                return canon_chain(canon2chain[c_])
        return None

    def yr_ranges(years):
        ys = sorted(set(int(y) for y in years))
        out, a_, b_ = [], ys[0], ys[0]
        for y in ys[1:]:
            if y == b_ + 1:
                b_ = y
                continue
            out.append(f"{a_}–{b_}" if a_ != b_ else str(a_))
            a_ = b_ = y
        out.append(f"{a_}–{b_}" if a_ != b_ else str(a_))
        return ", ".join(out)

    bg_ = d["bands_grounded"]
    canon_census_ = (dict(zip(d["band_canonical"].band_id, d["band_canonical"].canonical_band_id))
                     if len(d["band_canonical"]) else {})
    curated_of_ = ({r.band_id: r.curated_band_id for r in bg_.itertuples()
                    if isinstance(r.curated_band_id, str) and r.curated_band_id} if len(bg_) else {})
    census_name_ = dict(zip(bg_.band_id, bg_.name)) if len(bg_) else {}

    def band_target(bid_):
        c_ = canon_census_.get(bid_, bid_)
        return curated_of_.get(c_) or curated_of_.get(bid_) or c_

    def band_label(bid_):
        return band_name.get(bid_) or census_name_.get(bid_, bid_)

    def band_url(bid_):
        return ent_url("band", bid_) if bid_.startswith("BAND-") else site.url("bands", slug(bid_))

    bands_by_chain = defaultdict(lambda: defaultdict(set))
    if len(d["band_att"]):
        for r in d["band_att"].dropna(subset=["agency"]).itertuples():
            c_ = chain_for_string(r.agency)
            if c_:
                bands_by_chain[c_][band_target(r.band_id)].add(int(r.year))
    schools_by_chain = defaultdict(lambda: defaultdict(set))
    if len(d["school_att"]):
        for r in d["school_att"].dropna(subset=["agency"]).itertuples():
            c_ = chain_for_string(r.agency)
            if c_:
                schools_by_chain[c_][r.school_id].add(int(r.year))
    school_name_of = dict(zip(d["schools"].school_id, d["schools"].name)) if len(d["schools"]) else {}
    postings_by_chain = defaultdict(dict)
    lincs2chain_ = {u: canon_chain(cid_) for cid_, u in chains.lincs_uri.items() if isinstance(u, str) and u}
    for a_ in lincs.itertuples():
        c_ = lincs2chain_.get(a_.group)
        if not c_:
            continue
        y0 = pd.to_datetime(a_.begin, errors="coerce")
        y1 = pd.to_datetime(a_.end, errors="coerce")
        if pd.isna(y0):
            continue
        ys = range(y0.year, (y1.year if not pd.isna(y1) else y0.year) + 1)
        ent = postings_by_chain[c_].setdefault(a_.agent, (set(), a_.place_label or ""))
        ent[0].update(ys)

    for cid, c in chains.iterrows():
        if cid in chain_alias:
            continue   # folded into its canonical chain
        name = chain_name[cid]
        variants = d["members"][d["members"].chain_id == cid]
        var_rows = (variants.groupby("name").report_year.agg(["min", "max", "count"])
                    .sort_values("min"))
        signers = defaultdict(set)
        for (y, aid), _ in men[(men.entity_type == "agent") & (men.context_agency == cid)
                               ].groupby(["report_year", "entity_id"]):
            signers[aid].add(y)
        events = []
        if len(ev):
            for e in ev[(ev.from_chain == cid) | (ev.to_chain == cid)].itertuples():
                other = e.to_chain if e.from_chain == cid else e.from_chain
                verb = {"MERGED_INTO": "merged into" if e.from_chain == cid else "absorbed",
                        "SPLIT_INTO": "split into" if e.from_chain == cid else "split from"
                        }.get(e.event_type, e.event_type)
                events.append(f"<li>c.&nbsp;{e.year_approx}: {verb} "
                              f"<a href='{ent_url('agency', other)}'>{esc(chain_name.get(other, other))}</a>"
                              f" <span class=badge>{e.confidence}</span>"
                              f"<br><span class=cite>{esc(e.evidence)}</span></li>")
        letters = sorted(chain_segments.get(cid, []), key=lambda s: (s.report_year, s.page_start))
        rows = [("Type", esc(c.unit_type.title())),
                ("Attested", f"{c.first_year}–{c.last_year} ({c.n_years} report years, "
                             f"{c.n_attestations} headings, {c.n_variants} name variants)"),
                ("Canada50 URI", f"<code>{esc(c.uri)}</code>"),
                ("LINCS", f"<a href='{esc(c.lincs_uri)}'>{esc(c.lincs_uri)}</a>"
                 if not is_null(c.lincs_uri) else "")]
        body = [f"<h1>{esc(name)}</h1>", meta_box(rows)]
        if len(var_rows) > 1:
            body.append("<h2>Names as printed</h2><table><tr><th>Heading</th><th>Years</th><th>n</th></tr>")
            for n, r in var_rows.iterrows():
                body.append(f"<tr><td>{esc(n)}</td><td>{r['min']}–{r['max']}</td><td>{r['count']}</td></tr>")
            body.append("</table>")
        if events:
            body.append("<h2>Chain events</h2><ul>" + "".join(events) + "</ul>")
        if letters:
            body.append("<h2>Reports from this agency</h2>"
                        "<p class=cite>Letters headed with the unit's name, plus letters attributed "
                        "by dateline, signature or the signatory's other letters "
                        "(build/attribute_segments.py; method shown when not the heading).</p>"
                        "<ul class=compact>")
            for s in letters:
                meth = seg_method.get((s.tag, s.segment_id))
                body.append(f"<li><a href='{seg_url[(s.tag, s.segment_id)]}'>{s.report_year}</a>"
                            f" — {esc(s.heading)} (pp. {s.page_start}–{s.page_end})"
                            + (f" <span class=badge>{esc(meth)}</span>" if meth else "") + "</li>")
            body.append("</ul>")
        if signers:
            body.append("<h2>Officers signing for this agency</h2><ul>")
            for aid, ys in sorted(signers.items(), key=lambda kv: min(kv[1])):
                body.append(f"<li><a href='{ent_url('agent', aid)}'>{esc(ent_name('agent', aid))}</a>"
                            f" ({min(ys)}–{max(ys)})</li>")
            body.append("</ul>")
        # ---- the agency as a hub: what the tables list under it, by year
        bl = bands_by_chain.get(cid)
        if bl:
            body.append("<h2>Bands listed under this agency (census tables)</h2>"
                        "<p class=cite>From the annual census tables' agency column "
                        "(registries/annotations/band_attestations.parquet); years as printed.</p>"
                        "<table><tr><th>Band</th><th>Years</th></tr>")
            for bid_, ys in sorted(bl.items(), key=lambda kv: (min(kv[1]), band_label(kv[0]))):
                body.append(f"<tr><td><a href='{band_url(bid_)}'>{esc(band_label(bid_))}</a></td>"
                            f"<td>{yr_ranges(ys)}</td></tr>")
            body.append("</table>")
        sl = schools_by_chain.get(cid)
        if sl:
            body.append("<h2>Schools in this agency (School Statements)</h2>"
                        "<table><tr><th>School</th><th>Years</th></tr>")
            for sid_, ys in sorted(sl.items(), key=lambda kv: (min(kv[1]), school_name_of.get(kv[0], kv[0]))):
                body.append(f"<tr><td><a href='{site.url('schools', slug(sid_))}'>"
                            f"{esc(school_name_of.get(sid_, sid_))}</a></td><td>{yr_ranges(ys)}</td></tr>")
            body.append("</table>")
        pl = postings_by_chain.get(cid)
        if pl:
            body.append("<h2>Staff posted here (LINCS Indian Affairs Agents)</h2>"
                        "<table><tr><th>Person</th><th>Years</th><th>Place</th></tr>")
            for aid_, (ys, place) in sorted(pl.items(), key=lambda kv: (min(kv[1][0]), ent_name('agent', kv[0]))):
                body.append(f"<tr><td><a href='{ent_url('agent', aid_)}'>{esc(ent_name('agent', aid_))}</a></td>"
                            f"<td>{yr_ranges(ys)}</td><td>{esc(place)}</td></tr>")
            body.append("</table>")
        rids = reserves_by_chain.get(cid, [])
        if rids:
            body.append(f"<h2>Reserves administered (Schedule of 1902)</h2><ul class=compact>")
            for rid in sorted(rids, key=lambda r: (str(reserves.loc[r, 'band_norm']), str(reserves.loc[r, 'reserve_no']))):
                r = reserves.loc[rid]
                body.append(f"<li><a href='{ent_url('reserve', rid)}'>{esc(ent_name('reserve', rid))}</a>"
                            f" <span class=cite>{esc(r.band_norm)}</span></li>")
            body.append("</ul>")
        body.append(series_for(cid))
        body.append("<h2>Mentioned in the annual reports</h2>")
        body.append(render_passages("agency", cid, args.max_passages))
        site.write(f"agencies/{chain_slug(cid)}", "\n".join(body), name,
                   [("Canada50", site.url()), ("Agencies", site.url("agencies")), (name, None)],
                   f"{name}: Department of Indian Affairs {c.unit_type.lower()}, attested {c.first_year}–{c.last_year}")
        index_json[c.uri] = dict(type="agency", id=cid, name=name, url=ent_url("agency", cid))
        for frag in [f for f, t in chain_alias.items() if t == cid]:
            furi = chains.loc[frag, "uri"]
            furi = furi.iloc[0] if isinstance(furi, pd.Series) else furi
            index_json[furi] = dict(type="agency", id=frag, name=name, url=ent_url("agency", cid))

    # ======================================================== reserve pages
    print("reserves…")
    area_units = d["area_units"]

    def area_str(r):
        """Schedule area with its real unit (curation/schedule_area_units.csv)."""
        if is_null(r.acres_text):
            return ""
        unit = area_units.get(r.division_norm, "acres") if not is_null(r.division_norm) else "acres"
        if unit == "square_miles":
            eq = f" (≈ {r.acres * 640:,.0f} acres)" if not is_null(r.acres) else ""
            return (f"{esc(r.acres_text)} sq. miles{eq} <span class=cite>— printed under an "
                    f"'Acres' header; see curation/schedule_area_units.csv</span>")
        return f"{esc(r.acres_text)} acres"
    for rid, r in reserves.iterrows():
        name = ent_name("reserve", rid)
        bid = band_by_norm.get(r.band_norm)
        div_link = ""
        if not is_null(r.division_norm):
            if r.division_type == "agency":
                cid = "AG-" + slug(r.division_norm)
                div_link = (f"<a href='{ent_url('agency', cid)}'>{esc(chain_name.get(cid, title_case(r.division_norm)))}</a>"
                            if cid in chains.index else esc(title_case(r.division_norm)))
            else:
                div_link = f"{esc(title_case(r.division_norm))} <span class=badge>{esc(r.division_type)}</span>"
        rows = [("Band", f"<a href='{ent_url('band', bid)}'>{esc(band_name[bid])}</a>" if bid else esc(r.band_norm)),
                ("Division", div_link),
                ("Province", esc(title_case(r.province)) if not is_null(r.province) else ""),
                ("Location", esc(r.location)),
                ("Area", area_str(r)),
                ("Remarks", esc(r.remarks)),
                ("Source", f"Schedule of Indian Reserves, 1902, p.&nbsp;{r.page} "
                           f"<span class=badge>{esc(r.confidence)}</span>"),
                ("Canada50 URI", f"<code>{esc(r.uri)}</code>")]
        body = [f"<h1>{esc(name)}</h1>", meta_box(rows)]
        if len(d["attestations"]):
            at = d["attestations"]
            series = at[(at.reserve_id == rid) & (at.match_tier != "bc_index")].sort_values("edition")
            if len(series) > 1:   # more than the 1902 anchor row
                body.append("<h2>Schedule of Indian Reserves attestations</h2>"
                            "<p class=cite>Editions 1897–1901 extracted by an open model "
                            "(Qwen3.8) and linked to this reserve; match tier shown — "
                            "verify before citing. 1902 is the registry anchor.</p>"
                            "<table><tr><th>Edition</th><th>No.</th><th>Name as printed</th>"
                            "<th>Area</th><th>p. (pdf)</th><th>Link</th></tr>")
                for a in series.itertuples():
                    body.append(f"<tr><td>{a.edition}</td><td>{esc(a.reserve_no)}</td>"
                                f"<td>{esc(a.name)}</td><td>{esc(a.acres_text)}</td>"
                                f"<td>{'' if is_null(a.page) else int(a.page)}</td>"
                                f"<td><span class=badge>{esc(a.match_tier)}</span></td></tr>")
                body.append("</table>")
        body += ["<h2>Mentioned in the annual reports</h2>",
                 render_passages("reserve", rid, args.max_passages)]
        site.write(f"reserves/{rid}", "\n".join(body), name,
                   [("Canada50", site.url()), ("Reserves", site.url("reserves")), (name, None)],
                   f"{name} — Indian reserve, {r.band_norm}, {r.division_norm or ''}, {r.province or ''}")
        index_json[r.uri] = dict(type="reserve", id=rid, name=name, url=ent_url("reserve", rid))


    # ======================================================== school pages
    schools = d["schools"]
    if len(schools):
        print("schools…")
        att = d["school_att"]
        ev = d["school_events"]
        att_by = {k: g for k, g in att.groupby("school_id")} if len(att) else {}
        ev_by = {k: g for k, g in ev.groupby("school_id")} if len(ev) else {}
        t_by_school = ({k: g for k, g in d["teacher_att"].groupby("school_id")}
                       if len(d["teacher_att"]) else {})
        for sc in schools.itertuples():
            name = sc.name
            a = att_by.get(sc.school_id)
            teachers = []
            if a is not None:
                teachers = [(int(r.year), str(r.teacher)) for r in a.itertuples()
                            if r.teacher and str(r.teacher) not in ("None", "nan", "...")]
            rows = [("Type", esc(sc.school_type or "not stated")
                     + (f" <span class=cite>(also {esc(sc.types_seen.replace('|', ', '))})</span>"
                        if sc.types_seen and "|" in sc.types_seen else "")),
                    ("Province", esc(sc.province_pool or "—")),
                    ("Agency", esc(title_case(sc.agencies.replace("|", ", "))) if sc.agencies else ""),
                    ("Reserve", esc(title_case(sc.reserves.replace("|", ", "))) if sc.reserves else ""),
                    ("Denomination", esc(sc.denominations.replace("|", ", ")) if sc.denominations else ""),
                    ("Attested", f"{sc.first_year}–{sc.last_year} "
                                 f"<span class=cite>({sc.n_years} years printed)</span>"),
                    ("Name variants", esc(sc.name_variants.replace("|", " · "))
                     if "|" in str(sc.name_variants) else "")]
            body = [f"<h1>{esc(name)}</h1>",
                    "<p class=cite>School as recorded in the Department of Indian Affairs "
                    "School Statements. Identity, dates and figures are the department's own; "
                    "this page makes no claim beyond what the tables printed.</p>",
                    meta_box(rows)]
            e = ev_by.get(sc.school_id)
            if e is not None and len(e):
                body.append("<h2>Events</h2><table><tr><th>Year</th><th>Event</th><th>Detail</th></tr>")
                for r in e.sort_values("year").itertuples():
                    body.append(f"<tr><td>{int(r.year)}</td><td>{esc(r.event)}</td>"
                                f"<td>{esc(r.detail)}</td></tr>")
                body.append("</table>")
            body.append(series_for(sc.school_id))
            ta = t_by_school.get(sc.school_id)
            if ta is not None and len(ta):
                rows_ = ta.drop_duplicates(["year", "person_id"]).sort_values("year")
                body.append("<details><summary>Teachers named in the statements "
                            f"<span class=badge>{len(rows_)}</span></summary>"
                            "<table><tr><th>Year</th><th>Teacher</th></tr>")
                for r in rows_.itertuples():
                    url = site.url("persons", person_slug(canon_person(r.person_id)))
                    body.append(f"<tr><td>{int(r.year)}</td>"
                                f"<td><a href='{url}'>{esc(r.teacher_as_printed)}</a></td></tr>")
                body.append("</table></details>")
            elif teachers:
                body.append("<details><summary>Teachers named in the statements "
                            f"<span class=badge>{len(teachers)}</span></summary>"
                            "<table><tr><th>Year</th><th>Teacher</th></tr>")
                for y, t in sorted(set(teachers)):
                    body.append(f"<tr><td>{y}</td><td>{esc(t)}</td></tr>")
                body.append("</table></details>")
            reps = sorted(school_segments.get(sc.school_id, []), key=lambda s: (s.report_year, s.page_start))
            if reps:
                body.append("<h2>Reports on this school</h2><p class=cite>Principals' and "
                            "inspectors' reports attributed by heading (build/attribute_segments.py).</p>"
                            "<ul class=compact>")
                for s in reps:
                    body.append(f"<li><a href='{seg_url[(s.tag, s.segment_id)]}'>{s.report_year}</a>"
                                f" — {esc(s.heading)} (pp. {s.page_start}–{s.page_end})</li>")
                body.append("</ul>")
            site.write(f"schools/{slug(sc.school_id)}", "\n".join(body), name,
                       [("Canada50", site.url()), ("Schools", site.url("schools")), (name, None)],
                       f"{name} — {sc.school_type or 'school'}, {sc.province_pool}, "
                       f"{sc.first_year}–{sc.last_year}")
            index_json[f"school:{sc.school_id}"] = dict(
                type="school", id=sc.school_id, name=name,
                url=site.url("schools", slug(sc.school_id)))

        body = ["<h1>Schools</h1>",
                f"<p>{len(schools):,} schools recorded in the School Statements "
                f"{int(schools.first_year.min())}–{int(schools.last_year.max())}: "
                + ", ".join(f"{n:,} {t or 'type not stated'}"
                            for t, n in schools.school_type.value_counts().items())
                + ".</p>",
                "<table><tr><th>School</th><th>Type</th><th>Province</th>"
                "<th>Agency</th><th>Years</th></tr>"]
        for sc in schools.sort_values(["province_pool", "name"]).itertuples():
            body.append(
                f"<tr><td><a href='{site.url('schools', slug(sc.school_id))}'>{esc(sc.name)}</a></td>"
                f"<td>{esc(sc.school_type)}</td><td>{esc(sc.province_pool)}</td>"
                f"<td>{esc(title_case(sc.agencies.split('|')[0]) if sc.agencies else '')}</td>"
                f"<td>{sc.first_year}–{sc.last_year}</td></tr>")
        body.append("</table>")
        site.write("schools", "\n".join(body), "Schools",
                   [("Canada50", site.url()), ("Schools", None)])

    # ======================================================== band pages
    print("bands…")
    reserves_by_band = defaultdict(list)
    for rid, r in reserves.iterrows():
        b = band_by_norm.get(r.band_norm)
        if b:
            reserves_by_band[b].append(rid)
    for bid, b in bands.iterrows():
        name = b["name"]
        rows = [("Provinces", esc(", ".join(title_case(p) for p in listify(b.provinces)))),
                ("Divisions (1902)", ", ".join(
                    f"<a href='{ent_url('agency', 'AG-' + slug(dv))}'>{esc(title_case(dv))}</a>"
                    if "AG-" + slug(dv) in chains.index else esc(title_case(dv))
                    for dv in listify(b.divisions))),
                ("Reserves (1902)", f"{b.n_reserves} · {b.total_acres:,.0f} acres"
                 if not is_null(b.total_acres) else str(b.n_reserves)),
                ("Succeeded by", f"<a href='http://www.wikidata.org/entity/{esc(b.succeeded_by_qid)}'>"
                                 f"{esc(b.wd_label)}</a> ({esc(b.succeeded_by_qid)})"
                 if not is_null(b.succeeded_by_qid) else ""),
                ("People", f"<a href='http://www.wikidata.org/entity/{esc(b.people_qid)}'>"
                           f"{esc(b.wd_label if is_null(b.succeeded_by_qid) else b.people_qid)}</a>"
                 if not is_null(b.people_qid) else ""),
                ("Grounding", f"{esc(b.grounding_status)} <span class=badge>{esc(b.confidence)}</span>"
                              + (f"<br><span class=cite>{esc(b.notes)}</span>" if not is_null(b.notes) else "")),
                ("Canada50 URI", f"<code>{esc(b.uri)}</code>")]
        body = [f"<h1>{esc(name)}</h1>",
                "<p class=cite>Historical band as recorded by the Department of Indian Affairs; "
                "a distinct entity from the modern First Nation it may have become "
                "(<em>succeeded by</em>, never <em>same as</em>).</p>",
                meta_box(rows)]
        rids = reserves_by_band.get(bid, [])
        if rids:
            body.append("<h2>Reserves occupied (Schedule of 1902)</h2><ul class=compact>")
            for rid in sorted(rids, key=lambda r: str(reserves.loc[r, "reserve_no"]).zfill(4)):
                r = reserves.loc[rid]
                acres = f" — {r.acres_text} ac." if not is_null(r.acres_text) else ""
                body.append(f"<li><a href='{ent_url('reserve', rid)}'>{esc(ent_name('reserve', rid))}</a>{esc(acres)}</li>")
            body.append("</ul>")
        body.append(series_for(census_band_of.get(bid, "")))
        body += ["<h2>Mentioned in the annual reports</h2>",
                 render_passages("band", bid, args.max_passages)]
        site.write(f"bands/{band_slug(bid)}", "\n".join(body), name,
                   [("Canada50", site.url()), ("Bands", site.url("bands")), (name, None)],
                   f"{name} — band, {', '.join(listify(b.provinces))}")
        index_json[b.uri] = dict(type="band", id=bid, name=name, url=ent_url("band", bid))

    # ======================================================== census band pages
    # Bands the annual census tables list that no 1902-Schedule band was bridged
    # to (merge_band_registries). Identities aliased by alias_census_bands fold
    # into their canonical id; bridged ones fold into the curated page above.
    bg = d["bands_grounded"]
    if len(bg):
        print("census bands…")
        canon_census = (dict(zip(d["band_canonical"].band_id, d["band_canonical"].canonical_band_id))
                        if len(d["band_canonical"]) else {})
        curated_of = {r.band_id: r.curated_band_id for r in bg.itertuples()
                      if isinstance(r.curated_band_id, str) and r.curated_band_id}
        modern = {r.band_id: r for r in d["band_modern"].itertuples()} if len(d["band_modern"]) else {}
        batt = d["band_att"]
        att_by = {k: g for k, g in batt.groupby("band_id")} if len(batt) else {}
        members_of = defaultdict(list)
        for b_, c_ in canon_census.items():
            if b_ != c_:
                members_of[c_].append(b_)

        def agency_timeline(ids):
            """(agency string → years) from the census rows of these identities."""
            yrs = defaultdict(set)
            for i in ids:
                g = att_by.get(i)
                if g is None:
                    continue
                for r in g.itertuples():
                    if isinstance(r.agency, str) and r.agency.strip():
                        yrs[r.agency.strip()].add(int(r.year))
            return sorted(yrs.items(), key=lambda kv: min(kv[1]))

        def ranges(years):
            ys = sorted(set(years))
            out, a, b = [], ys[0], ys[0]
            for y in ys[1:]:
                if y == b + 1:
                    b = y
                    continue
                out.append(f"{a}–{b}" if a != b else str(a))
                a = b = y
            out.append(f"{a}–{b}" if a != b else str(a))
            return ", ".join(out)

        n_census_pages = 0
        rows_index = []
        for r in bg.itertuples():
            bid = r.band_id
            if canon_census.get(bid, bid) != bid:
                continue
            if curated_of.get(bid):
                continue
            ids = [bid] + members_of.get(bid, [])
            m = modern.get(bid)
            nv = r.name_variants
            nv = nv.split("|") if isinstance(nv, str) else listify(nv)
            variants = sorted({v for v in nv if v} - {r.name})
            tl = agency_timeline(ids)
            span = f"{r.first_year}–{r.last_year}"
            rows = [("Province", esc(r.province_pool or "not stated")),
                    ("Attested", f"{span} ({r.n_years} report years, {r.n_rows} rows)"),
                    ("Population", f"{int(r.population_first):,} ({r.first_year}) → "
                                   f"{int(r.population_last):,} ({r.last_year})"
                     if not is_null(r.population_first) and not is_null(r.population_last) else ""),
                    ("Succeeded by", f"<a href='http://www.wikidata.org/entity/{esc(m.qid)}'>{esc(m.modern_name)}</a>"
                                     f" ({esc(m.qid)}; band no. {int(m.band_no) if not is_null(m.band_no) else '?'}; "
                                     f"link tier {esc(m.tier)})" if m is not None and not is_null(m.qid) else ""),
                    ("Names as printed", esc(" · ".join(variants)) if variants else ""),
                    ("Merged identities", ", ".join(f"<code>{esc(x)}</code>" for x in members_of.get(bid, []))),
                    ("Canada50 URI", f"<code>{esc(r.uri)}</code>" if isinstance(r.uri, str) else "")]
            body = [f"<h1>{esc(r.name)}</h1>",
                    "<p class=cite>Band as listed in the Department of Indian Affairs annual census "
                    "tables; a distinct entity from the modern First Nation it may have become "
                    "(<em>succeeded by</em>, never <em>same as</em>). Identity follows the printed "
                    "name within a province (build/mint_bands_from_census.py).</p>",
                    meta_box(rows)]
            if tl:
                body.append("<h2>Agency as printed, by year</h2><table><tr><th>Agency</th><th>Years</th></tr>")
                for ag_, ys in tl:
                    cid = None
                    body.append(f"<tr><td>{esc(ag_)}</td><td>{ranges(ys)}</td></tr>")
                body.append("</table>")
            for i in ids:
                blk = series_for(i)
                if blk:
                    if len(ids) > 1:
                        body.append(f"<p class=cite>Series keyed by <code>{esc(i)}</code></p>")
                    body.append(blk)
            site.write(f"bands/{slug(bid)}", "\n".join(body), r.name,
                       [("Canada50", site.url()), ("Bands", site.url("bands")), (r.name, None)],
                       f"{r.name} — band in the DIA census tables, {r.province_pool or ''}, {span}")
            uri = r.uri if isinstance(r.uri, str) and r.uri else f"band:{bid}"
            index_json[uri] = dict(type="band", id=bid, name=r.name, url=site.url("bands", slug(bid)))
            rows_index.append((r.province_pool or "", r.name, bid, span, r.n_years,
                               m.modern_name if m is not None and not is_null(m.qid) else ""))
            n_census_pages += 1
        print(f"  {n_census_pages} census band pages")
        body = ["<h1>Bands in the annual census tables</h1>",
                f"<p>{n_census_pages:,} band identities listed in the DIA census tables 1881–1929 that "
                "are not bridged to a band of the 1902 Schedule (those appear under "
                f"<a href='{site.url('bands')}'>Bands</a>). Grouped by province pool.</p>"]
        for prov, g in pd.DataFrame(rows_index, columns=["prov", "name", "bid", "span", "n", "modern"]
                                    ).sort_values(["prov", "name"]).groupby("prov"):
            body.append(f"<h2>{esc(prov or 'province not stated')} <span class=badge>{len(g)}</span></h2>"
                        "<table><tr><th>Band</th><th>Attested</th><th>Years</th><th>Succeeded by</th></tr>")
            for x in g.itertuples():
                body.append(f"<tr><td><a href='{site.url('bands', slug(x.bid))}'>{esc(x.name)}</a></td>"
                            f"<td>{x.span}</td><td>{x.n}</td><td>{esc(x.modern)}</td></tr>")
            body.append("</table>")
        site.write("bands/census", "\n".join(body), "Census bands",
                   [("Canada50", site.url()), ("Bands", site.url("bands")), ("Census tables", None)])

    # ======================================================== person pages
    print("persons…")
    off_by = ({a: g for a, g in
               officer_att[officer_att.lincs_agent.fillna("") != ""]
               .groupby("lincs_agent")} if len(officer_att) else {})
    if len(teacher_att):
        teacher_att = teacher_att.assign(cid=teacher_att.person_id.map(canon_person))
    teach_by = ({c: g for c, g in teacher_att.groupby("cid")}
                if len(teacher_att) else {})
    school_name = (dict(zip(d["schools"].school_id, d["schools"].name))
                   if len(d["schools"]) else {})
    agent_ids = sorted({canon_person(x)
                        for x in men[men.entity_type == "agent"].entity_id}
                       | {canon_person(p) for p in persons.index}
                       | set(off_by) | set(teach_by))
    for aid in agent_ids:
        ids = [aid] + rev_alias.get(aid, [])
        name = ent_name("agent", aid)
        rows = []
        if not aid.startswith("PERSON-"):
            src = ("Indian Affairs Agents dataset" if "lincsproject" in aid
                   else "external authority")
            rows.append(("Identity", f"<a href='{esc(aid)}'>{esc(aid)}</a> ({src})"))
            acts = lincs[lincs.agent == aid].sort_values("begin")
            if len(acts):
                tbl = ["<table><tr><th>From</th><th>To</th><th>Place</th><th>Group</th></tr>"]
                for a in acts.itertuples():
                    tbl.append(f"<tr><td>{esc(str(a.begin)[:10])}</td><td>{esc(str(a.end)[:10]) if not is_null(a.end) else ''}</td>"
                               f"<td><a href='{esc(a.place)}'>{esc(a.place_label)}</a></td><td>{esc(a.group_label)}</td></tr>")
                tbl.append("</table>")
                rows.append(("Postings (LINCS)", "".join(tbl)))
        for pid in ids:
            if pid in persons.index:
                p = persons.loc[pid]
                tail = ("" if not aid.startswith("PERSON-")
                        else "; no LINCS Indian Affairs Agents match")
                rows += [("Report signatures",
                          f"{p.n_signatures} signature{'s' if p.n_signatures != 1 else ''}, "
                          f"{p.first_year}–{p.last_year}{tail}"),
                         ("Agencies", ", ".join(
                             f"<a href='{ent_url('agency', c)}'>{esc(chain_name.get(c, c))}</a>"
                             for c in listify(p.chains)))]
                if aid.startswith("PERSON-"):
                    rows.append(("Canada50 URI", f"<code>{esc(p.uri)}</code>"))
            if len(teachers_reg) and pid in teachers_reg.index:
                tr = teachers_reg.loc[pid]
                if aid.startswith("PERSON-"):
                    rows.append(("Canada50 URI", f"<code>{esc(tr.uri)}</code>"))
        signed = men[(men.entity_type == "agent") & men.entity_id.isin(ids)]
        surfaces = sorted(set(signed.surface))
        if surfaces:
            rows.insert(0, ("Names in reports", esc("; ".join(surfaces))))
        body = [f"<h1>{esc(name)}</h1>", meta_box(rows)]

        o = off_by.get(aid)
        if o is not None and len(o):
            body.append("<h2>Return A service record</h2>"
                        "<p class=cite>As printed in the annual List of Officers and "
                        "Employees; the name column may abbreviate differently year to year.</p>"
                        "<table><tr><th>Year</th><th>As printed</th><th>Designation</th>"
                        "<th>Salary</th><th>Appointed</th></tr>")
            for r in o.sort_values(["year", "page"]).itertuples():
                body.append(f"<tr><td>{int(r.year)}</td><td>{esc(r.name_as_printed)}</td>"
                            f"<td>{esc('' if is_null(r.designation) else r.designation)}</td>"
                            f"<td>{esc('' if is_null(r.salary) else str(r.salary))}</td>"
                            f"<td>{esc('' if is_null(r.appointed) else str(r.appointed))}</td></tr>")
            body.append("</table>")
        g = teach_by.get(aid)
        if g is not None and len(g):
            body.append("<h2>Schools</h2>"
                        "<p class=cite>From the School Statements' teacher column.</p>"
                        "<table><tr><th>Year</th><th>School</th><th>As printed</th></tr>")
            for r in g.drop_duplicates(["year", "school_id"]).sort_values(["year"]).itertuples():
                sn = school_name.get(r.school_id, r.school_id)
                body.append(f"<tr><td>{int(r.year)}</td>"
                            f"<td><a href='{site.url('schools', slug(r.school_id))}'>{esc(sn)}</a></td>"
                            f"<td>{esc(r.teacher_as_printed)}</td></tr>")
            body.append("</table>")
        if len(signed):
            body.append("<h2>Reports signed or mentioned</h2><ul>")
            for s_ in signed.drop_duplicates(["tag", "segment_id"]).sort_values(["report_year", "segment_id"]).itertuples():
                s = seg_by_key.get((s_.tag, s_.segment_id))
                if s is None:
                    continue
                ctx = f" — <a href='{ent_url('agency', s_.context_agency)}'>{esc(chain_name.get(s_.context_agency, ''))}</a>" \
                    if not is_null(s_.context_agency) and s_.context_agency in chains.index else ""
                body.append(f"<li><a href='{seg_url[(s.tag, s.segment_id)]}'>{s.report_year}: {esc(s.heading)}</a>{ctx}"
                            f" <span class=badge>{s_.confidence}</span></li>")
            body.append("</ul>")
        what = ("teacher, Indian day and residential schools"
                if g is not None and o is None and not len(signed)
                else "Department of Indian Affairs officer")
        site.write(f"persons/{person_slug(aid)}", "\n".join(body), name,
                   [("Canada50", site.url()), ("Persons", site.url("persons")), (name, None)],
                   f"{name} — {what}")
        if aid in persons.index:
            uri = persons.loc[aid, "uri"]
        elif len(teachers_reg) and aid in teachers_reg.index:
            uri = teachers_reg.loc[aid, "uri"]
        else:
            uri = aid
        index_json[uri] = dict(type="person", id=aid, name=name, url=ent_url("agent", aid))

    # ======================================================== report + segment pages
    print("reports + segments…")
    editions = {}
    xw = d["dia_sessional"].set_index("tag")
    seg_counts = men[men.primary].groupby(["tag", "segment_id"]).size()
    for tag, grp in segs.groupby("tag"):
        grp = grp.sort_values("ordinal")
        fm = fms[tag]
        year = int(grp.report_year.iat[0])
        doc_id = grp.doc_id.iat[0]
        x = xw.loc[tag] if tag in xw.index else None
        canadiana = ""
        if x is not None and not is_null(x.session_seq) and fm.get("sessional_paper_volume"):
            cid = f"oocihm.9_08052_{int(x.session_seq)}_{fm['sessional_paper_volume']}"
            if cid in d["sp_ids"]:
                canadiana = f"<a href='https://www.canadiana.ca/view/{cid}'>{cid}</a>"
            else:
                canadiana = f"<code>{cid}</code> (not in corpus id list)"
        title = fm.get("title", "Annual Report of the Department of Indian Affairs")
        rows = [("Report year", str(year)),
                ("Document id", f"<code>{esc(doc_id)}</code>"
                 + (f" — Sessional Paper No. {esc(x.paper_num)}, session {esc(x.session_year)}"
                    if x is not None and not is_null(x.paper_num) else " (provisional, awaiting catalog)")),
                ("Presented", esc(fm.get("presented_to_parliament", ""))),
                ("Canadiana volume", canadiana),
                ("Text", f"{esc(fm.get('text_layer', ''))}; {esc(fm.get('extraction_tool', ''))}; "
                         f"{fm.get('pdf_pages', '?')} pages, {int(fm.get('word_count', 0) or 0):,} words"),
                ("Source PDF", f"<code>{esc(fm.get('source_pdf', ''))}</code>")]
        toc = ["<h2>Contents</h2><table><tr><th>Pages</th><th>Segment</th><th>Kind</th><th>Agency</th><th>Mentions</th></tr>"]
        for s in grp.itertuples():
            ch = seg_chain.get((tag, s.segment_id))
            chl = f"<a href='{ent_url('agency', ch)}'>{esc(chain_name.get(ch, ''))}</a>" if ch else ""
            n = seg_counts.get((tag, s.segment_id), 0)
            toc.append(f"<tr><td>{s.page_start}–{s.page_end}</td>"
                       f"<td><a href='{seg_url[(tag, s.segment_id)]}'>{esc(s.heading)}</a></td>"
                       f"<td>{esc(KIND_LABEL.get(s.kind, s.kind))}</td><td>{chl}</td><td>{n or ''}</td></tr>")
        toc.append("</table>")
        body = [f"<h1>{esc(title)}, {year}</h1>", meta_box(rows)] + toc
        site.write(f"reports/{year}", "\n".join(body), f"DIA Annual Report {year}",
                   [("Canada50", site.url()), ("Reports", site.url("reports")), (str(year), None)],
                   f"{title}, {year} — {len(grp)} segments")
        editions[doc_id] = dict(tag=tag, url=report_url[year], segments={})

        # segment pages
        seglist = list(grp.itertuples())
        for i, s in enumerate(seglist):
            text = seg_text(s)
            ms = sorted((m for m in seg_mentions.get((tag, s.segment_id), [])),
                        key=lambda m: (m.char_start, -m.char_end))
            pieces, pos = [], 0
            for m in ms:
                if m.char_start < pos:
                    continue  # overlapping mention; keep the earlier one
                pieces.append(esc(text[pos:m.char_start]))
                surf = esc(text[m.char_start:m.char_end])
                u = ent_url(m.entity_type, m.entity_id)
                if m.primary and u:
                    pieces.append(f"<a class='m-{m.confidence}' href='{u}' title='{esc(ent_name(m.entity_type, m.entity_id))}'>{surf}</a>")
                else:
                    pieces.append(f"<span class='m-low' title='unverified: {esc(ent_name(m.entity_type, m.entity_id))}'>{surf}</span>")
                pos = m.char_end
            pieces.append(esc(text[pos:]))
            rendered = "".join(pieces)
            rendered = re.sub(r"&lt;!-- page (\d+) --&gt;\n?",
                              lambda mm: f"<span class=pg id='p{mm.group(1)}'>— page {mm.group(1)} —</span>",
                              rendered)
            ch = seg_chain.get((tag, s.segment_id))
            ents = defaultdict(list)
            for m in ms:
                ents[(m.entity_type, m.entity_id)].append(m)
            ent_list = []
            for (et, eid), mm in sorted(ents.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                prim = sum(1 for m in mm if m.primary)
                cls = "" if prim else " class=cite"
                ent_list.append(f"<li{cls}><a href='{ent_url(et, eid)}'>{esc(ent_name(et, eid))}</a>"
                                f" <span class=badge>{et}</span> ×{len(mm)}"
                                + ("" if prim else " (unverified)") + "</li>")
            prev_ = seglist[i - 1] if i else None
            next_ = seglist[i + 1] if i + 1 < len(seglist) else None
            nav = " · ".join(filter(None, [
                f"<a href='{seg_url[(tag, prev_.segment_id)]}'>← {esc(prev_.heading)}</a>" if prev_ else "",
                f"<a href='{report_url[year]}'>{year} report contents</a>",
                f"<a href='{seg_url[(tag, next_.segment_id)]}'>{esc(next_.heading)} →</a>" if next_ else ""]))
            rows = [("Report", f"<a href='{report_url[year]}'>DIA Annual Report {year}</a> "
                               f"(<code>{esc(doc_id)}</code>)"),
                    ("Pages", f"{s.page_start}–{s.page_end}"),
                    ("Kind", esc(KIND_LABEL.get(s.kind, s.kind))),
                    ("Agency", f"<a href='{ent_url('agency', ch)}'>{esc(chain_name.get(ch, ''))}</a>" if ch else ""),
                    ("Address", f"<code>{esc(doc_id)} / {esc(s.segment_id)}</code> · text version <code>{esc(s.text_version)}</code>")]
            body = [f"<h1>{esc(s.heading)}</h1>", f"<p class=cite>{nav}</p>", meta_box(rows)]
            if ent_list:
                body.append(f"<details open><summary>Entities linked in this segment ({len(ent_list)})</summary>"
                            f"<ul class=compact>{''.join(ent_list)}</ul></details>")
            body.append(f"<pre class=txt>{rendered}</pre>")
            body.append(f"<p class=cite>{nav}</p>")
            site.write(f"reports/{year}/{s.segment_id}", "\n".join(body),
                       f"{s.heading} — DIA {year}",
                       [("Canada50", site.url()), ("Reports", site.url("reports")),
                        (str(year), report_url[year]), (s.heading, None)],
                       ws(PAGE_RE.sub("", text[:400])))
            editions[doc_id]["segments"][s.segment_id] = seg_url[(tag, s.segment_id)]
        print(f"  {tag}: {len(grp)} segments")

    # ======================================================== indexes
    print("indexes…")
    n_men = {k: sum(len(v) for v in yrs.values()) for k, yrs in passages.items()}

    def count(et, eid):
        return n_men.get((et, eid), 0)

    # agencies by province (schedule province via reserves), then name
    chain_prov = {}
    for cid, rids in reserves_by_chain.items():
        provs = reserves.loc[rids].province.dropna()
        if len(provs):
            chain_prov[cid] = provs.mode().iat[0]
    body = ["<h1>Agencies and superintendencies</h1>",
            f"<p>{len(chains)} persistent agency chains attested in DIA annual-report headings, "
            "1880–1930. Region is taken from the 1902 Schedule of Reserves where the chain "
            "administered reserves.</p>",
            "<table><tr><th>Agency</th><th>Type</th><th>Years</th><th>Region</th><th>Reserves</th><th>Mentions</th></tr>"]
    for cid, c in chains.sort_values("canonical").iterrows():
        if cid in chain_alias:
            continue
        body.append(f"<tr><td><a href='{ent_url('agency', cid)}'>{esc(chain_name[cid])}</a></td>"
                    f"<td>{esc(c.unit_type.title())}</td><td>{c.first_year}–{c.last_year}</td>"
                    f"<td>{PROV_SHORT.get(chain_prov.get(cid, ''), esc(title_case(chain_prov.get(cid, ''))))}</td>"
                    f"<td>{len(reserves_by_chain.get(cid, []))}</td><td>{count('agency', cid)}</td></tr>")
    body.append("</table>")
    site.write("agencies", "\n".join(body), "Agencies",
               [("Canada50", site.url()), ("Agencies", None)])

    body = ["<h1>Indian reserves (Schedule of 1902)</h1>",
            f"<p>{len(reserves)} reserves from the Department's 1902 Schedule, grouped by province "
            "and division. Numbers are band-relative as printed.</p>"]
    for prov, g in reserves.fillna({"province": "(province not stated)"}).groupby("province"):
        body.append(f"<h2>{esc(title_case(prov))}</h2>")
        for div, gg in g.fillna({"division_norm": "(no division)"}).groupby("division_norm"):
            cid = "AG-" + slug(div)
            head = f"<a href='{ent_url('agency', cid)}'>{esc(title_case(div))}</a>" if cid in chains.index else esc(title_case(div))
            body.append(f"<h3>{head} <span class=badge>{len(gg)}</span></h3><ul class=compact>")
            for rid, r in gg.sort_values(["band_norm", "reserve_no"]).iterrows():
                n = count("reserve", rid)
                body.append(f"<li><a href='{ent_url('reserve', rid)}'>{esc(ent_name('reserve', rid))}</a>"
                            f" <span class=cite>{esc(r.band_norm)}</span>" + (f" <span class=badge>{n}</span>" if n else "") + "</li>")
            body.append("</ul>")
    site.write("reserves", "\n".join(body), "Reserves",
               [("Canada50", site.url()), ("Reserves", None)])

    body = ["<h1>Bands</h1>",
            f"<p>{len(bands)} historical bands as recorded in the 1902 Schedule; "
            f"{bands.succeeded_by_qid.notna().sum()} linked by succession to a modern First Nation on Wikidata. "
            f"Bands that appear only in the annual census tables are listed under "
            f"<a href='{site.url('bands', 'census')}'>Census tables</a>.</p>",
            "<table><tr><th>Band</th><th>Province</th><th>Reserves</th><th>Succeeded by</th><th>Mentions</th></tr>"]
    for bid, b in bands.sort_values("name").iterrows():
        succ = (f"<a href='http://www.wikidata.org/entity/{esc(b.succeeded_by_qid)}'>{esc(b.wd_label)}</a>"
                if not is_null(b.succeeded_by_qid) else "")
        body.append(f"<tr><td><a href='{ent_url('band', bid)}'>{esc(b['name'])}</a></td>"
                    f"<td>{esc(', '.join(PROV_SHORT.get(p, p) for p in listify(b.provinces)))}</td>"
                    f"<td>{b.n_reserves}</td><td>{succ}</td><td>{count('band', bid)}</td></tr>")
    body.append("</table>")
    site.write("bands", "\n".join(body), "Bands", [("Canada50", site.url()), ("Bands", None)])

    def person_weight(aid):
        """Total attestations across the three sources, for ranking."""
        ids = [aid] + rev_alias.get(aid, [])
        n = int(men[(men.entity_type == "agent") & men.entity_id.isin(ids)].shape[0])
        o = off_by.get(aid)
        if o is not None:
            n += len(o)
        g = teach_by.get(aid)
        if g is not None:
            n += g.drop_duplicates(["year", "school_id"]).shape[0]
        return n

    body = ["<h1>Persons</h1>",
            f"<p>{len(agent_ids):,} Department of Indian Affairs staff: report signatories, "
            "Return A officers and school teachers, matched to the LINCS Indian Affairs "
            "Agents dataset where it holds the identity, minted otherwise. "
            "Ranked by number of appearances across the three record types.</p>",
            "<table><tr><th>Name</th><th>Identity</th><th>Years</th><th>Record</th></tr>"]
    for aid in sorted(agent_ids,
                      key=lambda a: (-person_weight(a),
                                     ent_name("agent", a).split()[-1] if ent_name("agent", a) else "")):
        ids = [aid] + rev_alias.get(aid, [])
        sub = men[(men.entity_type == "agent") & men.entity_id.isin(ids)]
        years = list(sub.report_year)
        record = []
        if len(sub):
            record.append(f"{sub.drop_duplicates(['tag', 'segment_id']).shape[0]} reports")
        o = off_by.get(aid)
        if o is not None:
            years += list(o.year)
            record.append(f"Return A ×{len(o)}")
        g = teach_by.get(aid)
        if g is not None:
            years += list(g.year)
            record.append(f"schools ×{g.drop_duplicates(['year', 'school_id']).shape[0]}")
        yrs = f"{min(years)}–{max(years)}" if years else ""
        body.append(f"<tr><td><a href='{ent_url('agent', aid)}'>{esc(ent_name('agent', aid))}</a></td>"
                    f"<td>{'minted' if aid.startswith('PERSON-') else 'LINCS'}</td><td>{yrs}</td>"
                    f"<td>{esc(', '.join(record))}</td></tr>")
    body.append("</table>")
    site.write("persons", "\n".join(body), "Persons", [("Canada50", site.url()), ("Persons", None)])

    body = ["<h1>Department of Indian Affairs annual reports</h1>",
            "<table><tr><th>Year</th><th>Document</th><th>Segments</th><th>Agency letters</th><th>Linked mentions</th></tr>"]
    for tag, grp in segs.groupby("tag"):
        year = int(grp.report_year.iat[0])
        body.append(f"<tr><td><a href='{report_url[year]}'>{year}</a></td><td><code>{esc(grp.doc_id.iat[0])}</code></td>"
                    f"<td>{len(grp)}</td><td>{(grp.kind == 'agency_letter').sum()}</td>"
                    f"<td>{int(men[(men.tag == tag) & men.primary].shape[0])}</td></tr>")
    body.append("</table>")
    site.write("reports", "\n".join(body), "Reports", [("Canada50", site.url()), ("Reports", None)])

    body = [f"<h1>Canada50 — Department of Indian Affairs, 1880–1930</h1>",
            "<p><strong>Local evaluation build.</strong> Entity hub pages and the report edition "
            "for the DIA annual reports, generated deterministically from the Canada50 registries. "
            "This material is not deployed publicly pending consultation with Indigenous colleagues.</p>",
            "<ul>",
            f"<li><a href='{site.url('agencies')}'>Agencies</a> — {len(chains)} chains</li>",
            f"<li><a href='{site.url('reserves')}'>Reserves</a> — {len(reserves)} (Schedule of 1902)</li>",
            f"<li><a href='{site.url('bands')}'>Bands</a> — {len(bands)}</li>",
            f"<li><a href='{site.url('persons')}'>Persons</a> — {len(agent_ids)}</li>",
            f"<li><a href='{site.url('reports')}'>Reports</a> — {segs.tag.nunique()} volumes, {len(segs):,} segments</li>",
            "</ul>",
            f"<p class=cite>{int(men.primary.sum()):,} high/medium mentions and {int((~men.primary).sum()):,} "
            f"unverified mentions across {men.entity_id.nunique():,} entities. Mention links are "
            "stand-off annotations (<code>registries/annotations/</code>) bound to segment text versions.</p>"]
    site.write("", "\n".join(body), "Canada50 DIA wiki", [("Canada50", None)])

    (site.out / "index.json").write_text(json.dumps(index_json, indent=0), encoding="utf-8")
    (site.out / "editions.json").write_text(json.dumps(editions, indent=0), encoding="utf-8")
    print(f"\nwrote {site.n:,} pages to {site.out}; index.json {len(index_json):,} entities; "
          f"editions.json {len(editions)} documents")


if __name__ == "__main__":
    sys.exit(main())
