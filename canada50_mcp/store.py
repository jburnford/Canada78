"""Query layer for canada50-mcp — the six retrieval tools, plain Python.

Reads the SQLite index built by `python3 -m canada50_mcp.index` and the
generated wiki under site/. No LLM, no embeddings: lexical search (FTS5
BM25), alias lookup, graph edges, and document/segment addressing.

Entity references accepted everywhere a `ref` is taken:
  - canonical URI   https://jimclifford.ca/canada50/agencies/blackfoot-agency
                    http://id.lincsproject.ca/rfx8yZUrjkh
  - page URL/path   /agencies/blackfoot-agency/   (as returned by lookup/search)
  - typed id        agency:AG-blackfoot-agency, reserve:blackfoot-146,
                    band:BAND-blackfoot, person:PERSON-magnus-begg
"""
import html
import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITE = Path(os.environ.get("CANADA50_SITE", ROOT / "site"))
DEFAULT_DB = Path(os.environ.get("CANADA50_DB", DEFAULT_SITE / "_data/canada50.sqlite"))

TYPES = {"agency", "reserve", "band", "person"}
EDGE_TYPES = {"OCCUPIES", "ADMINISTERED_BY", "MERGED_INTO", "SPLIT_INTO", "SIGNED_FOR",
              "SUCCEEDED_BY", "MENTIONED_IN"}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


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

    def _entity(self, etype, eid):
        r = self.con.execute("SELECT * FROM entities WHERE type=? AND id=?", (etype, eid)).fetchone()
        return dict(r) if r else None

    def resolve(self, ref):
        """Resolve any accepted reference form to an entity row (or None)."""
        ref = str(ref).strip()
        m = re.match(r"^(agency|reserve|band|person|agent):(.+)$", ref)
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
        # bare ids
        for t in TYPES:
            e = self._entity(t, ref)
            if e:
                return e
        return None

    def _brief(self, e):
        return {k: e.get(k) for k in ("type", "id", "name", "uri", "url", "region", "qid", "n_mentions")}

    # ------------------------------------------------------------------ tools
    def lookup_entity(self, name, type=None, limit=10):
        """Registry + alias search. Returns candidates ranked: exact alias,
        prefix/substring alias, then token overlap; each with mention counts."""
        q = norm(name)
        if not q:
            return []
        tfilter = " AND a.type=?" if type else ""
        targs = [type] if type else []
        if type == "agent":
            targs = ["person"]
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
        out.sort(key=lambda d: (-d["score"], -(d["n_mentions"] or 0)))
        for d in out:
            d["matched"] = sorted(d["matched"])[:5]
        return out[:limit]

    def open_page(self, ref):
        """Wiki page content as text (entity page, report page, or segment page)."""
        e = self.resolve(ref)
        path = None
        if e and e.get("url"):
            path = e["url"]
        else:
            path = re.sub(r"^https?://[^/]+", "", str(ref))
            path = "/" + path.strip("/") + "/" if path.strip("/") else "/"
        f = self.site / path.strip("/") / "index.html"
        if not f.exists():
            return {"error": f"no page for {ref!r}", "hint": "use lookup_entity or search first"}
        text = html_to_text(f.read_text(encoding="utf-8"))
        out = {"url": path, "text": text}
        if e:
            out.update(self._brief(e))
        return out

    def search(self, query, year_from=None, year_to=None, kind=None, agency=None, limit=10):
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
            e = self.resolve(agency) if not str(agency).startswith("AG-") else {"id": agency}
            where.append("s.agency_id=?"); args.append(e["id"] if e else agency)
        sql = ("SELECT s.doc_id, s.segment_id, s.report_year, s.heading, s.kind, s.agency_id, "
               "s.page_start, s.url, bm25(segments_fts, 2.0, 1.0) AS rank, "
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
        return rows

    def neighbors(self, ref, edge_type=None, limit=50):
        """Graph edges out of and into an entity, plus where it is mentioned
        (per report year, with the top segments)."""
        e = self.resolve(ref)
        if not e:
            return {"error": f"unknown entity {ref!r}"}
        out = {"entity": self._brief(e), "out": [], "in": [], "mentioned_in": []}
        ef = " AND edge=?" if edge_type else ""
        ea = [edge_type] if edge_type else []
        out["out_total"] = self.con.execute(
            f"SELECT count(*) FROM edges WHERE src_type=? AND src_id=?{ef}", [e["type"], e["id"]] + ea).fetchone()[0]
        out["in_total"] = self.con.execute(
            f"SELECT count(*) FROM edges WHERE dst_type=? AND dst_id=?{ef}", [e["type"], e["id"]] + ea).fetchone()[0]
        if max(out["out_total"], out["in_total"]) > limit:
            out["note"] = f"truncated to {limit} per direction; open_page lists all relations"
        for r in self._rows(f"SELECT * FROM edges WHERE src_type=? AND src_id=?{ef} LIMIT ?",
                            [e["type"], e["id"]] + ea + [limit]):
            d = self._entity(r["dst_type"], r["dst_id"]) if r["dst_type"] in TYPES else None
            out["out"].append({"edge": r["edge"], "target": self._brief(d) if d else
                               {"type": r["dst_type"], "id": r["dst_id"], "name": r["note"],
                                "uri": f"http://www.wikidata.org/entity/{r['dst_id']}" if r["dst_type"] == "wikidata" else None},
                               "year": r["year"], "note": r["note"]})
        for r in self._rows(f"SELECT * FROM edges WHERE dst_type=? AND dst_id=?{ef} LIMIT ?",
                            [e["type"], e["id"]] + ea + [limit]):
            s = self._entity(r["src_type"], r["src_id"])
            if s:
                out["in"].append({"edge": r["edge"], "source": self._brief(s), "year": r["year"],
                                  "note": r["note"]})
        if not edge_type or edge_type == "MENTIONED_IN":
            mt = "agent" if e["type"] == "person" else e["type"]
            years = self._rows(
                "SELECT report_year, sum(confidence IN ('high','medium')) primary_n, count(*) n "
                "FROM mentions WHERE entity_type=? AND entity_id=? GROUP BY report_year ORDER BY report_year",
                (mt, e["id"]))
            top = self._rows(
                "SELECT m.doc_id, m.segment_id, m.report_year, s.heading, s.kind, s.url, count(*) n "
                "FROM mentions m JOIN segments s USING(doc_id, segment_id) "
                "WHERE m.entity_type=? AND m.entity_id=? AND m.confidence IN ('high','medium') "
                "GROUP BY m.doc_id, m.segment_id ORDER BY n DESC, m.report_year LIMIT ?",
                (mt, e["id"], limit))
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
            "SELECT s.segment_id, s.heading, s.kind, s.agency_id, s.page_start, s.page_end, s.url, "
            "(SELECT count(*) FROM mentions m WHERE m.doc_id=s.doc_id AND m.segment_id=s.segment_id "
            " AND m.confidence IN ('high','medium')) n_mentions "
            "FROM segments s WHERE s.doc_id=? ORDER BY s.ordinal", (d["doc_id"],))
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
        if with_mentions:
            ms = self._rows(
                "SELECT entity_type, entity_id, count(*) n, "
                "sum(confidence IN ('high','medium')) primary_n, group_concat(DISTINCT surface) surfaces "
                "FROM mentions WHERE doc_id=? AND segment_id=? GROUP BY entity_type, entity_id "
                "ORDER BY primary_n DESC, n DESC", (s["doc_id"], s["segment_id"]))
            ents = []
            for m in ms:
                t = "person" if m["entity_type"] == "agent" else m["entity_type"]
                e = self._entity(t, m["entity_id"])
                ents.append({"type": t, "id": m["entity_id"], "name": e["name"] if e else m["entity_id"],
                             "url": e["url"] if e else None, "n": m["n"],
                             "unverified": m["primary_n"] == 0, "surfaces": m["surfaces"]})
            s["entities"] = ents
        return s
