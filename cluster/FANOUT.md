# Qwen extraction fan-out across clusters

_2026-08-27. Qwen3.8-27B-FP8 + vLLM 0.27.1 in one apptainer image, one job
per 40 GB MIG slice, sharded **by volume**: each job takes an `EDITIONS`
list, writes `eval/results/<task><year>_qwen38_<effort>/`, and results are
rsync'd back to WSL where remerge → link → adjudicate run locally
(docs/MCP_EVAL.md, docs/SCHEDULE_EDITIONS.md). No shared state between
clusters; the container is the reproducibility unit._

## Clusters

| cluster | slice | job script | staging | throughput (measured / expected) | notes |
|---|---|---|---|---|---|
| plato (USask) | A100 `gpu:3g.40gb:1` (no `--partition`; filter routes by gres) | `cluster/plato_qwen38.slurm` | `/project/clifford/canada50` (done) | ~1.4M chars / 8 h (FP8 via Marlin W8A16) | pip builds from source; home quota tight; apptainer/1.4.5 |
| Nibi (DRAC) | H100 `gpu:nvidia_h100_80gb_hbm3_3g.40gb:1`, account `def-jic823_gpu` | `cluster/nibi_qwen38.slurm` | `~/projects/def-jic823/canada50` (= `/project/6080182/canada50`; `cluster/nibi_setup.sh`) | expect 1.5–2× plato (native FP8) | **shares MIG slices with the OCR array — do not submit while `sp-mig` is running** |
| Narval (DRAC) | A100-40GB full (`--gres=gpu:a100:1`), Milan + NVLink; no H100s | copy `nibi_qwen38.slurm`, change gres + account | not staged | = plato slice config (A100 40 GB, FP8→Marlin W8A16), so ~1.4M chars / 8 h validated | 2026-08-27: 564 A100s, 93% used, 280 pending for 41 free — but our fair-share 0.409 / effective usage 0.042, zero queued; `a100_4g.20gb` 81% idle (26/32 free) yet too small for FP8 weights (28.8 GB) — needs a full W4A16 quant (~15 GB) to use; best-positioned cluster for the *next* corpus |
| other DRAC | same image + weights; edit gres/account | copy `nibi_qwen38.slurm` | `cluster/nibi_setup.sh` with `C50=` | — | compute nodes have no internet: stage on login node |

Setup per cluster: pull `docker://vllm/vllm-openai:latest` → `.sif` (≈8 GB,
20–40 min), `hf download Qwen/Qwen3.8-27B-FP8` (29 GB), rsync the repo
(code + `registries/external/*/chunks_*`). Keep tmp/cache on project or
scratch, never home. **Narval kills the image unpack on its login node
(silently, ~5 min in) and compute nodes have no internet** — copy the
built `vllm.sif` from another cluster instead (relay through WSL:
`ssh nibi 'cat …/vllm.sif' | ssh narval 'cat > …/vllm.sif'`); the weights
download survives the login node fine.

## Work units (env interface is identical on every cluster)

```
TASK=schedule EDITIONS="1897 1898"             # Schedule of Indian Reserves editions
TASK=census   EDITIONS="1897 1898 1899 1900"   # band census tables
CHUNKS=9 FORCE=1 N_WINDOWS=3                   # targeted rerun of one chunk
PARALLEL=3 MAX_SEQS=4 EFFORT=medium            # concurrency / thinking effort
```

Sizing rule of thumb: output tokens ≈ 0.9 × source chars at `effort=medium`
(thinking included); a 40 GB A100 slice sustains ~25 tok/s single-stream,
~50 aggregate with `PARALLEL=3`. A 200K-char volume ≈ 1–1.5 h.

## Queue as of 2026-08-27 (afternoon)

plato: A `5847283` FAILED at 1904 (str-row crash, fixed 19:50 EDT) →
replaced by A3 `5847460` census 1897–1909 → C `5847461` sched 1900 ch.9 →
D `5847462` sched 1901 ch.3. (plato's census B was cancelled in favour of
Narval.)
Narval: census B `1881165` crashed in 1911 → B2 `1908898` (running);
`1881304` school S1 1896–1901 (running; schema validated on first chunk);
`1883381` school S2 1903–09, `1883383` S3a 1910–17, `1883385` S3b 1918–30
(queued). All school volumes are now assigned.
Nibi: **census second batch submitted 2026-08-27 22:00 EDT** (`--nice=100`
so the last OCR rescues schedule first): `20687236` N1 `1886 1893`
(1.39M), `20687237` N2 `1895 1896` (0.98M), `20687238` N3
`1906 1925 1926 1928` (1.01M); 12 h each on one H100 3g.40gb slice.
Still unlocated census volumes: 1881/1882 (No. 4 runs to EOF), 1887–92,
1910, 1918–23, 1927, 1930 (heading variants — inspect ToCs).
Nibi: staged 2026-08-27 (vllm.sif = vLLM **0.28.0**, plato's is 0.27.1 —
same prompts/client; note the version in any cross-cluster comparison),
weights 29 GB; nothing submitted (OCR array occupies the MIG slices).

