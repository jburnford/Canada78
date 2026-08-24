# Canada50

Integration repo for a knowledge graph of Canadian and imperial history
whose **primary text store is a generated static wiki** — navigation,
lexical search, and graph traversal instead of embeddings.

Canada50 owns the two registries (entities, documents), the crosswalks and
curation files, the merge/build code, the wiki generator, and the retrieval
interface. The source corpora stay independent pipelines:

- [Canada-History-Knowledge-Graph](https://github.com/jburnford/Canada-History-Knowledge-Graph) — census LOD, 1851–1921 (jimclifford.ca/hgiscanada)
- [col_matching](https://github.com/jburnford/col_matching) — Imperial Careers KG (jimclifford.ca/col_matching)
- DeptIndianAffairs — DIA annual reports, 1880–1930
- sessional_papers — Dominion Sessional Papers catalog + OCR (372 volumes)
- uk_trade_db — UK/Canada trade statistics, 1866–1900
- encyclopedia2-26 — Encyclopaedia Britannica 1771–1860 (architecture template)

## Documents

- `DESIGN.md` — architecture: the two-spine model, the wiki layer, access
  tiers, phasing rationale.
- `registries/SCHEMAS.md` — registry and crosswalk schemas, segment
  addressing and text-versioning rules.
- `docs/AUDIT_*.md` — dated audit reports.

## Layout

```
registries/   entities/ documents/ crosswalks/   (generated parquet + csv)
curation/     human adjudications the build consumes (never edit outputs)
build/        registry + site build scripts
mcp/          canada50-mcp server (Tier 1 access)
docs/         reports and specs
```

## Rebuild

```bash
python3 build/audit_crosswalks.py   # crosswalks + registry seeds
```
