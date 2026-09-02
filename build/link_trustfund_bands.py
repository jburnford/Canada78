#!/usr/bin/env python3
"""Link Indian Trust Fund ledger accounts (Return B/C) to census band identities.

Step 7 remainder of docs/KG_BUILD_PLAN.md.  parse_return_b.py left 2,703
account-years under 1,052 distinct printed account names; most are band
accounts ("Chippewas of Nawash", "Batchewana Indians, Ont. (No. 1)"), the
rest are department-level funds (Indian Land Management Fund, Salaries,
Sundry Disbursements) that must not be forced onto a band.

Inputs:
    registries/annotations/trustfund_entries.parquet
    registries/entities/bands_census.parquet

Outputs:
    registries/crosswalks/trustfund_bands.csv         one row per distinct name
    registries/crosswalks/trustfund_bands_review.csv  ambiguous / below-gate

Design notes:
  * The 1893-97 lists print a trust-fund account number "(No. N)" that is
    stable across editions and spellings (No. 1 is Batchewana in all three
    spellings), so linked names propagate their band to same-numbered
    unlinked names.  The 1882-89 ledger era sometimes prints "in Account
    No. N" -- a DIFFERENT numbering (Shawanaga is "Account No. 33" in the
    ledgers but "(No. 34)" in the lists, where No. 33 is Six Nations), so
    only parenthesized numbers take part in consolidation.
  * Collective annuity funds ("Ojibbewas of Lake Huron", the Robinson
    treaty funds; "Stony Indians, Bands Nos. 142-3-4") cover many bands;
    they are flagged 'collective' and routed to review, never auto-linked
    to a single band.
  * Name scoring reuses sim_band/band_variants from link_bands_modern --
    the symmetric Dice + length-ratio score built for short band names.
  * A printed province abbreviation (", Ont.", ", B.C.") is soft evidence:
    agreement is a small bonus, contradiction demotes an otherwise-accepted
    link to review.  N.W.T. accounts may match bands the census registry
    carries under Saskatchewan/Alberta/Assiniboia after the 1905 split.
"""

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from link_bands_modern import band_variants, norm_band, sim_band

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- name parsing

# "in Account (Current) with the Department of Indian Affairs. No. 48." /
# "in Account No. 33" -- ledger-era boilerplate, occasionally carrying the
# untrusted ledger account number.
ACCT_PHRASE = re.compile(
    r",?\s*in\s+Account\s*(?:Current)?\s*"
    r"(?:with\s+the\s+Department\s+of\s+Indian\s+Affairs[.,]?\s*(?:No\.?\s*(?P<n1>\d+[A-Za-z]?))?"
    r"|No\.?\s*(?P<n2>\d+[A-Za-z]?))\s*\.?\s*$",
    re.I,
)
# trailing "(No. 228)" / "(No 172.)" / "(No. 97*.)" -- the trusted list-era number
PAREN_NO = re.compile(r"\(\s*No\.?\s*(?P<no>\d+[A-Za-z]?)\s*\*?\s*\.?\s*\)\s*[*.]?\s*$", re.I)
# single-letter parenthetical annotations: "(No. 209)(t)", "No. 188(t)"
LETTER_PAREN = re.compile(r"\(\s*[a-z]\s*\)", re.I)
# ", Crooked Lake Agency" qualifier -- an agency, never part of the band name
AGENCY_QUAL = re.compile(r",\s*[^,]*\bAgency\b\.?", re.I)
POSSESSIVE = re.compile(r"(\w)'s\b")  # \w, not [A-Za-z]: "Côté's"
# "Reserve No. 44, Treaty No. 2" qualifiers on list-era names
RES_TREATY_QUAL = re.compile(
    r",?\s*(?:in\s+)?(?:Reserve\s*,?\s*No\.?\s*\d+[A-Za-z]?|Treaty\s+No\.?\s*\d+)", re.I
)
# a band number embedded in the name itself ("Hungry Hall No. 1", "Reserve 80")
EMBED_NO = re.compile(r"\b(?:No\.?|Reserve)\s*(\d+[A-Za-z]?)\b", re.I)
# suffix qualifiers on band accounts: "- INTEREST", "Land Fund", "- Con."
SUFFIX = re.compile(
    r"\s*[-,]?\s*(INTEREST|Land\s+Fund|Con(?:cluded|tinued)?\.?)\s*[-.]?\s*$", re.I
)
PROV_TOKEN = re.compile(
    r",?\s*\b(Ont|Que|P\.?\s?Q|B\.?\s?C|N\.?\s?B|N\.?\s?S|P\.?E\.?I|Man|Manitoba|N\.?\s?W\.?\s?T)\b\.?\s*[,.]?",
    re.I,
)
PROV_CANON = {
    "ont": "ontario", "que": "quebec", "pq": "quebec",
    "bc": "british columbia", "nb": "new brunswick", "ns": "nova scotia",
    "pei": "prince edward island", "man": "manitoba", "manitoba": "manitoba",
    "nwt": "north-west territories",
}
# census `province` strings a hint is compatible with (lowercased substring test)
PROV_OK = {
    "ontario": ["ontario"],
    "quebec": ["quebec"],
    "british columbia": ["british columbia", "b.c"],
    "new brunswick": ["new brunswick"],
    "nova scotia": ["nova scotia"],
    "prince edward island": ["prince edward"],
    "manitoba": ["manitoba"],
    # ledger-era N.W.T. bands sit under the post-1905 provinces in the census
    "north-west territories": [
        "territor", "north-west", "saskatchewan", "alberta", "assiniboia",
        "manitoba", "treaty",
    ],
}

