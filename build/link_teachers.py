#!/usr/bin/env python3
"""Teachers in the school statements -> person identities (KG_BUILD_PLAN step 6).

    python3 build/link_teachers.py [--accept 0.90] [--lincs-accept 0.95]

The school statements print a teacher for 10,454 school-years, 1896-1930
(5,225 distinct strings).  LINCS is the identity authority for DIA staff,
but teachers are essentially absent from it -- Hoy's person-level rows
behind LINCS hold only ~38 teacher entries against 5,000+ teachers here --
so this script MINTS teacher persons, after checking each identity against
the LINCS agents so the few who do appear there (Rev. principals who were
also salaried missionaries, agents' wives on day-school pay) carry the
LINCS URI instead of a minted one.

Name matching reuses build/person_names.py -- do not rewrite it; its rules
and calibration are documented in the person-name-matching memory.

Identity model, deliberately conservative:
  * strings at the SAME school cluster by complete-linkage name score --
    the same-school prior is strong, and OCR respellings across years
    ("Arsenault"/"Arsennault") are the common case;
  * across schools, only EXACT folded-name equality within one province
    merges ("J. Oliver" at two schools of one agency is one teacher; the
    same string in two provinces stays two identities with a review row);
  * a title-class conflict (Rev./Mr. vs Miss/Mrs./Sister) blocks any merge;
    Miss -> Mrs. is marriage, not a different person, so f == f merges.

Outputs:
  registries/entities/persons_teachers.parquet     one row per identity
  registries/annotations/teacher_attestations.parquet  one row per school-year
  registries/crosswalks/teachers_review.csv        cross-province same names,
                                                   LINCS candidates below gate
"""
import argparse
import collections
import csv
import itertools
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

from person_names import _fold, blocking_keys, parse, score  # noqa: E402

# religious-order post-nominals the school statements print after a comma;
# person_names strips degrees but not these, and a bare comma+acronym would
# otherwise read as an inverted "Surname, Initials" name
ORDERS = re.compile(
    r",?\s*\(?(O\.?\s?M\.?\s?I|S\.?\s?J|O\.?\s?S\.?\s?B|S\.?\s?G\.?\s?M"
    r"|S\.?\s?N\.?\s?J\.?\s?M|F\.?\s?S\.?\s?C|C\.?\s?S\.?\s?B|S\.?\s?S\.?\s?A"
    r"|O\.?\s?S\.?\s?F|R\.?\s?S\.?\s?M|C\.?\s?N\.?\s?D)\.?\)?\s*$",
    re.I,
)
# "F.A. Rand, M.D., Agent" -- Nova Scotia county rows print the AGENT in the
# teacher column; the row is a real staff attestation but the role is agent
ROLE_SUFFIX = re.compile(r",?\s*(Agent|Instructor|Farmer)\.?\s*$", re.I)
TITLE = re.compile(
    r"^\s*(Rev\.?\s+Sister|Rev\.?\s+Mother|Rev\.?|Sister|Sisters|Miss|Mrs\.?"
    r"|Mr\.?|Bro(?:ther)?\.?|Dr\.?|Madame|Mme\.?|S[oœ]ur|Father|Fr\.?)\s+",
    re.I,
)
FEMALE = {"sister", "sisters", "miss", "mrs", "madame", "mme", "soeur",
          "rev sister", "rev mother"}
MALE = {"rev", "mr", "bro", "brother", "father", "fr"}
# occupation words that leaked into the teacher column; not names
NOT_A_NAME = {"dispenser", "missionary", "constable", "teacher", "matron",
              "principal", "agent", "school field matron", "the missionary"}


def clean(raw):
    """(name, title, title_class, role) with orders and title split off."""
    s = str(raw).strip().rstrip(".,;")
    role = "teacher"
    m = ROLE_SUFFIX.search(s)
    if m:
        role = m.group(1).lower()
        s = s[: m.start()].strip().rstrip(",")
    for _ in range(2):
        s = ORDERS.sub("", s).strip().rstrip(",")
    title = ""
    m = TITLE.match(s)
    if m:
        title = re.sub(r"[.\s]+", " ", m.group(1)).strip().lower()
        s = s[m.end():].strip()
    tclass = "f" if title in FEMALE else "m" if title in MALE else ""
    return s, title, tclass, role


def title_conflict(a, b):
    return a and b and a != b


