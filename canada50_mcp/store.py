"""Query layer for canada50-mcp — the retrieval tools, plain Python.

Reads the SQLite index built by `python3 -m canada50_mcp.index` and the
generated wiki under site/. No LLM, no embeddings: lexical search (FTS5
BM25), alias lookup (exact → prefix → substring → trigram), graph edges with
years, and document/segment addressing.

Entity references accepted everywhere a `ref` is taken:
  - canonical URI   https://jimclifford.ca/canada50/agencies/blackfoot-agency
                    http://id.lincsproject.ca/rfx8yZUrjkh
  - page URL/path   /agencies/blackfoot-agency/   (as returned by lookup/search)
  - typed id        agency:AG-blackfoot-agency, reserve:blackfoot-146,
                    band:BAND-blackfoot, band:band_c02877, person:PERSON-magnus-begg,
                    school:SCH-00421
  - bare id         band_c02877, AG-file-hills-agency, SCH-00421
  - a name          "File Hills Agency" (best alias match, exact first)
Superseded ids (a census band merged into another, a minted person
re-anchored to LINCS) follow the `redirects` table transparently.
"""
import html
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITE = Path(os.environ.get("CANADA50_SITE", ROOT / "site"))
DEFAULT_DB = Path(os.environ.get("CANADA50_DB", DEFAULT_SITE / "_data/canada50.sqlite"))

TYPES = {"agency", "reserve", "band", "person", "school"}
EDGE_TYPES = {"OCCUPIES", "ADMINISTERED_BY", "IN_AGENCY", "ON_RESERVE", "MERGED_INTO",
              "SPLIT_INTO", "RENAMED", "SIGNED_FOR", "REPORTED_ON", "REPORTED_IN",
              "TAUGHT_AT", "POSTED_TO", "SUCCEEDED_BY", "MENTIONED_IN"}
EDGE_LABELS = {
    "OCCUPIES": "occupies reserve", "ADMINISTERED_BY": "administered by",
    "IN_AGENCY": "in agency", "ON_RESERVE": "on reserve", "MERGED_INTO": "merged into",
    "SPLIT_INTO": "split into", "RENAMED": "renamed", "SIGNED_FOR": "signed report for",
    "REPORTED_ON": "reported on", "REPORTED_IN": "reported in", "TAUGHT_AT": "taught at",
    "POSTED_TO": "posted to (LINCS)", "SUCCEEDED_BY": "succeeded by (modern)"}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def year_ranges(years):
    """[1894, 1895, 1896, 1899] → '1894–1896, 1899'."""
    ys = sorted({int(y) for y in years if y is not None})
    if not ys:
        return ""
    out, start, prev = [], ys[0], ys[0]
    for y in ys[1:]:
        if y == prev + 1:
            prev = y
            continue
        out.append(f"{start}–{prev}" if start != prev else str(start))
        start = prev = y
    out.append(f"{start}–{prev}" if start != prev else str(start))
    return ", ".join(out)


