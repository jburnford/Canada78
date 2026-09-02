#!/usr/bin/env python3
"""Attribute every DIA report letter to the agency or school it reports on.

`build_agency_chains.py` attributes a segment to a chain only when the
segment *heading* names the unit — 1,762 of 4,437 agency letters. The rest
are headed by the salutation ("The Honourable", "Frank Pedley, Esq.") because
the printer moved the dateline to the foot of the letter after ~1905, or are
principals' reports on a school. Without attribution `search(agency=…)` and
"reports from this agency" miss most of the corpus.

Methods, in priority order (first hit wins; every verdict carries evidence):

  heading          the chain member already recorded for the heading
  school           heading or opening sentence names a school ("report on
                   the Mount Elgin Industrial Institute") matched to a
                   schools-registry name variant with type words stripped;
                   the segment's province breaks ties
  dateline         the dated place line at the head (to ~1905) or foot of the
                   letter — "NOVA SCOTIA, MICMACS OF QUEENS AND LUNENBURG
                   COUNTIES, CALEDONIA, June 1, 1909" — split on commas and
                   matched piecewise to a chain (canonicalised as headings
                   are) or, failing that, to an agstat agency identity
                   (the Nova Scotia county agencies, Moravian, Bécancour…
                   that never had a heading of their own)
  phrase           "… in regard to the Caughnawaga agency" in the first 600
                   characters, or a unit name in the last 400
  signature        a high/medium agent mention (the signature) whose LINCS
                   posting overlapping the report year names a chain's group
  mentions         the reserves and bands the letter itself names (high/
                   medium mentions), taken to the unit that administered
                   them (1902 Schedule division; census agency within ±3
                   years); a unique or 3:1 winner → medium (one mention → low)
  signature_prior  the same agent signed a letter attributed by one of the
                   methods above within ±3 years, for exactly one unit

Every row also gets a `letter_kind` (agency_report, school_report, audit,
commission, commissioner, inspection, survey, table_fragment, unknown) so
the residue is classified rather than merely unattributed. A trailing
dateline block belongs to the *next* letter in every era: the printer set
"place, date" before the salutation and the segmenter's walk-back left it in
the previous segment (verified 1880 and 1909).

Output: registries/annotations/segment_attribution.parquet — one row per
agency_letter segment (entity_type agency|school|None, entity_id, method,
confidence, evidence); registries/crosswalks/segment_attribution_review.csv
for the ambiguous and unmatched.

    python3 build/attribute_segments.py
"""
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
from build_agency_chains import canonicalize  # noqa: E402

DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
OUT = ROOT / "registries/annotations/segment_attribution.parquet"
REVIEW = ROOT / "registries/crosswalks/segment_attribution_review.csv"
LINKS = ROOT / "registries/crosswalks/agstat_chain_dateline_links.csv"

MONTHS = ("January|February|March|April|May|June|July|August|September|October|"
          "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec")
DATELINE_RE = re.compile(
    rf"([A-Z][A-Z0-9 .,'’&()\-]{{5,160}}?)[,.]?\s+"
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?[,.]?\s+(?:{MONTHS})\.?|(?:{MONTHS})\.?\s+\d{{1,2}})"
    rf"[,.]?\s+(1[89]\d\d)")
UNIT_RE = re.compile(
    r"((?:[A-Z][\w'’.\-]+[ ,]+){1,5}(?i:agency|superintendency|inspectorate))\b")
SCHOOL_WORDS = ("SCHOOL", "INSTITUTE", "INSTITUTION", "HOME", "ACADEMY")
SCHOOL_HEAD_RE = re.compile(
    r"report (?:on|of|for) the ([A-Z][\w'’.\- ]+?(?:school|institute|institution|home|academy))\b",
    re.I)
STRIP_RE = re.compile(
    r"\b(indian|industrial|boarding|day|school|institute|institution|home|academy|the|"
    r"report|on|of|for|annual|reserve|mission|roman|catholic|r\.?c\.?|church of england|"
    r"c\.?e\.?|methodist|presbyterian|anglican)\b")