class Clusters:
    """Union-find over attestation-name nodes, with complete-linkage checks
    done by the callers before union()."""

    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def slugify(name):
    s = _fold(name).replace("'", "").replace(",", " ")
    return re.sub(r"[\s-]+", "-", s).strip("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", type=float, default=0.90,
                    help="same-school complete-linkage threshold")
    ap.add_argument("--lincs-accept", type=float, default=0.95)
    ap.add_argument("--lincs-margin", type=float, default=0.03)
    args = ap.parse_args()

    sa = pd.read_parquet(ROOT / "registries/annotations/school_attestations.parquet")
    t = sa[sa.teacher.notna() & (sa.teacher != "None")
           & (sa.teacher.str.strip() != "")].copy()
    t[["name", "title", "tclass", "role"]] = [clean(x) for x in t.teacher]
    junk = t.name.str.lower().isin(NOT_A_NAME) | (t.name.str.len() < 2)
    print(f"{len(t):,} teacher school-years, {junk.sum()} occupation-word rows dropped")
    t = t[~junk].copy()
    t["fold"] = t.name.map(_fold)

    # ---- nodes: one per (school, folded string); parse once per string -----
    parsed = {f: parse(f) for f in t.fold.unique()}
    # node title-class by majority vote -- one OCR "Miss" on a Rev.'s row
    # ("Miss C.D. White" among 26 "Rev. C.D. White") must not flip the class
    tclass_votes = collections.defaultdict(collections.Counter)
    for r in t.itertuples():
        if r.tclass:
            tclass_votes[(r.school_id, r.fold)][r.tclass] += 1
    tclass_of = {n: c.most_common(1)[0][0] for n, c in tclass_votes.items()}

    uf = Clusters()
    for node in {(r.school_id, r.fold) for r in t.itertuples()}:
        uf.find(node)

    # ---- pass 1: same-school clustering, complete linkage ------------------
    by_school = collections.defaultdict(set)
    for sch, f in uf.parent:
        by_school[sch].add(f)
    for sch, folds in by_school.items():
        folds = sorted(folds)
        groups = []  # list of lists of folds, complete linkage
        for f in folds:
            placed = False
            for g in groups:
                ok = all(
                    score(parsed[f], parsed[h]) >= args.accept
                    and not title_conflict(tclass_of.get((sch, f), ""),
                                           tclass_of.get((sch, h), ""))
                    for h in g
                )
                if ok:
                    g.append(f)
                    placed = True
                    break
            if not placed:
                groups.append([f])
        for g in groups:
            for h in g[1:]:
                uf.union((sch, g[0]), (sch, h))

    # ---- pass 2: exact string across schools, same province ----------------
    def prov_norm(p):
        return re.sub(r"[^A-Z]", "", str(p).upper())  # NORTH WEST == NORTHWEST

    prov_of = collections.defaultdict(set)
    for r in t.itertuples():
        if prov_norm(r.province):
            prov_of[(r.school_id, r.fold)].add(prov_norm(r.province))
    by_fold = collections.defaultdict(list)
    for node in list(uf.parent):
        by_fold[node[1]].append(node)
    cross_prov_review = []
    for f, nodes in by_fold.items():
        if len(nodes) < 2:
            continue
        by_prov = collections.defaultdict(list)
        for n in nodes:
            for p in prov_of[n]:
                by_prov[p].append(n)
        for p, ns in by_prov.items():
            for n in ns[1:]:
                if not title_conflict(tclass_of.get(ns[0], ""),
                                      tclass_of.get(n, "")):
                    uf.union(ns[0], n)
        if len(by_prov) > 1 and len(parsed[f][2]) > 0:  # has a given name
            cross_prov_review.append(dict(
                kind="same_name_two_provinces", name=f,
                detail="; ".join(f"{p}: {len(ns)} schools"
                                 for p, ns in sorted(by_prov.items())),
            ))

    # ---- collect identities ------------------------------------------------
    members = collections.defaultdict(list)
    for node in uf.parent:
        members[uf.find(node)].append(node)
    t["node"] = list(zip(t.school_id, t.fold))
    t["root"] = t.node.map(uf.find)

    # ---- LINCS check -------------------------------------------------------
    lincs = pd.read_parquet(ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    lincs["year"] = pd.to_datetime(lincs.begin, errors="coerce").dt.year
    agents = lincs.groupby("agent").agg(
        label=("agent_label", "first"),
        years=("year", lambda y: set(y.dropna().astype(int))),
    )
    lincs_parsed = {a: parse(r.label) for a, r in agents.iterrows()}
    lincs_block = collections.defaultdict(list)
    for a, p in lincs_parsed.items():
        for k in blocking_keys(p):
            lincs_block[k].append(a)

    def strong_given(pa, pb):
        """Two agreeing initials, or the same spelled-out first name.

        LINCS holds almost no teachers, so a bare surname + one initial is
        not enough to say our teacher IS the agent -- "J. Jackson" the
        teacher may only share a common name with "J. Jackson" the agent.
        """
        _, ia, ga = pa
        _, ib, gb = pb
        return ((len(ia) >= 2 and ia == ib)
                or bool(ga and gb and len(ga[0]) >= 3 and ga[0] == gb[0]))

    def lincs_match(fold_names, years, tclass):
        cands = {}
        for f in fold_names:
            p = parsed[f]
            seen = set()
            for k in blocking_keys(p):
                for a in lincs_block.get(k, ()):
                    if a in seen:
                        continue
                    seen.add(a)
                    s = score(p, lincs_parsed[a])
                    if s > cands.get(a, 0.0):
                        cands[a] = s
        if not cands:
            return None, []
        ranked = sorted(cands.items(), key=lambda kv: -kv[1])
        best, s = ranked[0]
        overlap = bool(agents.loc[best].years
                       & {y for yr in years for y in range(yr - 2, yr + 3)})
        margin = s - ranked[1][1] if len(ranked) > 1 else 1.0
        # a female-titled teacher never auto-links: "Mrs. W.R. Tucker" is the
        # agent W.R. Tucker's wife carrying his initials, not the agent
        accepted = (s >= args.lincs_accept and margin >= args.lincs_margin
                    and overlap and tclass != "f"
                    and any(strong_given(parsed[f], lincs_parsed[best])
                            for f in fold_names))
        return (best if accepted else None), ranked[:3]

    existing = pd.read_parquet(ROOT / "registries/entities/persons_minted.parquet")
    taken = set(existing.person_id)

    persons, att_person, lincs_review = [], {}, []
    used_slugs = collections.Counter()
    for root, nodes in sorted(members.items()):
        rows = t[t.root == root]
        canon = rows.name.mode().iat[0]
        title = rows[rows.title != ""].title.mode()
        tcl = rows[rows.tclass != ""].tclass.mode()
        tcl = tcl.iat[0] if len(tcl) else ""
        years = sorted(rows.year.unique())
        folds = {n[1] for n in nodes}
        agent, ranked = lincs_match(folds, years, tcl)
        if ranked and not agent and ranked[0][1] >= 0.88:
            lincs_review.append(dict(
                kind="lincs_candidate", name=canon,
                detail="; ".join(f"{agents.loc[a].label} {s:.2f}" for a, s in ranked),
            ))
        slug = slugify(canon) or "unnamed"
        used_slugs[slug] += 1
        if used_slugs[slug] > 1:
            slug = f"{slug}-{used_slugs[slug]}"
        pid = f"PERSON-{slug}"
        if pid in taken:  # a signatory already holds this slug; same-name check
            lincs_review.append(dict(
                kind="slug_collision_with_signatory", name=canon,
                detail=pid,
            ))
            pid = f"PERSON-{slug}-t"
        persons.append(dict(
            person_id=pid, name=canon,
            title=title.iat[0] if len(title) else "",
            role=rows.role.mode().iat[0],
            first_year=int(min(years)), last_year=int(max(years)),
            n_attestations=len(rows),
            n_schools=rows.school_id.nunique(),
            schools=sorted(rows.school_id.unique()),
            provinces=sorted({str(p) for p in rows.province.dropna()}),
            lincs_agent=agent or "",
            uri=(agent or f"https://jimclifford.ca/canada50/persons/{slug}"),
            uri_source=("lincs" if agent else "minted"),
            source="school_teacher",
        ))
        for n in nodes:
            att_person[n] = pid

    # ---- one LINCS agent == one person: merge identities the authority
    # unifies (de Molitor taught in NS then BC; the name alone kept them apart)
    by_agent = collections.defaultdict(list)
    for p in persons:
        if p["lincs_agent"]:
            by_agent[p["lincs_agent"]].append(p)
    remap = {}
    for agent_uri, ps in by_agent.items():
        if len(ps) < 2:
            continue
        ps.sort(key=lambda p: -p["n_attestations"])
        keep = ps[0]
        for p in ps[1:]:
            remap[p["person_id"]] = keep["person_id"]
            keep["first_year"] = min(keep["first_year"], p["first_year"])
            keep["last_year"] = max(keep["last_year"], p["last_year"])
            keep["n_attestations"] += p["n_attestations"]
            keep["schools"] = sorted(set(keep["schools"]) | set(p["schools"]))
            keep["n_schools"] = len(keep["schools"])
            keep["provinces"] = sorted(set(keep["provinces"]) | set(p["provinces"]))
            persons.remove(p)

    t["person_id"] = t.node.map(att_person).map(lambda p: remap.get(p, p))
    att = t[["school_id", "year", "person_id", "teacher", "name", "title",
             "role", "page", "chunk", "confidence"]].rename(
        columns={"teacher": "teacher_as_printed", "name": "name_clean"})

    pdf = pd.DataFrame(persons)
    pdf.to_parquet(ROOT / "registries/entities/persons_teachers.parquet", index=False)
    att.to_parquet(ROOT / "registries/annotations/teacher_attestations.parquet",
                   index=False)
    review = pd.DataFrame(cross_prov_review + lincs_review)
    review.to_csv(ROOT / "registries/crosswalks/teachers_review.csv", index=False,
                  quoting=csv.QUOTE_MINIMAL)

    print(f"teacher identities: {len(pdf):,} "
          f"({(pdf.uri_source == 'lincs').sum()} carry a LINCS URI)")
    print(f"attestations: {len(att):,} school-years, "
          f"{att.person_id.nunique():,} persons, {att.year.min()}-{att.year.max()}")
    print(f"multi-year identities: {(pdf.last_year > pdf.first_year).sum():,}; "
          f"multi-school: {(pdf.n_schools > 1).sum():,}")
    print(f"review rows: {len(review)} "
          f"({sum(1 for r in cross_prov_review)} cross-province, "
          f"{sum(1 for r in lincs_review if r['kind'] == 'lincs_candidate')} "
          f"LINCS candidates below gate)")


if __name__ == "__main__":
    main()