## Table families and their state

| family | chars | schema | locator | status |
|---|---|---|---|---|
| Schedule of Indian Reserves 1897–1902 | 1.9M | done | done | extracted, linked, adjudicated |
| Band census 1897–1929 (20 vols) | 4.2M | done (`--task census`) | `eval/make_census_chunks.py` | running on plato |
| Band census, formerly unlocated: 1881/82 (No. 4), 1887–92 (= Tab. Stmt. **No. 3**), 1902 (pp. 552–586), 1910 (pp. 879–959) | 0.9M (not 5M — thin volumes) | same | hand-pinned `RANGES` / `--pin` in `make_census_chunks.py` (2026-08-28) | **running on plato** `5848183` → `5848186` (2026-08-28 22:40 EDT) |
| Census recapitulation-only years 1918–21, 1923, 1927, 1930 (Table No. 1 by inspectorate/province; no band rows printed) | 0.17M | generic `--task table` | `make_census_chunks.py --out-dir tabstmt_tables` | **plato `5848184`** |
| School Statement 1902 (pp. 529–548; rough OCR in the recovered volume) | 0.15M | `--task school` | `--pin 1902:529-548 --out-dir school_tables` | **plato `5848187`** |
| Band census 1922 | — | — | `dia_ar_1922.md` = 84 pp garbage OCR; needs re-OCR | blocked |
| **Indian Trust Fund — Return B per-band accounts** (Dr/Cr statements; years the Returns family missed: 1884, 1887, 1888, 1892, 1894–97) | 4.6M, 314 chunks | generic `--task table` | `make_census_chunks.py --pin --append --out-dir tabstmt_tables` (spans from the "INDIAN TRUST FUND" heading to volume end) | **running Narval 2026-08-29 23:05 EDT**: tf1 `2079918` (1887), tf2 `2079920` (1884/88/92), tf3 `2079921` (1894–97) |
| **Auditor General's Part J** (Indian Affairs expenditure & revenue by vote/agency/payee, bound into the AR 1898–1930 under the "Indian Trust Fund" title) | ~16M | generic `--task table` (`TASK=agrep` → `agreport_tables`) | pinned per year | pilot 1900 DONE (2,852 rows, good: agency + payee/itemised label + amounts). **Stage 1 DONE** (1898–1913, 15 vols, 1,170 chunks, 84,500 rows, 0 errors; 14 jobs finished in ≤6 h). **Stage 2 = 1914–30 running on Narval** (18 jobs `2095535–54`, 2026-08-30 08:30 EDT; 1,486 chunks, ~23M chars; big years split a/b by CHUNKS). 1902 volume has no AG report. |
| Officers (Return A) 1880–1904, 21 vols — 9 years re-cut 2026-08-30 after Hoy validation (END regex fired on the in-table "Indian Lands" header, truncating spans to 4–6 pp; real spans 16–31 pp). Family now 5,881 rows | 1.0M | `--task officers` | pinned | Narval `2079924`; re-cut 1881/1891/1894/1899 in `2107631` (**pull first deletes `out_NN` with NN ≥ the new chunk count** — 1881/1891/1894 each carried 2 stale chunks, inflating totals by 20–35%). **Validated per year with `eval/compare_officers_hoy.py`** (our distinct staff vs Ben Hoy's): 15 of 21 years ≥ 0.95, 1881/1894 fixed to 1.00. Residual gap traced to the segmenter: `CAPTION_RE` required the exact `RETURN A (1)`, so the printer's `RETURN, A (1).` (1891) and `RETURN A (1.` (1881) produced **no Return A (1) segment** and the Ottawa headquarters table never reached the chunker — 49 of the 51 people missing in 1891 are that one table. Regex widened in `build/segment_dia.py`; 1891 pp. 676–678 and 1882 pp. 430–431 cut with `make_census_chunks.py --pin … --out-dir officers_tables` → Narval `2110042` → **1882 and 1891 both now 1.00**. Second defect, same family: Return A's own sub-tables **"MISSIONARIES receiving remuneration…" and "MEDICAL MEN employed…"** (and 1904's "OFFICERS OF OUTSIDE SERVICE AT HEADQUARTERS") list staff by Address/Denomination/Tribe, not Office, so `officers_page()` scored them as non-staff and dropped them — they are exactly Hoy's missing "Missionary"/"Medical Man" rows. Gate vocabulary widened in `eval/make_table_chunks.py`; 1880 pp. 177–178, 1884 pp. 381–383, 1888 pp. 680–681, 1904 pp. 972–973 + 998 pinned → Narval `2110707` → all four closed (1880 **1.00**, 1884 0.99, 1888 **1.00** with all 253 of Hoy's shared, 1904 0.97); then 1889 p. 638 and 1890 pp. 647–648 (the same two sub-tables) → `2113918`. **Measure with `--span-only`**: six chunk dirs hold a stray chunk cut from the Auditor General's ledgers or Returns C–G (1884 `07`, 1888 `06`/`07`, 1889 `08`, 1890 `09`/`10`, 1904 `10`/`11`), whose retired-allowance and teacher-salary rows are real staff but not Return A — they were flattering 1884 to 1.03 and 1888 to 1.04, and they must be filtered before the officers rows feed the persons layer (step 6). **1899 (0.93) is not a gap** — its chunks cover the whole return; that volume's OCR fragments pages (several under 500 chars) and rows are thin throughout |
| **Agricultural & Industrial Statistics 1895–1930** (band-level economic tables: one wide table 1895–1913; Tables No. 2 grain/roots, 3 land & buildings, 4 live stock, 5 value of property, 6 income 1914–30) | **8.59M**, 34 vols, 35 chunk dirs | generic `--task table` (`TASK=agstat` → `agstat_tables`, results `agstat{year}_…`) | `eval/make_agstat_chunks.py` — text-scan locator (segment registry never headed these); per-table sub-spans each with own header; 1902 pinned (587–648); 1895 mixed-case / 1906 "AGRICUTURAL" handled by regex | **DONE Narval 2026-08-29** (six jobs `2048361–66`, all <10 h): 34 vols home, **38,891 rows, 0 errors** except 1912 ch.10 (parse failure → rerun `2074361`). 1902 thin (129 rows, rough OCR). Column maps per table (Claude) next; pre-1914 wide table needs per-page header reconstruction. |
| Return A = officers & employés staff lists (1880–1904, 18 vols) | 0.89M (the 8.7M was ledger spill-over) | done (`--task officers`) | `make_table_chunks.py --family officers` (office/expense page gate) | **running on Narval `1919493`** (2026-08-27 23:15 EDT) |
| DIA Tier-2 mention residuals (20,338) | ~7M prompt tokens | `eval/disambiguate_residuals.py` (`--prepare` locally, stdlib on cluster; 20/prompt, **thinking off**) | n/a | **DONE Narval `1922186`** 3.4 h, 20,306 decisions, 0 failures → `build/apply_residual_decisions.py` → 11,706 Tier-2 mentions + review queue |
| Tabular Statements No. 1–3 + Returns B–G (1880–94) | 9.5M | generic `--task table` | `--family tabstmt` (chunks generated, includes ledger-type returns) | **DONE Narval 5 jobs, 2026-08-28/29: 14 vols, 35,944 rows, 0 errors** (1894 ch.04 redone on plato). Note: the `[123]` heading regex means the 1888–92 census (= Tab. Stmt. No. 3) is in this family too — a generic second witness to the plato census run. **1887 missing**: the segmenter swallowed pp. 548–727+ of `dia_ar_1887` into "INDIAN OFFICE (cont.)" letters (no TABULAR STATEMENT segments — segmenter defect to fix); cut by pinned pages instead (`make_census_chunks.py --pin 1887:562-567 / 568-599 / 600-616 --append --out-dir tabstmt_tables`, 13 chunks, 286K) → **Narval `2019989`** (2026-08-29 00:50 EDT). Returns B–G 1887 (ledgers) not cut. |
| School Statements (1896–1930, 34 vols; 1902 deferred) | **5.9M** (the 26.5M figure was Auditor-General ledger spill-over mis-segmented as "SCHOOL STATEMENT (cont.)") | done (`--task school`: identity cols + headers row + positional values) | `eval/make_table_chunks.py --family school` (header-signature + per-page content gate + continuation-header inheritance) | **EXTRACTED 2026-08-27 on Narval (4 jobs, ~6 h wall)**: 14,702 school-year rows, all chunks; results in WSL `eval/results/school{year}_qwen38_medium/` — next: link schools/reserves/agencies, mint a schools facet (Claude) |
| Residential-school principals' reports (1896–1910, narrative letters under BOARDING/INDUSTRIAL SCHOOL headings) | ~? | not a table — needs a *schools* entity facet + Tier 0/1 linking + wiki pages | — | registry gap, noted |
| Sessional papers series tables | per series | TODO per series | TODO | after OCR completes |

Claude designs schema + locator + validation sample before any family gets
GPU time; Qwen transcribes; Claude links and adjudicates.
