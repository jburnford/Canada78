#!/usr/bin/env python3
"""Phase 0 crosswalk audit: build the initial document crosswalks and registry
seeds from the source corpora, and print audit findings.

Outputs (all under registries/):
  crosswalks/dia_sessional.csv   one row per DIA volume (51), link status
  crosswalks/tn_sessional.csv    one row per T&N volume in uk_trade_db
  documents/documents.parquet    document registry seed (catalog papers + DIA)
  entities/places.parquet        place entity seed (HGIS e53_place_uri)

Read-only over the source corpora. Rerunnable.
"""
import csv
import json
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIA = Path.home() / "DeptIndianAffairs"
CATALOG = Path.home() / "sessional_papers/export/catalog.duckdb"
TN_INDEX = Path.home() / "uk_trade_db/raw_canada/INDEX.tsv"
HGIS_PLACES = (
    Path.home() / "Canada-History-Knowledge-Graph/neo4j_cidoc_crm_v2/e53_place_uri.csv"
)
COL_PERSONS = Path.home() / "col_matching/data/kg/graph_stage3/persons.jsonl"

CATALOG_MAX_SEQ = None  # filled at runtime


def read_frontmatter(md_path: Path) -> dict:
    fm = {}
    with open(md_path, encoding="utf-8") as fh:
        first = fh.readline()
        if first.strip() != "---":
            return fm
        for line in fh:
            if line.strip() == "---":
                break
            m = re.match(r'^(\w+):\s*"?(.*?)"?\s*$', line)
            if m:
                fm[m.group(1)] = m.group(2)
    return fm


def load_catalog():
    con = duckdb.connect(str(CATALOG), read_only=True)
    papers = con.execute(
        "select paper_id, seq, session_year, paper_num, series_id, title from papers"
    ).fetchdf()
    sidx = con.execute(
        "select seq, session_year, paper_num, title from session_index"
    ).fetchdf()
    full_papers = con.execute("select * from papers").fetchdf()
    con.close()
    return papers, sidx, full_papers