DEPT = re.compile(
    r"^\s*(indian\s+(land\s+management|school|trust)\s+fund"
    r"|province\s+of\s+\w+(\s+indian)?\s+fund"
    r"|salaries|missionaries.?\s+salaries|sundry\s+disbursements?"
    r"|grants?(\s+for\s+seed\s+grain)?|annual\s+grants?\s+in\s+aid"
    r"|fuel\s+for\s+schools|inspection\s+of\s+schools|institutes"
    r"|books,?\s+maps|survey\s+account|suspense|interest|advertising"
    r"|schools?)\b",
    re.I,
)
COLLECTIVE = re.compile(
    r"(ojib?bewas|chippewas)\s+of\s+lake\s+(huron|superior)|bands?\s+nos", re.I
)
PERSON = re.compile(r"^\s*(heirs\s+of|estate\s+of)\b", re.I)


def dehyph_norm(name):
    """Identity key that closes hyphens and drops embedded numbers, so
    "Sa-ki-may" == "Sakimay" but "Hungry Hall No. 1" != "... No. 2"."""
    m = EMBED_NO.search(str(name))
    return norm_band(str(name).replace("-", "")), (m.group(1).upper() if m else "")


def parse_account(raw):
    """(clean_name, account_no, ledger_no, province_hint, kind)"""
    s = str(raw).strip()
    if not s or s == "(unnamed)":
        return "", "", "", "", "unnamed"
    no = ledger_no = prov = ""
    s = LETTER_PAREN.sub(" ", s).strip()
    m = ACCT_PHRASE.search(s)
    if m:
        ledger_no = (m.group("n1") or m.group("n2") or "").upper()
        s = s[: m.start()].strip(" ,.")
    m = PAREN_NO.search(s)
    if m:
        no = m.group("no").upper()
        s = s[: m.start()].strip(" ,.")
    for _ in range(2):  # "Iroquois of St. Regis, Que., Land Fund" needs both passes
        s = SUFFIX.sub("", s).strip(" ,.")
    s = AGENCY_QUAL.sub("", s).strip(" ,.")
    m = PROV_TOKEN.search(s)
    if m:
        key = re.sub(r"[^a-z]", "", m.group(1).lower())
        prov = PROV_CANON.get(key, "")
        s = (s[: m.start()] + " " + s[m.end():]).strip(" ,.")
    s = re.sub(r"\s+", " ", s).strip(" ,.*")
    if DEPT.search(s):
        kind = "department"
    elif COLLECTIVE.search(str(raw)):
        kind = "collective"
    elif PERSON.search(s):
        kind = "person"
    else:
        kind = "band"
    return s, no, ledger_no, prov, kind