CLOSE_RE = re.compile(r"I have,? (?:&|etc)|I have the hono?u?r to be|obedient servant", re.I)
DATELINE_DROP = re.compile(
    r"^(province of|micmacs of|indians of|the|county of|district of|indian district|"
    r"indian office|office of the|treaty no \d+|no \d+)\s*")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def pool_code(s):
    """Province string (any of the printers' forms) → pool code, or None."""
    u = str(s or "").upper()
    for pat, code in (("NOVA", "NS"), ("NEW BRUNSWICK", "NB"), ("PRINCE", "PE"), ("QUEBEC", "QC"),
                      ("ONTARIO", "ON"), ("BRITISH", "BC"), ("YUKON", "YT"),
                      ("MANITOBA", "PR"), ("SASK", "PR"), ("ALBERTA", "PR"), ("PRAIRIES", "PR"),
                      ("NORTH-WEST", "PR"), ("NORTHWEST", "PR"), ("NWT", "PR"), ("TERRITOR", "PR")):
        if pat in u:
            return code
    return None


NON_AGENCY_HEADING = re.compile(r"AUDIT|AUDITOR|COMMISSION\b|SURVEY|D\.L\.S", re.I)


def squash(s):
    return norm(s).replace(" ", "")


def school_key(s):
    return STRIP_RE.sub(" ", norm(s)).strip()


def school_forms(s):
    """'Yale (All Hallows) Boarding' → itself, 'Yale Boarding', 'All Hallows'."""
    s = str(s)
    out = [s, re.sub(r"\(.*?\)", " ", s)]
    out += re.findall(r"\(([^)]+)\)", s)
    return [o for o in out if o.strip()]


def read_body(tag):
    raw = (DIA_MD / f"{tag}.md").read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    return raw[m.end():] if m else raw