def dia_crosswalk(papers, sidx):
    links_file = DIA / "structured/sessional_links.json"
    links = {}
    if links_file.exists():
        data = json.loads(links_file.read_text())
        for row in data.get("links", []):
            links[row["tag"]] = row

    overrides_file = ROOT / "curation/dia_sessional_overrides.csv"
    overrides = {}
    if overrides_file.exists():
        with open(overrides_file, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                overrides[row["tag"]] = row

    paper_ids = set(papers.paper_id)
    sidx_keys = set(zip(sidx.seq.astype(int), sidx.paper_num.astype(str)))

    rows = []
    for md in sorted((DIA / "markdown").glob("dia_ar_*.md")):
        fm = read_frontmatter(md)
        tag = fm.get("tag", md.stem)
        report_year = int(fm.get("report_year", md.stem.split("_")[-1]))
        link = links.get(tag)
        override = overrides.get(tag)
        seq = paper_num = session_year = paper_id = None
        if link is None and override:
            link = dict(
                session_seq=override["session_seq"],
                paper_number=override["paper_num"],
                session_year=override["session_year"],
            )
        if link:
            seq = int(link["session_seq"])
            paper_num = str(link["paper_number"])
            session_year = link["session_year"]
            paper_id = f"{session_year}_{paper_num}"
            if paper_id in paper_ids:
                status = "linked_extracted"
            elif (seq, paper_num) in sidx_keys:
                status = "linked_indexed"  # in session index, text not yet in catalog
                paper_id = f"{session_year}_{paper_num}"
            else:
                status = "linked_unverified"
            if override and tag not in links:
                status = status.replace("linked_", "curated_")
        else:
            # publication is normally the session after the report year
            pub_year = int(fm.get("publication_year", report_year + 1))
            status = (
                "pending_catalog"  # session should exist but no link found
                if pub_year <= 1900
                else "pending_catalog_post1900"  # catalog does not reach this session yet
            )
        rows.append(
            dict(
                tag=tag,
                report_year=report_year,
                session_seq=seq,
                session_year=session_year,
                paper_num=paper_num,
                paper_id=paper_id,
                link_status=status,
                fm_paper_number=fm.get("sessional_paper_number"),
                fm_session=fm.get("sessional_paper_session"),
            )
        )
    return pd.DataFrame(rows)


def tn_crosswalk(papers, sidx):
    tn = pd.read_csv(TN_INDEX, sep="\t")
    tn_idx = sidx[
        sidx.title.str.match(r"(\*\*—)?Tables of the Trade and Navigation", na=False)
    ].copy()
    paper_ids = set(papers.paper_id)
    rows = []
    for _, r in tn.iterrows():
        m = re.match(r"oocihm\.9_08052_(\d+)_", str(r.volume_tag) + "_")
        seq = int(m.group(1)) if m else None
        paper_num = session_year = paper_id = None
        status = "no_catalog_session"
        if seq is not None:
            hit = tn_idx[tn_idx.seq == seq]
            if len(hit) == 1:
                paper_num = str(hit.iloc[0].paper_num)
                session_year = hit.iloc[0].session_year
                paper_id = f"{session_year}_{paper_num}"
                status = (
                    "linked_extracted" if paper_id in paper_ids else "linked_indexed"
                )
            elif len(hit) > 1:
                status = "ambiguous_index"
            elif seq <= int(sidx.seq.max()):
                status = "session_indexed_no_tn_entry"
        rows.append(
            dict(
                fiscal_year=r.fiscal_year,
                volume_tag=r.volume_tag,
                session_seq=seq,
                session_year=session_year,
                paper_num=paper_num,
                paper_id=paper_id,
                link_status=status,
            )
        )
    # T&N papers indexed in the catalog but NOT yet in uk_trade_db raw_canada
    have_seq = set(tn.volume_tag.str.extract(r"oocihm\.9_08052_(\d+)_")[0].dropna().astype(int))
    missing = tn_idx[~tn_idx.seq.isin(have_seq)]
    return pd.DataFrame(rows), missing


def person_probe():
    """Weak-evidence probe: DIA agent surnames vs Colonial Office List surnames."""
    agent_pat = re.compile(
        r"([A-Z][A-Za-z.'\- ]{2,40}?),\s*(?:Indian\s+)?Agent\b"
    )
    agents = set()
    for year in (1885, 1895, 1905):
        md = DIA / f"markdown/dia_ar_{year}.md"
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in agent_pat.finditer(text):
            name = m.group(1).strip()
            surname = name.split()[-1].strip(".,'-")
            if len(surname) > 2 and surname[0].isupper() and surname.isalpha():
                agents.add(surname.upper())
    col_surnames = set()
    with open(COL_PERSONS, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            s = d.get("surname")
            if s:
                col_surnames.add(s.upper())
    overlap = agents & col_surnames
    return agents, col_surnames, overlap


def build_document_registry(full_papers, dia_xw):
    docs = full_papers.rename(columns={"paper_id": "doc_id"}).copy()
    docs["doc_kind"] = "sessional_paper"
    docs["id_status"] = "catalog"
    docs["corpus"] = "sessional_papers"

    dia_rows = []
    for _, r in dia_xw.iterrows():
        linked = r.link_status in ("linked_extracted", "linked_indexed")
        dia_rows.append(
            dict(
                doc_id=r.paper_id if linked else f"prov:{r.tag}",
                doc_kind="sessional_paper",
                id_status="catalog_ref" if linked else "provisional",
                corpus="dia_annual_reports",
                seq=r.session_seq,
                session_year=r.session_year,
                paper_num=r.paper_num,
                series_id="annual_report:indian_affairs",
                title=f"Annual Report of the Department of Indian Affairs, {r.report_year}",
            )
        )
    dia_df = pd.DataFrame(dia_rows)
    out = pd.concat([docs, dia_df], ignore_index=True)
    return out


def main():
    (ROOT / "registries/crosswalks").mkdir(parents=True, exist_ok=True)
    (ROOT / "registries/documents").mkdir(parents=True, exist_ok=True)
    (ROOT / "registries/entities").mkdir(parents=True, exist_ok=True)

    papers, sidx, full_papers = load_catalog()
    print(f"catalog: {len(full_papers)} papers, sessions seq 1–{int(sidx.seq.max())} "
          f"({sidx.session_year.min()}–{sidx.session_year.max()})")

    dia = dia_crosswalk(papers, sidx)
    dia.to_csv(ROOT / "registries/crosswalks/dia_sessional.csv", index=False)
    print("\nDIA crosswalk status counts:")
    print(dia.link_status.value_counts().to_string())

    tn, tn_missing = tn_crosswalk(papers, sidx)
    tn.to_csv(ROOT / "registries/crosswalks/tn_sessional.csv", index=False)
    print("\nT&N crosswalk status counts:")
    print(tn.link_status.value_counts().to_string())
    print(f"\nT&N papers indexed in catalog but absent from uk_trade_db/raw_canada "
          f"({len(tn_missing)}):")
    print(tn_missing[["seq", "session_year", "paper_num"]].to_string(index=False))

    agents, col_surnames, overlap = person_probe()
    print(f"\nPerson probe: {len(agents)} DIA agent surnames (1885/1895/1905) vs "
          f"{len(col_surnames)} COL surnames -> {len(overlap)} surname-level hits "
          f"(weak evidence; sample: {sorted(overlap)[:10]})")

    docs = build_document_registry(full_papers, dia)
    docs.to_parquet(ROOT / "registries/documents/documents.parquet", index=False)
    print(f"\ndocument registry seed: {len(docs)} rows "
          f"({(docs.id_status == 'provisional').sum()} provisional)")

    places = pd.read_csv(HGIS_PLACES)
    places = places.rename(columns={"place_id:ID": "place_id"})
    places["source"] = "hgiscanada"
    places.to_parquet(ROOT / "registries/entities/places.parquet", index=False)
    print(f"place entity seed: {len(places)} rows "
          f"({(places.grounding_status == 'matched').sum()} wikidata-matched)")


if __name__ == "__main__":
    sys.exit(main())