def account_variants(clean):
    """Score both the full cleaned name and its head with descriptors stripped.

    "Indians of Ohiat Reserve" and "Ohiat Band" must both reach "Ohiat";
    norm_band already drops band/reserve/nation, band_variants splits
    parentheticals and closes hyphens.  "Enoch's Band" needs the possessive
    closed (else the stray "s" token depresses the score), and
    "Muscowpetung's Reserve 80" a digit-free reading.  Variants under three
    characters are noise -- a stray "t" in one census name_variants list
    scored 1.0 against every "(t)"-annotated account.
    """
    out = set()
    unq = RES_TREATY_QUAL.sub(" ", clean)
    for base in {clean, POSSESSIVE.sub(r"\1", clean),
                 unq, POSSESSIVE.sub(r"\1", unq)}:
        out |= set(band_variants(base))
        head = re.sub(r"^\s*indians\s+of\s+(the\s+)?", "", base, flags=re.I)
        head = re.sub(r"\bindians?\b", " ", head, flags=re.I)
        out |= band_variants(head)
    for v in {re.sub(r"\s*\d+[a-z]?\b", "", v).strip() for v in out}:
        out.add(v)
    return {v for v in out if len(v) >= 3}


# ---------------------------------------------------------------- matching


def prov_relation(hint, band_prov):
    if not hint or not isinstance(band_prov, str) or not band_prov.strip():
        return "unknown"
    bp = band_prov.lower()
    return "ok" if any(t in bp for t in PROV_OK[hint]) else "conflict"


def load_bands():
    b = pd.read_parquet(ROOT / "registries/entities/bands_census.parquet")
    cand = []
    for r in b.itertuples():
        primary = {v for v in band_variants(r.name) if len(v) >= 3}
        variants = set(primary)
        for v in str(r.name_variants).split(" | "):
            variants |= {x for x in band_variants(v) if len(x) >= 3}
        if not variants:
            continue
        m = EMBED_NO.search(str(r.name))
        emb_no = m.group(1).upper() if m else ""
        cand.append((r.band_id, r.name, r.province, variants, primary,
                     emb_no, int(r.n_rows)))
    return cand


def best_matches(acct_vars, acct_no, hint, cand, topn=3):
    """acct_no here is the number embedded in the band NAME ("Hungry Hall
    No. 1"), not the trust-fund account number.  Where both sides print one
    and they differ, the candidate is wrong no matter how the words score --
    the schools registry learned this the hard way."""
    scored = []
    for band_id, name, prov, variants, primary, emb_no, n_rows in cand:
        if acct_no and emb_no and acct_no != emb_no:
            continue
        s = max(sim_band(a, v) for a in acct_vars for v in variants)
        if s <= 0.4:
            continue
        rel = prov_relation(hint, prov)
        adj = s + (0.03 if rel == "ok" else -0.10 if rel == "conflict" else 0.0)
        # agreeing embedded numbers are strong evidence: "Shoal Lake Reserve
        # 39" belongs to "Shoal Lake No. 39", not "Shoal Lake (Crees)"
        if acct_no and emb_no and acct_no == emb_no:
            adj += 0.06
        # an exact hit on the band's own printed name outranks a hit on an
        # attestation variant (which can be another band's mis-clustered row:
        # Walpole Island's variants contain "Chippewas of Beausoleil")
        if acct_vars & primary:
            adj += 0.05
        # the census minter left agency-keyed pseudo-identities ("Muscowpetung
        # Agency", "Cowichan Agency") in the registry; a trust-fund account is
        # a band account, so they must lose ties against the band itself
        if re.search(r"\bagency\b", name, re.I):
            adj -= 0.05
        scored.append((adj, s, band_id, name, prov, rel, n_rows))
    scored.sort(key=lambda t: (t[0], t[6]), reverse=True)
    return scored[:topn]