def main():
    segs = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet")
    segs["report_year"] = segs.tag.str.extract(r"(\d{4})").astype(int)
    members = pd.read_parquet(ROOT / "registries/entities/agency_chain_members.parquet")
    chains = pd.read_parquet(ROOT / "registries/entities/agency_chains.parquet")
    heading_chain = {(m.tag, m.segment_id): m.chain_id for m in members.itertuples()}
    canon2chain = dict(zip(members.canonical, members.chain_id))
    canon2chain.update(dict(zip(chains.canonical, chains.chain_id)))
    chain_canonical_name = dict(zip(chains.chain_id, chains.canonical))
    unit_chain_links = defaultdict(int)   # (agstat unit, chain) named together in one dateline
    lincs2chain = {u: c for c, u in zip(chains.chain_id, chains.lincs_uri)
                   if isinstance(u, str) and u}

    # agstat agency identities as a second gazetteer (county agencies etc.)
    agp = ROOT / "registries/entities/agencies_agstat.parquet"
    agstat_key = {}          # key -> {target: (first_year, last_year, n_years)}
    if agp.exists():
        ag = pd.read_parquet(agp)
        if "identity_kind" in ag.columns:
            ag = ag[ag.identity_kind == "agency"]
        for r in ag.itertuples():
            target = r.chain_id if r.link_method else r.agency_id
            names = [r.name] + (str(r.name_variants).split("|") if r.name_variants else [])
            if getattr(r, "name_clean", ""):
                names.append(r.name_clean)
            for n in names:
                k = squash(re.sub(r"\b(agency|ag'cy|superintendency|superintend'cy|"
                                  r"inspectorate|indian|county|counties|co)\b", " ", n, flags=re.I))
                if len(k) >= 5:
                    agstat_key.setdefault(k, {})[target] = (int(r.first_year), int(r.last_year),
                                                            int(r.n_years), pool_code(r.pool))

    def unit_for_key(k, year, prov=None, strict=False):
        """Several agstat identities share a key ("Halifax" / "Halifax
        County"; "Victoria" county N.B. vs Victoria B.C.): keep those in the
        letter's province when known, prefer the one attested in that year,
        then the longest."""
        t = agstat_key.get(k)
        if not t:
            return None
        if prov:
            same = {u: v for u, v in t.items() if v[3] in (None, prov)}
            # a dateline's province is inferred and may be wrong: keep a unique
            # key even outside it; a reserve's province is printed, so be strict
            if same or strict or len(t) > 1:
                t = same
            if not t:
                return None
        if len(t) == 1:
            return next(iter(t))
        covering = [u for u, (f, l, n, p) in t.items() if f - 2 <= year <= l + 2]
        pool = covering or list(t)
        return max(pool, key=lambda u: t[u][2])

    men = pd.read_parquet(ROOT / "registries/annotations/mentions_dia.parquet")
    t2 = ROOT / "registries/annotations/mentions_dia_tier2.parquet"
    if t2.exists():
        men = pd.concat([men, pd.read_parquet(t2)], ignore_index=True)
    agents = men[(men.entity_type == "agent") & men.confidence.isin(["high", "medium"])]
    pm = pd.read_parquet(ROOT / "registries/entities/persons_minted.parquet")
    to_lincs = ({p: a for p, a in zip(pm.person_id, pm.lincs_agent) if isinstance(a, str) and a}
                if "lincs_agent" in pm.columns else {})
    agents_by_seg = defaultdict(set)
    for m in agents.itertuples():
        agents_by_seg[(m.tag, m.segment_id)].add(to_lincs.get(m.entity_id, m.entity_id))
    acts = pd.read_parquet(ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    acts = acts[acts.group.isin(lincs2chain)].copy()
    acts["y0"] = pd.to_datetime(acts.begin, errors="coerce").dt.year
    acts["y1"] = pd.to_datetime(acts.end, errors="coerce").dt.year
    acts_by_agent = {a: g for a, g in acts.groupby("agent")}

    # ---- place mentions → unit, for the mentions vote
    reserves = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    reserve_unit = {}
    for r in reserves.itertuples():
        if not isinstance(r.division_norm, str) or not r.division_norm:
            continue
        if r.division_type == "agency":
            reserve_unit[r.reserve_id] = ("AG-" + re.sub(r"[^a-z0-9]+", "-", r.division_norm.lower()).strip("-"),
                                          None, pool_code(r.province))
        elif r.division_type == "county":   # treaty sections are not administering units
            reserve_unit[r.reserve_id] = (None, squash(re.sub(r"\b(county|counties|co)\b", " ", r.division_norm, flags=re.I)),
                                          pool_code(r.province))
    bgp = ROOT / "registries/entities/bands_census_grounded.parquet"
    census_of_curated = defaultdict(list)
    if bgp.exists():
        for r in pd.read_parquet(bgp).itertuples():
            if isinstance(r.curated_band_id, str) and r.curated_band_id:
                census_of_curated[r.curated_band_id].append(r.band_id)
    band_att = pd.read_parquet(ROOT / "registries/annotations/band_attestations.parquet")
    band_agency_years = defaultdict(list)   # band_id -> [(year, agency string)]
    for r in band_att.dropna(subset=["agency"]).itertuples():
        band_agency_years[r.band_id].append((int(r.year), r.agency))
    place_mentions = defaultdict(list)      # (tag, segment_id) -> [(type, id)]
    for m in men[men.confidence.isin(["high", "medium"]) & men.entity_type.isin(["reserve", "band"])].itertuples():
        place_mentions[(m.tag, m.segment_id)].append((m.entity_type, m.entity_id))

    chain_ids = set(canon2chain.values())

    def unit_for_agency_string(name, year):
        for cand in (str(name), str(name) + " AGENCY"):
            c, _ = canonicalize(cand)
            if c and c in canon2chain:
                return canon2chain[c]
        k = squash(re.sub(r"\b(agency|ag'cy|superintendency|inspectorate|indian|con|county|counties|co)\b",
                          " ", str(name), flags=re.I))
        return unit_for_key(k, year) if len(k) >= 5 else None

    def mention_votes(key, year):
        votes = defaultdict(set)
        for t, i in place_mentions.get(key, ()):
            if t == "reserve" and i in reserve_unit:
                chain, ckey, prov = reserve_unit[i]
                u = chain if chain in chain_ids else (unit_for_key(ckey, year, prov, strict=True) if ckey else None)
                if u:
                    votes[u].add(("reserve", i))
            elif t == "band":
                for cid in census_of_curated.get(i, []):
                    near = [ag for y, ag in band_agency_years.get(cid, []) if abs(y - year) <= 3]
                    for ag in set(near):
                        u = unit_for_agency_string(ag, year)
                        if u:
                            votes[u].add(("band", i))
        return votes

    schools = pd.read_parquet(ROOT / "registries/entities/schools.parquet")
    sch_idx = defaultdict(set)
    sch_prov = dict(zip(schools.school_id, schools.province_pool))
    sch_span = {s.school_id: (s.first_year, s.last_year, s.n_years) for s in schools.itertuples()}
    for s in schools.itertuples():
        for v in [s.name] + str(s.name_variants).split("|"):
            for form in school_forms(v):
                k = school_key(form)
                if len(k) >= 4:
                    sch_idx[k].add(s.school_id)

    def pick_school(ids, year):
        """Several registry identities for one printed school (the registry
        splits e.g. 'Battleford Industrial' 1896–97 from 'Battleford'
        1898–1915): prefer the one attested in that year, then the longest."""
        if len(ids) <= 1:
            return ids
        covering = [i for i in ids if sch_span[i][0] - 1 <= year <= sch_span[i][1] + 1]
        pool = covering or list(ids)
        best = max(pool, key=lambda i: sch_span[i][2])
        return {best} if (len(covering) == 1 or best) else ids

    def dateline_targets(chunk, year=None, prov=None):
        """Chains / agstat identities named in a dated place line."""
        out = []
        for m in DATELINE_RE.finditer(chunk):
            line = m.group(1)
            pieces = [p.strip(" .") for p in re.split(r"[,;]|\s{2,}", line) if p.strip(" .")]
            # try progressively longer joins so "TREATY No. 4, INDIAN HEAD AGENCY" works
            cands = pieces + [", ".join(pieces[i:j]) for i in range(len(pieces))
                              for j in range(i + 2, min(len(pieces), i + 3) + 1)]
            for c in cands:
                hit = None
                for form in (c, c + " AGENCY"):
                    canon, _ = canonicalize(form)
                    if canon and canon in canon2chain:
                        hit = canon2chain[canon]
                        break
                if hit is None:
                    k = DATELINE_DROP.sub("", norm(c))
                    k = squash(re.sub(r"\b(agency|counties|county|co)\b", " ", k))
                    hit = unit_for_key(k, year, prov) if len(k) >= 5 else None
                if hit:
                    out.append((hit, c))
        return out

    bodies = {}
    rows, review = [], []
    letters = segs[segs.kind == "agency_letter"]
    order = segs.sort_values(["tag", "ordinal"])
    nxt = {}
    for tag, g in order.groupby("tag"):
        ids = list(g.segment_id)
        for i in range(len(ids) - 1):
            nxt[(tag, ids[i])] = (tag, ids[i + 1])
    carry = {}   # segment key -> dateline targets carried from the previous letter's tail
    for s in letters.itertuples():
        key = (s.tag, s.segment_id)
        base = dict(doc_id=s.doc_id, tag=s.tag, segment_id=s.segment_id,
                    report_year=int(s.report_year), heading=s.heading)
        if key in heading_chain:
            rows.append(dict(base, entity_type="agency", entity_id=heading_chain[key],
                             method="heading", confidence="high", evidence=s.heading))
            continue
        if s.tag not in bodies:
            bodies[s.tag] = read_body(s.tag)
        text = re.sub(r"<!-- page \d+ -->", " ", bodies[s.tag][s.char_start:s.char_end])
        text = re.sub(r"\s+", " ", text)   # datelines are set one element per line
        head, tail = text[:600], text[-450:]
        H = s.heading.upper()

        if NON_AGENCY_HEADING.search(H) or NON_AGENCY_HEADING.search(head[:150]):
            rows.append(dict(base, entity_type=None, entity_id=None, method="none",
                             confidence="none", evidence=re.sub(r"\s+", " ", head[:120])))
            continue
        m = SCHOOL_HEAD_RE.search(head)
        if any(w in H for w in SCHOOL_WORDS) or m:
            cand = s.heading if any(w in H for w in SCHOOL_WORDS) else m.group(1)
            cand = re.sub(r",?\s+\d{1,2}(st|nd|rd|th)?\s+[A-Z][a-z]+,?\s+1[89]\d\d.*$", "", cand)
            ids = set()
            for form in school_forms(cand):
                ids |= sch_idx.get(school_key(form), set())
            if len(ids) > 1 and s.province:
                narrowed = {i for i in ids if sch_prov.get(i)
                            and norm(sch_prov[i]).split()[0] in norm(s.province)}
                ids = narrowed or ids
            ids = pick_school(ids, s.report_year)
            if len(ids) == 1:
                rows.append(dict(base, entity_type="school", entity_id=next(iter(ids)),
                                 method="school", confidence="high", evidence=cand))
            else:
                review.append(dict(base, method="school", candidates="|".join(sorted(ids)),
                                   evidence=cand,
                                   reason="ambiguous" if ids else "no school matched"))
                rows.append(dict(base, entity_type=None, entity_id=None, method="school",
                                 confidence="none", evidence=cand))
            continue

        # A dateline printed after the closing formula is the *next* letter's
        # header block, which the segmenter's walk-back left behind here
        # (Pedley era: "I have, &c., J.D. MORIN, Indian Agent. PROVINCE OF
        # QUEBEC, MICMACS OF RESTIGOUCHE, POINTE LA GARDE, May 1, 1909.").
        close = CLOSE_RE.search(tail)
        prov = pool_code(s.province) or pool_code(head[:200])
        tail_dl = dateline_targets(tail, s.report_year, prov)
        if os.environ.get("DEBUG_SEG") == s.segment_id:
            print("DEBUG", s.tag, s.segment_id, "prov", prov, "\n head:", head[:200], "\n tail_dl:", tail_dl,
                  "\n head_dl:", dateline_targets(head[:400], s.report_year, prov),
                  "\n close:", CLOSE_RE.search(tail), "\n dm:", DATELINE_RE.search(tail))
        if tail_dl and close:
            dm = DATELINE_RE.search(tail)
            if dm and dm.start() > close.start():
                carry[nxt.get(key)] = tail_dl
                tail_dl = []
        found = [("dateline_tail", c, ev) for c, ev in tail_dl] \
            or [("dateline_head", c, ev) for c, ev in dateline_targets(head[:400], s.report_year, prov)] \
            or [("dateline_prev", c, ev) for c, ev in carry.get(key, [])]
        if not found:
            for where, chunk in (("phrase_head", head), ("phrase_tail", tail)):
                for mm in UNIT_RE.finditer(chunk):
                    canon, _ = canonicalize(mm.group(1))
                    if canon and canon in canon2chain:
                        found.append((where, canon2chain[canon], mm.group(1)))
        if found:
            ids = {c for _, c, _ in found}
            pick = None
            if len(ids) == 1:
                pick = next(iter(ids))
            else:
                # one unit under two names: "MICMACS OF HANTS COUNTY" (agstat
                # county unit) beside "SHUBENACADIE" (the chain seated there),
                # or a chain fragment beside its parent ("INDIAN AGENT'S OFFICE,
                # SARCEE AGENCY" / "SARCEE AGENCY"). Prefer the chain; among
                # chains the one whose name the other contains.
                chains_ = {c for c in ids if c.startswith("AG-")}
                units_ = ids - chains_
                if len(chains_) == 1:
                    pick = next(iter(chains_))
                elif len(chains_) > 1:
                    names_ = {c: chain_canonical_name.get(c, "") for c in chains_}
                    inner = [c for c in chains_ if any(names_[c] and names_[c] in names_[o] and c != o
                                                        for o in chains_)]
                    if len(inner) == 1:
                        pick = inner[0]
                if pick and units_:
                    for u in units_:
                        unit_chain_links[(u, pick)] += 1
            if pick:
                w, c, ev = next((f for f in found if f[1] == pick), found[0])
                rows.append(dict(base, entity_type="agency", entity_id=pick, method=w,
                                 confidence="high", evidence="; ".join(ev_ for _, _, ev_ in found)))
                continue
            review.append(dict(base, method=found[0][0], candidates="|".join(sorted(ids)),
                               evidence="; ".join(f"{w}:{ev}" for w, _, ev in found),
                               reason="several units named"))

        votes = mention_votes(key, s.report_year)
        if votes:
            ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
            top_n = len(ranked[0][1])
            second = len(ranked[1][1]) if len(ranked) > 1 else 0
            if len(ranked) == 1 or top_n >= 3 * max(second, 1):
                u = ranked[0][0]
                ev = ", ".join(f"{t}:{i}" for t, i in sorted(ranked[0][1])[:4])
                rows.append(dict(base, entity_type="agency", entity_id=u, method="mentions",
                                 confidence="medium" if top_n >= 2 else "low",
                                 evidence=f"{top_n} place mention(s) administered by this unit: {ev}"))
                continue
            review.append(dict(base, method="mentions", candidates="|".join(u for u, _ in ranked[:4]),
                               evidence="; ".join(f"{u}:{len(v)}" for u, v in ranked[:4]),
                               reason="place mentions point to several units"))

        hits = defaultdict(list)
        for a in agents_by_seg.get(key, ()):
            g = acts_by_agent.get(a)
            if g is None:
                continue
            for r in g.itertuples():
                if pd.isna(r.y0):
                    continue
                y1 = r.y1 if pd.notna(r.y1) else r.y0
                if r.y0 - 1 <= s.report_year <= y1 + 1:
                    hits[lincs2chain[r.group]].append((a, int(r.y0), r.group_label))
        if len(hits) == 1:
            c, ev = next(iter(hits.items()))
            rows.append(dict(base, entity_type="agency", entity_id=c, method="signature",
                             confidence="medium",
                             evidence=f"{ev[0][0]} posted {ev[0][1]} {ev[0][2]}"))
            continue
        if len(hits) > 1:
            review.append(dict(base, method="signature", candidates="|".join(sorted(hits)),
                               evidence="; ".join(f"{c}:{v[0][2]}" for c, v in hits.items()),
                               reason="agent posted to several chains"))
        rows.append(dict(base, entity_type=None, entity_id=None, method="none",
                         confidence="none", evidence=re.sub(r"\s+", " ", head[:120])))

    # ---- second pass: the same signatory's other, attributed letters
    out = pd.DataFrame(rows)
    prior = defaultdict(set)  # agent -> {(unit, year)}
    for r in out[out.entity_type == "agency"].itertuples():
        for a in agents_by_seg.get((r.tag, r.segment_id), ()):
            prior[a].add((r.entity_id, r.report_year))
    n_prior = 0
    for i in out.index[out.entity_id.isna()]:
        r = out.loc[i]
        units = defaultdict(list)
        for a in agents_by_seg.get((r.tag, r.segment_id), ()):
            for unit, y in prior.get(a, ()):
                if abs(y - r.report_year) <= 3:
                    units[unit].append((a, y))
        if len(units) == 1:
            unit, ev = next(iter(units.items()))
            out.loc[i, ["entity_type", "entity_id", "method", "confidence", "evidence"]] = [
                "agency", unit, "signature_prior", "medium",
                f"{ev[0][0]} signed for this unit in {sorted(y for _, y in ev)}"]
            n_prior += 1
        elif len(units) > 1:
            review.append(dict(doc_id=r.doc_id, tag=r.tag, segment_id=r.segment_id,
                               report_year=r.report_year, heading=r.heading,
                               method="signature_prior", candidates="|".join(sorted(units)),
                               evidence="", reason="signatory attributed to several units"))

    # ---- what kind of letter is it (for the unattributed residue especially)
    kinds = []
    for r in out.itertuples():
        H = (r.heading or "").upper()
        ev = str(r.evidence or "")
        if r.entity_type == "school" or r.method == "school":
            k = "school_report"
        elif "AUDIT" in H or "AUDITOR" in H:
            k = "audit"
        elif "COMMISSION" in H:
            k = "commission"
        elif "COMMISSIONER" in H:
            k = "commissioner"
        elif re.search(r"INSPECTOR|INSPECTORATE", H):
            k = "inspection"
        elif re.search(r"SURVEY|D\.?L\.?S\.", H + " " + ev):
            k = "survey"
        elif re.search(r"RECAPITULATION|STATEMENT|TABLE|SUMMARY", H) and r.entity_type is None:
            k = "table_fragment"
        elif r.entity_type == "agency":
            k = "agency_report"
        else:
            k = "unknown"
        kinds.append(k)
    out["letter_kind"] = kinds
    out.to_parquet(OUT, index=False)
    pd.DataFrame(review).to_csv(REVIEW, index=False)
    # agstat units that share a dateline with exactly one chain are that chain
    by_unit = defaultdict(dict)
    for (u, c), n in unit_chain_links.items():
        by_unit[u][c] = n
    links = [dict(agency_id=u, chain_id=max(cs, key=cs.get), n_datelines=sum(cs.values()),
                  other_chains="|".join(c for c in cs if c != max(cs, key=cs.get)))
             for u, cs in by_unit.items()]
    pd.DataFrame(links).to_csv(LINKS, index=False)
    print(f"{len(links)} agstat units co-named with a chain in datelines → {LINKS}")
    print(out.groupby(["letter_kind", out.entity_id.notna()]).size().to_string())
    print(out.groupby(["entity_type", "method", "confidence"], dropna=False).size().to_string())
    print(f"\n{out.entity_id.notna().sum():,} of {len(letters):,} agency letters attributed "
          f"({out.entity_id.notna().mean():.0%}); {len(review)} rows → {REVIEW}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
