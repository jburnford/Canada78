#!/usr/bin/env python3
"""Classify the identities minted from the Agricultural & Industrial
Statistics (registries/entities/agencies_agstat.parquet) by what they
actually are, and write the verdict back as `identity_kind`.

The header-class gate kept band lists and chiefs' lists out of the registry,
but the expenditure and staff tables that share those pages still minted
identities that are not agencies at all (KG_BUILD_PLAN §3.8, found
2026-09-01):

    agency         "Yarmouth County", "Moose Mountain Agency, Treaty No. 4",
                   "Isle à la Crosse", "Wood Mountain Reserve"
    staff          "Helen G. Russell", "Lazier, D.B., M.D." — headquarters and
                   outside-service staff from the salary tables
    school         "Port Simpson Girls' Home" — a school row on the same page
    ledger_person  "Mrs. Caroline Budd, No. 151 - Cumberland Band." — named
                   Indigenous individuals in commutation/annuity lists; NOT
                   published as persons (consultation; §1.3)
    band_row       "Hungry Hall No. 1, M. Begg, Agent" — the 1890s Treaty 3
                   tables key rows by band with the agent's name appended
    appropriation  "Repairs to roads and bridges Tyendinaga"

Rules are deliberately simple and every verdict is written to
registries/crosswalks/agstat_identity_kinds.csv for review. Only
`identity_kind == 'agency'` rows become agency entities in the MCP index.

    python3 build/classify_agstat_identities.py
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
from person_names import blocking_keys, parse, score  # noqa: E402

REG = ROOT / "registries/entities/agencies_agstat.parquet"
OUT = ROOT / "registries/crosswalks/agstat_identity_kinds.csv"

PLACE_WORDS = re.compile(
    r"\b(agency|agencies|ag'cy|agt|superintendency|inspectorate|district|county|counties|"
    r"co\.|reserve|reserves|band|bands|treaty|tr'ty|island|lake|river|bay|creek|"
    r"mountain|point|portage|fort|ft\.|mission|indians|nation|nations|division|"
    r"micmacs?|chippewas?|crees?|saulteaux|ojibb?ewas?|sioux|iroquois|abenakis?|"
    r"hurons?|mohawks?|algonquins?|montagnais|of|at|and|&|mainland|coast|settlement|"
    r"village|no\.)\b", re.I)
APPROPRIATION = re.compile(
    r"\b(repairs?|grant|grants|salar(y|ies)|relief|supplies|expenses?|annuit(y|ies)|"
    r"surveys?|commutation|interest|contingenc(y|ies)|payments?|purchase|"
    r"construction|maintenance|travelling|medical attendance|implements|seed|"
    r"provisions|clothing|rations|tools)\b", re.I)
AGENT_SUFFIX = re.compile(
    r",\s*([A-Z][A-Za-z.'\- ]{2,40}?),?\s*(agent|agt\.?|inspector|insp\.?|"
    r"superintendent|supt\.?|clerk|farmer|instructor|missionary)\.?\s*$", re.I)
LEDGER = re.compile(r"\bno\.?\s*\d+\b.*\bband\b", re.I)
TITLE = re.compile(r"^(mrs|miss|mr|rev|drs?|madame|mme|mlle)\b\.?|\b(m\.?d\.?|d\.?l\.?s\.?)\s*$", re.I)


def known_people():
    """Names attested as DIA staff or teachers, indexed by blocking key."""
    names = set()
    oa = ROOT / "registries/annotations/officer_attestations.parquet"
    if oa.exists():
        names |= set(pd.read_parquet(oa).name_as_printed.dropna())
    ta = ROOT / "registries/annotations/teacher_attestations.parquet"
    if ta.exists():
        names |= set(pd.read_parquet(ta).teacher_as_printed.dropna())
    li = ROOT / "registries/external/lincs_ia_activities_dedup.parquet"
    if li.exists():
        names |= set(pd.read_parquet(li).agent_label.dropna())
    idx = {}
    for n in names:
        pr = parse(n)
        if pr[0]:
            for k in blocking_keys(pr):
                idx.setdefault(k, []).append(pr)
    return idx


KNOWN = None


def is_known_person(n):
    global KNOWN
    if KNOWN is None:
        KNOWN = known_people()
    pr = parse(n)
    if not pr[0]:
        return False
    for k in blocking_keys(pr):
        for other in KNOWN.get(k, ()):
            if score(pr, other) >= 0.9:
                return True
    return False


def classify(name):
    n = str(name).strip()
    if not n:
        return "other", ""
    if LEDGER.search(n):
        return "ledger_person", ""
    if re.search(r"\b(school|schools|institute|institution|home|academy)\b", n, re.I):
        return "school", ""
    if APPROPRIATION.search(n) and not re.search(r"\b(agency|county|district)\b", n, re.I):
        return "appropriation", ""
    if re.match(r"^(cleansing|destroying|spraying|clearing|building|erecting)\b", n, re.I):
        return "appropriation", ""
    m = AGENT_SUFFIX.search(n)
    if m:
        head = n[: m.start()].strip(" ,.")
        if re.search(r"\bno\.?\s*\d+|\bband\b", head, re.I):
            return "band_row", head
        return "agency", head
    # "Maria Agency, Rev. J. Gagné": unit before the comma, a person after it
    if "," in n:
        head, _, tail = n.partition(",")
        if re.search(r"\b(agency|ag'cy|superintendency|county|district)\b", head, re.I) \
                and parse(tail)[1]:
            return "agency", head.strip(" ,.")
    bare = PLACE_WORDS.sub(" ", n)
    if PLACE_WORDS.search(n) and len(bare.split()) < len(n.split()):
        return "agency", ""
    variants, initials, given = parse(n)
    toks = re.sub(r"[^A-Za-z' ]", " ", n).split()
    if not variants or not 1 < len(toks) <= 5:
        return "agency", ""
    # inverted "Jones, A.E." or a title/degree is person-shaped on its own;
    # a bare "John McGirr" / "Lac Seul" needs a known DIA person to match
    if TITLE.search(n) or ("," in n and initials):
        return "staff", ""
    if initials and is_known_person(n):
        return "staff", ""
    return "agency", ""


def main():
    ag = pd.read_parquet(REG)
    kinds, cleaned = [], []
    for r in ag.itertuples():
        k, c = classify(r.name)
        # a real chain link is stronger than the name heuristic
        if r.link_method and k in ("staff", "appropriation"):
            k = "agency"
        kinds.append(k)
        cleaned.append(c)
    ag["identity_kind"] = kinds
    ag["name_clean"] = cleaned
    ag.to_parquet(REG, index=False)
    rev = ag[["agency_id", "name", "name_clean", "identity_kind", "pool", "link_method",
              "chain_id", "n_years", "n_rows"]].sort_values(["identity_kind", "name"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rev.to_csv(OUT, index=False)
    print(ag.identity_kind.value_counts().to_string())
    for k in ("staff", "ledger_person", "band_row", "appropriation"):
        s = ag[ag.identity_kind == k].name.head(6).tolist()
        print(f"  {k}: {s}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
