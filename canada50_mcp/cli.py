"""Shell front-end to the same tools (no MCP SDK needed).

    canada50 lookup "Blackfoot" [--type agency]
    canada50 page agency:AG-blackfoot-agency
    canada50 search '"industrial school" AND Qu\'Appelle' [--from 1885 --to 1900] [--kind agency_letter]
    canada50 neighbors band:BAND-kitwangar [--edge OCCUPIES] [--year 1896]
    canada50 doc 1885
    canada50 segment 1885 p0170-blackfoot-agency-n-w-t-treaty [--no-mentions]
"""
import argparse
import json
import sys

from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="canada50")
    ap.add_argument("--db"), ap.add_argument("--site")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lookup"); p.add_argument("name"); p.add_argument("--type"); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("page"); p.add_argument("ref")
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--from", dest="year_from", type=int)
    p.add_argument("--to", dest="year_to", type=int); p.add_argument("--kind"); p.add_argument("--agency")
    p.add_argument("--school"); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("neighbors"); p.add_argument("ref"); p.add_argument("--edge"); p.add_argument("--year", type=int)
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("doc"); p.add_argument("doc_id")
    p = sub.add_parser("segment"); p.add_argument("doc_id"); p.add_argument("segment_id")
    p.add_argument("--no-mentions", action="store_true")
    p = sub.add_parser("series"); p.add_argument("ref"); p.add_argument("series_id", nargs="?")
    p.add_argument("--limit", type=int, default=200)
    a = ap.parse_args(argv)
    s = Store(a.db, a.site)
    if a.cmd == "lookup":
        out = s.lookup_entity(a.name, a.type, a.limit)
    elif a.cmd == "page":
        out = s.open_page(a.ref)
        if "text" in out:
            print(out["text"]); return 0
    elif a.cmd == "search":
        out = s.search(a.query, a.year_from, a.year_to, a.kind, a.agency, a.school, a.limit)
    elif a.cmd == "neighbors":
        out = s.neighbors(a.ref, a.edge, a.year, a.limit)
    elif a.cmd == "doc":
        out = s.get_document(a.doc_id)
    elif a.cmd == "series":
        out = s.series(a.ref, a.series_id, a.limit)
    else:
        out = s.get_segment(a.doc_id, a.segment_id, not a.no_mentions)
        if "text" in out:
            meta = {k: v for k, v in out.items() if k != "text"}
            print(json.dumps(meta, ensure_ascii=False, default=str, indent=1)); print(out["text"]); return 0
    print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