def emit_observations(xw):
    """Band-keyed trust-fund balance series into observations.parquet.

    Only balance lines from accounts that BALANCE (Dr == Cr, the parser's
    verdict) and whose account is band-linked are published.  The balance at
    30 June of year Y is often printed twice -- as the closing line of the
    return for Y and the opening line of the return for Y+1 -- so lines are
    deduplicated per (band, fund, date) preferring the closing line, then
    summed over a band's funds (St. Regis holds both a main account and a
    Land Fund).  The write replaces only source_family == 'trustfund', the
    same idempotent pattern apply_column_maps.py uses.
    """
    bal = pd.read_parquet(ROOT / "registries/annotations/trustfund_balances.parquet")
    m = bal.merge(
        xw[["account", "band_id", "status", "account_no", "clean_name"]],
        on="account", how="inner",
    )
    m = m[(m.balance_status == "balanced")
          & m.status.isin(["linked", "linked_by_number"])].copy()
    m["date"] = m.stated_year.where(
        m.stated_year.notna(), m.year.where(m.kind == "closing", m.year - 1)
    ).astype(int)
    m = m[(m.date >= 1875) & (m.date <= 1905)]  # OCR-mangled printed years
    m["fund_key"] = m.account_no.where(m.account_no != "", m.clean_name.map(norm_band))
    m = (m.sort_values("kind")  # 'closing' precedes 'opening'
          .drop_duplicates(["band_id", "fund_key", "date"]))

    sess = pd.read_csv(ROOT / "registries/crosswalks/dia_sessional.csv")
    paper_of = {int(r.report_year): r.paper_id for r in sess.itertuples()
                if pd.notna(r.report_year)}
    rows = []
    for (band, date), g in m.groupby(["band_id", "date"]):
        for sid, col in (("trustfund_capital", "capital"),
                         ("trustfund_interest", "interest")):
            v = g[col].dropna()
            if not len(v):
                continue
            rows.append(dict(
                entity_id=band, entity_type="band", series_id=sid,
                year=int(date), value=float(v.sum()), unit="dollars",
                source_family="trustfund",
                paper_id=paper_of.get(int(g.year.max()), ""),
                page=g.page.iloc[0], chunk=g.chunk.iloc[0], row_idx=-1,
                confidence="high",
            ))
    df = pd.DataFrame(rows)
    obs_path = ROOT / "registries/annotations/observations.parquet"
    if obs_path.exists():
        old = pd.read_parquet(obs_path)
        df = pd.concat([old[old.source_family != "trustfund"], df],
                       ignore_index=True)
    df.to_parquet(obs_path, index=False)
    mine = df[df.source_family == "trustfund"]
    print(f"\ntrust-fund observations: {len(mine):,} rows, "
          f"{mine.entity_id.nunique()} bands, {mine.year.min()}-{mine.year.max()} "
          f"({len(df):,} rows across all families)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", type=float, default=0.85)
    ap.add_argument("--floor", type=float, default=0.65)
    args = ap.parse_args()

    e = pd.read_parquet(ROOT / "registries/annotations/trustfund_entries.parquet")
    names = (
        e.groupby("account")
        .agg(
            years=("year", lambda y: f"{y.min()}-{y.max()}"),
            n_account_years=("account_id", "nunique"),
            n_entries=("account_id", "size"),
        )
        .reset_index()
    )
    cand = load_bands()
    print(f"{len(names)} distinct account names, {len(cand)} candidate bands")

    rows = []
    for r in names.itertuples():
        clean, no, ledger_no, prov, kind = parse_account(r.account)
        row = dict(
            account=r.account, years=r.years, n_account_years=r.n_account_years,
            n_entries=r.n_entries, kind=kind, clean_name=clean, account_no=no,
            ledger_no=ledger_no, province_hint=prov, band_id="", band_name="",
            band_province="", sim=0.0, status="", candidates="", note="",
        )
        if kind in ("department", "unnamed", "person"):
            row["status"] = kind
            rows.append(row)
            continue
        acct_vars = account_variants(clean)
        if not acct_vars:
            row["status"] = "unparseable"
            rows.append(row)
            continue
        m = EMBED_NO.search(clean)
        emb = m.group(1).upper() if m else ""
        top = best_matches(acct_vars, emb, prov, cand)
        if top:
            adj, s, band_id, name, bprov, rel, _ = top[0]
            row["candidates"] = "; ".join(
                f"{b}={n} ({p}) {sc:.2f}" for _, sc, b, n, p, _, _ in top
            )
            runner = next(
                (t for t in top[1:] if dehyph_norm(t[3]) != dehyph_norm(name)), None
            )
            margin = adj - runner[0] if runner else 1.0
            row.update(band_id=band_id, band_name=name, band_province=bprov,
                       sim=round(s, 3))
            if kind == "collective":
                row["status"] = "review_collective"
            elif s >= args.accept and rel == "conflict":
                row["status"] = "review_province"
            elif s >= args.accept and margin < 0.05:
                row["status"] = "review_ambiguous"
            elif s >= args.accept:
                row["status"] = "linked"
            elif s >= args.floor:
                row["status"] = "review_below_gate"
            else:
                row.update(band_id="", band_name="", band_province="",
                           status="unlinked")
        else:
            row["status"] = "review_collective" if kind == "collective" else "unlinked"
        rows.append(row)

    out = pd.DataFrame(rows)

    # ---- consolidate on the trusted list-era account number ------------------
    # Two linked spellings of one numbered account that reach *different*
    # census identities usually reach same-name duplicates the registry has
    # not merged yet (Eagle Lake under both Manitoba and Ontario -- the
    # Treaty 3 reporting move, see band_registry_merge_review.csv).  That is
    # not a linking conflict: settle the group on the best-attested identity
    # and record the alternates.  Only different-NAME disagreement is real.
    nrows = {c[0]: c[6] for c in cand}
    for no, grp in out[out.account_no != ""].groupby("account_no"):
        linked = (
            grp[grp.status == "linked"]
            .assign(_nr=lambda d: d.band_id.map(nrows))
            .sort_values(["sim", "_nr"], ascending=False)
        )
        if not len(linked):
            continue
        keys = {dehyph_norm(n) for n in linked.band_name}
        if len(keys) > 1:
            # spelling-level duplicates the census clustering kept apart
            # (Sampson/Samson, Munceys/Munsees) settle on the best-attested;
            # different NAMES (Enoch vs Paspaschase) go to review
            norms = [k[0] for k in keys]
            if min(
                sim_band(a, b) for i, a in enumerate(norms) for b in norms[i + 1:]
            ) < 0.7:
                out.loc[grp.index, "status"] = "review_number_conflict"
                out.loc[grp.index, "note"] = (
                    f"No. {no} links to different band names: "
                    + "; ".join(sorted(linked.band_name.unique()))
                )
                continue
        w = linked.iloc[0]
        alts = sorted(set(linked.band_id) - {w.band_id})
        todo = grp[grp.status.isin(["unlinked", "review_below_gate",
                                    "review_ambiguous"])]
        fix = grp[(grp.status == "linked") & (grp.band_id != w.band_id)]
        for idx, why in [(todo.index, f"propagated from '{w.account}' via No. {no}"),
                         (fix.index, f"settled on best-attested duplicate via No. {no}")]:
            if len(idx):
                out.loc[idx, ["band_id", "band_name", "band_province"]] = [
                    w.band_id, w.band_name, w.band_province
                ]
                out.loc[idx, "note"] = why
        if len(todo):
            out.loc[todo.index, "status"] = "linked_by_number"
        if alts:
            note = "duplicate census identities: " + ",".join(alts)
            has = out.loc[grp.index, "note"].str.len() > 0
            out.loc[grp.index[has], "note"] += "; " + note
            out.loc[grp.index[~has], "note"] = note

    xw = ROOT / "registries/crosswalks/trustfund_bands.csv"
    out.to_csv(xw, index=False, quoting=csv.QUOTE_MINIMAL)
    review = out[out.status.str.startswith("review")]
    rv = ROOT / "registries/crosswalks/trustfund_bands_review.csv"
    review.to_csv(rv, index=False, quoting=csv.QUOTE_MINIMAL)

    emit_observations(out)

    print(out.status.value_counts().to_string())
    ok = out[out.status.isin(["linked", "linked_by_number"])]
    ay = ok.n_account_years.sum()
    print(f"\nlinked names: {len(ok)}/{len(out)}; "
          f"account-years covered: {ay}/{out.n_account_years.sum()}; "
          f"distinct bands: {ok.band_id.nunique()}")
    print(f"wrote {xw.name} ({len(out)}) and {rv.name} ({len(review)})")


if __name__ == "__main__":
    main()