def html_to_text(page):
    """Wiki HTML → readable text; links kept as [label](url) so agents can follow them."""
    page = re.sub(r"<style>.*?</style>|<footer>.*?</footer>", "", page, flags=re.S)
    page = re.sub(r"<a [^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
                  lambda m: f"[{re.sub('<[^>]+>', '', m.group(2))}]({m.group(1)})", page, flags=re.S)
    page = re.sub(r"<(h[1-6])[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)[1]) + " ", page)
    page = re.sub(r"</(h[1-6]|p|li|tr|dd|div|details|pre)>", "\n", page)
    page = re.sub(r"<(li)[^>]*>", "- ", page)
    page = re.sub(r"<dt[^>]*>", "\n", page)
    page = re.sub(r"</dt>", ": ", page)
    page = re.sub(r"</t[hd]>", " | ", page)
    page = re.sub(r"<span class=pg[^>]*>", "\n", page)
    page = re.sub(r"<[^>]+>", "", page)
    page = html.unescape(page)
    page = re.sub(r"[ \t]+", " ", page)
    page = re.sub(r"\n\s*\n\s*\n+", "\n\n", page)
    return page.strip()


class Store:
    def __init__(self, db_path=None, site_dir=None):
        self.db_path = Path(db_path or DEFAULT_DB)
        self.site = Path(site_dir or DEFAULT_SITE)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"{self.db_path} not found — run `python3 build/gen_wiki.py` then "
                f"`python3 -m canada50_mcp.index`, or set CANADA50_DB / CANADA50_SITE")
        self.con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    # ------------------------------------------------------------------ helpers
    def _rows(self, sql, args=()):
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def _canon(self, etype, eid):
        seen = set()
        while eid not in seen:
            seen.add(eid)
            r = self.con.execute("SELECT canonical_id FROM redirects WHERE type=? AND id=?",
                                 (etype, eid)).fetchone()
            if not r:
                break
            eid = r[0]
        return eid

    def _entity(self, etype, eid):
        eid = self._canon(etype, eid)
        r = self.con.execute("SELECT * FROM entities WHERE type=? AND id=?", (etype, eid)).fetchone()
        return dict(r) if r else None

    def resolve(self, ref, type=None):
        """Resolve any accepted reference form to an entity row (or None)."""
        ref = str(ref).strip()
        m = re.match(r"^(agency|reserve|band|person|agent|school):(.+)$", ref)
        if m:
            t = "person" if m.group(1) == "agent" else m.group(1)
            return self._entity(t, m.group(2))
        r = self.con.execute("SELECT * FROM entities WHERE uri=? OR url=?", (ref, ref)).fetchone()
        if r:
            return dict(r)
        path = re.sub(r"^https?://[^/]+", "", ref)
        path = "/" + path.strip("/") + "/"
        r = self.con.execute("SELECT * FROM entities WHERE url=?", (path,)).fetchone()
        if r:
            return dict(r)
        for t in ([type] if type in TYPES else sorted(TYPES)):
            e = self._entity(t, ref)
            if e:
                return e
        # a name: best alias match, exact aliases only
        q = norm(ref)
        if q:
            tf = " AND type=?" if type in TYPES else ""
            rows = self._rows(f"SELECT type,id,sum(n) n FROM aliases WHERE alias=?{tf} "
                              "GROUP BY type,id ORDER BY n DESC LIMIT 2", [q] + ([type] if type in TYPES else []))
            if rows:
                return self._entity(rows[0]["type"], rows[0]["id"])
        return None

    def _brief(self, e):
        d = {k: e.get(k) for k in ("type", "id", "name", "uri", "url", "region", "qid", "kind",
                                   "n_mentions", "n_years")}
        if e.get("first_year"):
            d["attested"] = (f"{e['first_year']}–{e['last_year']}" if e.get("last_year")
                             and e["last_year"] != e["first_year"] else str(e["first_year"]))
        return d

    # ------------------------------------------------------------------ tools
    def lookup_entity(self, name, type=None, limit=10):
        """Registry + alias search. Ranked: exact alias, prefix/substring alias,
        token overlap, then trigram similarity (OCR/spelling variants)."""
        q = norm(name)
        if not q:
            return []
        if type == "agent":
            type = "person"
        tfilter = " AND a.type=?" if type in TYPES else ""
        targs = [type] if type in TYPES else []
        seen, out = {}, []

        def add(rows, score):
            for r in rows:
                key = (r["type"], r["id"])
                if key in seen:
                    seen[key]["score"] = max(seen[key]["score"], score + min(r["n"], 50) / 100)
                    seen[key]["matched"].add(r["alias"])
                    continue
                e = self._entity(r["type"], r["id"])
                if not e:
                    continue
                d = self._brief(e)
                d.update(score=score + min(r["n"], 50) / 100, matched={r["alias"]}, summary=e["summary"])
                seen[key] = d
                out.append(d)

        add(self._rows(f"SELECT alias,type,id,sum(n) n FROM aliases a WHERE alias=?{tfilter} "
                       "GROUP BY type,id", [q] + targs), 3)
        add(self._rows(f"SELECT alias,type,id,sum(n) n FROM aliases a WHERE alias LIKE ?{tfilter} "
                       "GROUP BY type,id ORDER BY n DESC LIMIT 200", [q + "%"] + targs), 2)
        add(self._rows(f"SELECT alias,type,id,sum(n) n FROM aliases a WHERE alias LIKE ?{tfilter} "
                       "GROUP BY type,id ORDER BY n DESC LIMIT 200", ["%" + q + "%"] + targs), 1.5)
        toks = [t for t in q.split() if len(t) > 2]
        if toks and len(out) < limit:
            cond = " AND ".join("alias LIKE ?" for _ in toks)
            add(self._rows(f"SELECT alias,type,id,sum(n) n FROM aliases a WHERE {cond}{tfilter} "
                           "GROUP BY type,id ORDER BY n DESC LIMIT 200",
                           [f"%{t}%" for t in toks] + targs), 1)
        sq = q.replace(" ", "")
        if len(sq) >= 3 and len(out) < limit:
            try:
                fuzzy = self._rows(
                    "SELECT a.alias, a.type, a.id, a.n, bm25(aliases_fts) rank FROM aliases_fts "
                    "JOIN aliases a ON a.rowid = aliases_fts.rowid "
                    f"WHERE aliases_fts MATCH ?{tfilter.replace('a.type', 'a.type')} ORDER BY rank LIMIT 40",
                    [f'"{sq}"' if len(sq) < 6 else " OR ".join(f'"{sq[i:i+5]}"' for i in range(0, len(sq) - 4, 2))] + targs)
                add(fuzzy, 0.5)
            except sqlite3.OperationalError:
                pass
        # ties (same alias class) go to the better-attested identity: mentions,
        # then attested report years (a 28-year census band over a 2-year one)
        out.sort(key=lambda d: (-round(d["score"], 1), -(d["n_mentions"] or 0),
                                -(d.get("n_years") or 0)))
        for d in out:
            d["matched"] = sorted(d["matched"])[:5]
        return out[:limit]

    def open_page(self, ref):
        """Wiki page content as text (entity page, report page, or segment page).
        Entities that have no generated page yet (census bands, agstat units,
        LINCS agents without attestations here) get a text hub composed from
        the index: summary, aliases, relations with years, series."""
        e = self.resolve(ref)
        path = None
        if e and e.get("url"):
            path = e["url"]
        else:
            path = re.sub(r"^https?://[^/]+", "", str(ref))
            path = "/" + path.strip("/") + "/" if path.strip("/") else "/"
        f = self.site / path.strip("/") / "index.html"
        if f.exists():
            text = html_to_text(f.read_text(encoding="utf-8"))
            out = {"url": path, "text": text}
            if e:
                out.update(self._brief(e))
            return out
        if e:
            return self._synth_page(e)
        return {"error": f"no page for {ref!r}", "hint": "use lookup_entity or search first"}

    def _synth_page(self, e):
        lines = [f"# {e['name']}", "",
                 f"{e['type']}" + (f" ({e['kind']})" if e.get("kind") else "")
                 + f" · {e['uri']}" + (f" · Wikidata {e['qid']}" if e.get("qid") else ""),
                 "", e.get("summary") or "", ""]
        al = self._rows("SELECT alias, source, sum(n) n FROM aliases WHERE type=? AND id=? "
                        "GROUP BY alias ORDER BY n DESC LIMIT 25", (e["type"], e["id"]))
        if al:
            lines += ["## Names as printed", ", ".join(f"{a['alias']} ({a['n']})" for a in al), ""]
        nb = self.neighbors(f"{e['type']}:{e['id']}", limit=200)
        if nb.get("out"):
            lines.append("## Relations")
            for r in nb["out"]:
                t = r["target"]
                lines.append(f"- {EDGE_LABELS.get(r['edge'], r['edge'])} {t.get('name')} "
                             f"[{t.get('type')}:{t.get('id')}]" + (f" {r['years']}" if r.get("years") else ""))
            lines.append("")
        if nb.get("in"):
            lines.append("## Referenced by")
            for r in nb["in"]:
                s = r["source"]
                lines.append(f"- {s.get('name')} [{s.get('type')}:{s.get('id')}] "
                             f"{EDGE_LABELS.get(r['edge'], r['edge'])} this"
                             + (f" {r['years']}" if r.get("years") else ""))
            lines.append("")
        ser = self.series(f"{e['type']}:{e['id']}")
        if ser.get("series"):
            lines.append("## Series (use `series(ref, series_id)` for the values)")
            for s in ser["series"]:
                lines.append(f"- {s['series_id']} ({s['source_family']}, {s['first_year']}–{s['last_year']}, {s['n']} points)")
            lines.append("")
        mi = nb.get("mentioned_in", {})
        if mi.get("by_year"):
            lines.append("## Mentioned in the annual reports")
            lines.append("years: " + ", ".join(f"{y['report_year']} ({y['primary_n']})" for y in mi["by_year"]))
            for s in mi.get("top_segments", [])[:15]:
                lines.append(f"- {s['report_year']} {s['heading']} — {s['doc_id']} / {s['segment_id']} (×{s['n']})")
        return {"url": None, "text": "\n".join(lines).strip(), "generated": True, **self._brief(e)}

    def search(self, query, year_from=None, year_to=None, kind=None, agency=None, school=None,
               limit=10):
        """BM25 full-text search over report segments (FTS5 syntax: phrases in
        quotes, AND/OR/NOT, prefix*). Returns addresses + snippets."""
        where, args = [], []
        if year_from:
            where.append("s.report_year>=?"); args.append(int(year_from))
        if year_to:
            where.append("s.report_year<=?"); args.append(int(year_to))
        if kind:
            where.append("s.kind=?"); args.append(kind)
        if agency:
            e = self.resolve(agency, type="agency")
            where.append("s.agency_id=?"); args.append(e["id"] if e else str(agency))
        if school:
            e = self.resolve(school, type="school")
            where.append("s.school_id=?"); args.append(e["id"] if e else str(school))
        sql = ("SELECT s.doc_id, s.segment_id, s.report_year, s.heading, s.kind, s.agency_id, "
               "s.school_id, s.page_start, s.url, bm25(segments_fts, 2.0, 1.0) AS rank, "
               "snippet(segments_fts, 1, '«', '»', '…', 28) AS snippet "
               "FROM segments_fts JOIN segments s ON s.rowid = segments_fts.rowid "
               "WHERE segments_fts MATCH ?" + "".join(" AND " + w for w in where)
               + " ORDER BY rank LIMIT ?")
        try:
            rows = self._rows(sql, [query] + args + [int(limit)])
        except sqlite3.OperationalError:
            safe = " ".join(f'"{t}"' for t in re.findall(r"[\w'’-]+", query))
            rows = self._rows(sql, [safe] + args + [int(limit)]) if safe else []
        for r in rows:
            r["snippet"] = re.sub(r"\s+", " ", r["snippet"])
            r["address"] = f"{r['doc_id']} / {r['segment_id']}"
            if r["agency_id"]:
                a = self._entity("agency", r["agency_id"])
                r["agency"] = a["name"] if a else r["agency_id"]
            if r["school_id"]:
                a = self._entity("school", r["school_id"])
                r["school"] = a["name"] if a else r["school_id"]
        return rows

    def neighbors(self, ref, edge_type=None, year=None, limit=50):
        """Graph edges out of and into an entity, collapsed to one row per
        (edge, other entity) with the attested years as ranges; optionally
        only edges attested in `year` (undated edges always pass). Plus where
        the entity is mentioned (per report year, with the top segments)."""
        e = self.resolve(ref)
        if not e:
            return {"error": f"unknown entity {ref!r}"}
        out = {"entity": self._brief(e), "out": [], "in": [], "mentioned_in": []}
        ef = " AND edge=?" if edge_type else ""
        ea = [edge_type] if edge_type else []
        yf = " AND (year IS NULL OR year=?)" if year else ""
        ya = [int(year)] if year else []

        def collapse(rows, other_side):
            groups = defaultdict(list)
            for r in rows:
                groups[(r["edge"], r[f"{other_side}_type"], r[f"{other_side}_id"])].append(r)
            res = []
            for (edge, ot, oid), rs in groups.items():
                d = self._entity(ot, oid) if ot in TYPES else None
                other = self._brief(d) if d else {
                    "type": ot, "id": oid, "name": rs[0]["note"],
                    "uri": f"http://www.wikidata.org/entity/{oid}" if ot == "wikidata" else None}
                years = [r["year"] for r in rs if r["year"] is not None]
                res.append({"edge": edge, "years": year_ranges(years), "n_years": len(set(years)),
                            "note": rs[0]["note"], "provenance": rs[0]["source"], "_other": other})
            res.sort(key=lambda d: (d["edge"], -d["n_years"], d["_other"].get("name") or ""))
            return res

        o = collapse(self._rows(f"SELECT * FROM edges WHERE src_type=? AND src_id=?{ef}{yf}",
                                [e["type"], e["id"]] + ea + ya), "dst")
        i = collapse(self._rows(f"SELECT * FROM edges WHERE dst_type=? AND dst_id=?{ef}{yf}",
                                [e["type"], e["id"]] + ea + ya), "src")
        out["out_total"], out["in_total"] = len(o), len(i)
        if max(len(o), len(i)) > limit:
            out["note"] = f"truncated to {limit} per direction (totals are counts of distinct relations)"
        for d in o[:limit]:
            d["target"] = d.pop("_other")
            out["out"].append(d)
        for d in i[:limit]:
            d["source"] = d.pop("_other")
            out["in"].append(d)
        if not edge_type or edge_type == "MENTIONED_IN":
            yfm = " AND report_year=?" if year else ""
            years = self._rows(
                "SELECT report_year, sum(confidence IN ('high','medium')) primary_n, count(*) n "
                f"FROM mentions WHERE entity_type=? AND entity_id=?{yfm} GROUP BY report_year ORDER BY report_year",
                [e["type"], e["id"]] + ya)
            top = self._rows(
                "SELECT m.doc_id, m.segment_id, m.report_year, s.heading, s.kind, s.url, count(*) n "
                "FROM mentions m JOIN segments s USING(doc_id, segment_id) "
                f"WHERE m.entity_type=? AND m.entity_id=? AND m.confidence IN ('high','medium'){yfm.replace('report_year', 'm.report_year')} "
                "GROUP BY m.doc_id, m.segment_id ORDER BY n DESC, m.report_year LIMIT ?",
                [e["type"], e["id"]] + ya + [limit])
            out["mentioned_in"] = {"by_year": years, "top_segments": top}
        return out

    def get_document(self, doc_id):
        """Document metadata + table of contents. Accepts a doc_id (`1886_4`,
        `prov:dia_ar_1925`) or a report year (`1885`)."""
        d = self.con.execute("SELECT * FROM documents WHERE doc_id=? OR report_year=? OR tag=?",
                             (str(doc_id), int(doc_id) if str(doc_id).isdigit() else -1,
                              str(doc_id))).fetchone()
        if not d:
            return {"error": f"unknown document {doc_id!r}"}
        d = dict(d)
        d["segments"] = self._rows(
            "SELECT s.segment_id, s.heading, s.kind, s.agency_id, s.school_id, s.attribution, s.letter_kind, "
            "s.page_start, s.page_end, s.url, "
            "(SELECT count(*) FROM mentions m WHERE m.doc_id=s.doc_id AND m.segment_id=s.segment_id "
            " AND m.confidence IN ('high','medium')) n_mentions "
            "FROM segments s WHERE s.doc_id=? ORDER BY s.ordinal", (d["doc_id"],))
        names = {}
        for s in d["segments"]:
            for t, k in (("agency", "agency_id"), ("school", "school_id")):
                if s[k]:
                    if (t, s[k]) not in names:
                        e = self._entity(t, s[k])
                        names[(t, s[k])] = e["name"] if e else s[k]
                    s[t] = names[(t, s[k])]
        return d

    def get_segment(self, doc_id, segment_id, with_mentions=True):
        """Full segment text (page markers as `[p. N]`) with its linked entities."""
        s = self.con.execute("SELECT * FROM segments WHERE (doc_id=? OR report_year=?) AND segment_id=?",
                             (str(doc_id), int(doc_id) if str(doc_id).isdigit() else -1,
                              segment_id)).fetchone()
        if not s:
            return {"error": f"unknown segment {doc_id!r} / {segment_id!r}"}
        s = dict(s)
        s["address"] = f"{s['doc_id']} / {s['segment_id']}"
        for t, k in (("agency", "agency_id"), ("school", "school_id")):
            if s.get(k):
                e = self._entity(t, s[k])
                s[t] = self._brief(e) if e else s[k]
        if with_mentions:
            ms = self._rows(
                "SELECT entity_type, entity_id, count(*) n, "
                "sum(confidence IN ('high','medium')) primary_n, group_concat(DISTINCT surface) surfaces "
                "FROM mentions WHERE doc_id=? AND segment_id=? GROUP BY entity_type, entity_id "
                "ORDER BY primary_n DESC, n DESC", (s["doc_id"], s["segment_id"]))
            ents = []
            for m in ms:
                e = self._entity(m["entity_type"], m["entity_id"])
                ents.append({"type": m["entity_type"], "id": m["entity_id"],
                             "name": e["name"] if e else m["entity_id"],
                             "url": e["url"] if e else None, "n": m["n"],
                             "unverified": m["primary_n"] == 0, "surfaces": m["surfaces"]})
            s["entities"] = ents
        return s

    def series(self, ref, series_id=None, limit=200):
        """Annual series for an entity. Without series_id: what series exist,
        with counts and year spans. With one: the year-value points as
        printed (value, unit, paper_id, page).

        Observations are keyed by the ids the extraction minted (band_cNNNNN,
        AG-/AGT-, SCH-); an entity reaches the observations of every id that
        redirects to it through obs_map."""
        e = self.resolve(ref)
        ids = []
        if e:
            ids.append((e["type"], e["id"]))
            ids += [(r["obs_type"], r["obs_id"]) for r in self._rows(
                "SELECT obs_type, obs_id FROM obs_map WHERE ref_type=? AND ref_id=?",
                (e["type"], e["id"]))]
        else:
            ids = [(t, str(ref).strip()) for t in ("band", "agency", "school")]
        where = " OR ".join(["(entity_type=? AND entity_id=?)"] * len(ids))
        args = [x for pair in ids for x in pair]
        head = {"entity": self._brief(e) if e else str(ref)}
        if series_id is None:
            rows = self._rows(
                f"SELECT series_id, source_family, unit, COUNT(*) n, "
                f"MIN(year) first_year, MAX(year) last_year "
                f"FROM observations WHERE {where} "
                f"GROUP BY series_id ORDER BY source_family, series_id", args)
            if not rows:
                return {**head, "series": [], "note": "no observations for this entity"}
            return {**head, "series": rows}
        rows = self._rows(
            f"SELECT year, value, unit, source_family, paper_id, page "
            f"FROM observations WHERE ({where}) AND series_id=? "
            f"ORDER BY year LIMIT ?", args + [series_id, int(limit)])
        # the citable address: the segment of that report year whose page
        # span contains the printed page (tables first, then any segment)
        cache = {}
        for r in rows:
            key = (r["year"], r["page"])
            if r["page"] is None:
                continue
            if key not in cache:
                seg = self.con.execute(
                    "SELECT doc_id, segment_id, heading FROM segments WHERE report_year=? "
                    "AND page_start<=? AND page_end>=? "
                    "ORDER BY (kind='tabular_statement') DESC, (page_end-page_start) LIMIT 1",
                    (int(r["year"]), int(r["page"]), int(r["page"]))).fetchone()
                cache[key] = dict(seg) if seg else None
            seg = cache[key]
            if seg:
                r["address"] = f"{seg['doc_id']} / {seg['segment_id']}"
                r["segment_heading"] = seg["heading"]
                if not r["paper_id"]:
                    r["paper_id"] = seg["doc_id"]
        return {**head, "series_id": series_id, "points": rows}
