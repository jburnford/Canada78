# Canada50 — DIA Knowledge-Graph Build Plan (v2, 2026-08-30)

Scope: the Department of Indian Affairs annual reports 1880–1930 (`dia_ar_*`),
built out **to completion** before the sessional-papers layer. Trade &
Navigation data is out of scope here (separate session). Everything below
stays local / GitHub-only (no Pages deploy) pending consultation with
Indigenous colleagues (see memory: no-public-html-indigenous).

Division of labour (standing): Qwen on the clusters does bulk extraction —
that part is now essentially finished; every step below is Claude-side
modelling, linking, validation and generation.

## 0. Where we are

| layer | in the graph today | extracted, not yet loaded |
|---|---|---|
| reserves | 1,422 (1902 Schedule) + 5,822 attestations 1897–1902; adjudications | — |
| bands | **236** (thin; residual review found 1,779 mentions naming bands we lack) | census rows name every band, every year: 38 vols / 32K rows |
| agencies | 570 in 280 chains, LINCS-linked | AG Part J sections (agency names per year) |
| persons | 412 minted (signatories) | officers 21 vols / 4.7K rows; AG payees (~200K rows) |
| places | 8,706 | — |
| schools | none | school statements 35 vols / 16K rows; principals' reports (narrative) |
| series | none | census (population, religion, age/sex), agstat 35 vols / 41K rows, Return B trust funds (69K rows incl. Returns), AG Part J |
| mentions | ~130K Tier 0/1/2 | — |

Extraction inventory and cluster provenance: `cluster/FANOUT.md`; result dirs
`eval/results/<family><year>_qwen38_medium/`.

## 1. Identity model decisions

### 1.1 Bands are the hub
The band registry is rebuilt **from the census series** —
the department's own annual list of bands per agency — not from mentions.

- **Band identity key**: (normalised name, agency chain, era). A band record
  persists across years while name-normalised + agency match; renames and
  agency transfers are recorded as `band_event` rows (RENAMED, TRANSFERRED,
  MERGED_INTO, SPLIT_FROM, ABSENT_AFTER) exactly like `agency_chain_events`.
- **Name normalisation**: strip possessives ("Beardy's" → "Beardy"), treaty
  numbers in parentheses, "band of"/"Indians of" wrappers, OCR variants by
  char-similarity within the same agency-year (reuse `link_schedule_editions.sim`).
- **Modern anchor** (never `sameAs`, always *succeeded-by*): Wikidata First
  Nation band government items — 627 exist, **611 carry the ISC band number
  (P2865)**; ISC "First Nations Location" gives the same 638 band numbers with
  names + coordinates. Historical band → modern band government is a
  reviewed crosswalk (`registries/crosswalks/band_modern.csv`), seeded by
  (a) 1902-Schedule reserve → modern reserve (below), (b) name similarity
  within province, (c) the LAC "Index of Bands in Western Canada" (~1,500
  names from FA 10-12, 1871–1959, with agencies — see §4).
- **Reserves → modern reserves**: Wikidata has 3,489 Indian-reserve items
  (3,026 with GeoNames, 3,397 with coordinates, 935 with StatCan geo code,
  263 with reserve number P2887); NRCan Aboriginal Lands has every current
  reserve polygon with the official name+number ("STAR BLANKET I.R. 83D")
  and CLSS code. Reserve-number + province is a strong key for prairie/BC
  reserves; eastern reserves link by name. Output: `reserve_modern.csv`.

### 1.2 Schools
- Entity: `school` = (name, type ∈ {day, boarding, industrial, residential},
  reserve/agency, denomination). Identity across years by (normalised name,
  agency); type changes and renames are events.
- Modern anchors: **Schedule K** (699 federal day schools with name variants,
  open/close dates, location, denomination — downloaded, `registries/external/schools/`)
  and the **NCTR/IRSSA list** (140 residential schools; per-school pages carry
  dates and denominations). Link by name variant + province + date overlap;
  never assume a pre-1930 day school is the Schedule K school of the same
  name without date/location agreement (Schedule K dates mostly start 1920s–50s).
- Teachers (school rows) and principals (narrative reports) → persons
  (§1.3), attested with year + school.

### 1.3 Persons — **LINCS is the identity authority for DIA staff**
Revised 2026-08-30 once the provenance chain became clear:

    DIA annual reports (Return A) → Ben Hoy's `CAN_DIA_Employees_Checked_
    Complete.xlsx` (14,477 person-YEAR rows, human-checked, geocoded, with
    page + bac-lac URL, but **no person id across years**) → LINCS Indian
    Affairs dataset (14,138 activities resolved to **2,468 person URIs**,
    mean career 5.6 yrs, longest 41: M. Benson 1876–1916)

The user's team did the career-linking that Hoy's rows lack, and it also
*splits* 21 ambiguous names into distinct people (A. McDonald ×2, D.
McDonald ×2 — the "John Macdonald" problem, already solved here). So:

- **Do not mint our own persons for DIA staff.** Use the LINCS `agent` URI
  as the person identity; our officers rows and Hoy's spreadsheet are the
  underlying attestations.
- Our extraction contributes what the LINCS activity model does not carry:
  per-year **designation, salary, appointment date, first-civil-service
  date**, plus our own page-level provenance.
- Join: our officer row → LINCS agent on (normalised name, year). A crude
  surname+initials key already reaches **67%** (3,926/5,881 rows, 1,152 of
  2,468 persons). The residue is name-form, not data: "John O. Arsennault"
  vs "John Arsenault", "Wm. Plummer" vs "William Plummer", and compound
  surnames my key mis-parses ("McGregor Ironsides", "Wm. Van Abbott").
  A proper matcher — surname = last token, expand Wm./Thos./Jas./Chas.,
  fuzzy on surname, require place or year agreement — should exceed 90%.
- Persons *not* in LINCS (teachers from school statements, AG Part J payees,
  letter signatories) are minted by us as before, and checked against LINCS
  before minting.
- AG Part J payees: mostly firms ("Hull Bros.", "Stovel & Strang") and
  local persons; **mint, don't ground**, except where the payee is already
  an officer/teacher/chief in the registry. Deferred until the
  sessional-papers mention-detection design exists (same person/firm/
  commodity problem; see `docs/SESSIONAL_PARSING.md`).
- Indigenous individuals named in ledgers (chiefs, pensioners in Return B,
  commutation lists) are **not** minted as persons in this phase — flagged
  for the consultation.

### 1.4 Series (observations)
One `observation` table, not per-family schemas:
`(entity_id, entity_type, series_id, year, value, unit, source_family,
paper_id, page, chunk, row_idx, confidence)`. Series ids come from the
column-semantics maps (§2). Wiki pages render series as tables/sparklines;
the MCP exposes `series(entity, series_id)`.

**Not every family is keyed by band** (corrected 2026-08-30 against the data).
The census is, and the trust funds are. The **Agricultural & Industrial
Statistics are keyed by *agency*** in every era — 1897 "Walpole Island",
1905 "Grand River Superintendency — Six Nations", 1914-30 "Blackfoot",
"Hobbema", "Lesser Slave Lake" — so agstat observations attach to the agency
chain, not to a band, and a band page shows its agency's economic series as
context rather than as its own. School statements are keyed by school.

## 2. Column-semantics maps (written by hand, one per format era)

| family | eras | notes |
|---|---|---|
| census | 1881–82 (No. 4), 1887–92 (No. 3: census + denominations), 1895–96 wide (census + agricultural cols), 1897–1917 (No. 4/1: population, religion, age/sex, births/deaths), 1918–30 recap only (by inspectorate/province) + 1924/1929 band level | `headers` rows in the output give per-chunk column names; the map assigns `series_id` per column label pattern |
| agstat | 1895–1913 wide table (column groups printed on successive pages → per-page header reconstruction), 1914–16 Tables 2–7, 1917–30 Tables 2–6 | 14 header variants seen in 1900 alone; map by table number + normalised header |
| school | 1896–1930 (1902 rough OCR) | identity cols already structured; values = roll/attendance/standards |
| Return B | 1884–97 | **not** from the Qwen rows — the deterministic two-sided parser (§3.2) |
| AG Part J | 1898–1930 | amount normalisation (dollars/cents split), vote/section headings → `expenditure_category` |

Deliverable: `curation/column_maps/<family>.yaml` + `build/apply_column_maps.py`
that emits observations and a per-family coverage report (columns unmapped,
rows dropped, per-year counts).

**Built 2026-08-30 for the census.** The per-era split turned out to be
unnecessary: the extractor emits a `headers` row of printed labels and data
rows whose `values` are positionally aligned to them, so the map is a list of
*label → series* rules, with an optional `years:` window for the few labels
that changed meaning. `curation/column_maps/census.yaml` (31 series) →
**139,681 observations, 3,068 bands, 1881–1929**, 8 unmapped labels left (556
occurrences, mostly the 1902 age columns that lost their male/female spanner).
Rule ids may interpolate regex named groups (`crop_{crop}_acres_sown`) so the
agstat table's ~1,270 labels can be covered by a few dozen shape rules.

Validation is the census's own redundancy: it prints the population and then
breaks it down twice, by religion and by age/sex. The two breakdowns sum to
the printed population within 2% in **84% / 85%** of band-years; the rest go to
`observations_census_checks.csv` (usually a provincial total line read as a
band, or a run-together OCR digit).

**Built 2026-08-30 for agstat.** `curation/column_maps/agstat.yaml` (34 rules,
which expand to **542 series**) → **164,751 observations over 540 agencies,
1895–1930**; 293 labels left unmapped (20,339 cells, ~11%, listed in
`observations_agstat_coverage.csv`). Enumerating the ~1,270 printed labels was
never going to work — the department invented columns yearly and merged old
ones ("Mowers, Reapers, Binders, Threshers, etc.") — so the rules capture the
varying part and keep the printed wording in the series id
(`crop_wheat_acres_sown`, `value_household_effects`, `effects_sail_boats`).
Spot-checks read true: Blackfoot and Blood lead wheat acreage, and Cowichan's
wheat runs a smooth 34-year curve 5 → 53 acres.

The agstat entity had to be built first: `build/mint_agencies_from_agstat.py`
mints an identity for every agency the *series* attests (the letters-derived
registry has only the 280 that filed narrative reports, and misses the Nova
Scotia county agencies, Moravian, Bécancour, Mud Lake, The Pas). **1,102
identities, 110 linked to an existing chain**, name variants clustered by
complete linkage within a province pool exactly as in step 2, and the rows
gated by their governing header — without that gate the lists of chiefs and
band tables beside these pages mint thousands of phantom agencies from person
names.

## 3. Build steps, in order

1. **Snapshot** (user says "commit"): everything since 8dd27e7.
2. **Band registry v2** — `build/mint_bands_from_census.py`: iterate census
   rows by year → (agency, band) → registry with first/last attested year,
   name variants, provinces; band_events for renames/transfers; load the
   1,779 residual-review band mentions as candidates. Validation: every
   census row links to exactly one band; no band with <2 attestations unless
   1930-only. Then `band_modern.csv` seeding (Wikidata P2865 ∪ ISC locations).
3. **Reserve ↔ modern reserve crosswalk** — Wikidata reserves (name+number+
   province) + NRCan AL names; review CSV for ambiguous; attach QID/CLSS code
   as *succeeded-by* on reserve pages. **DONE 2026-08-30.**
   `build/link_reserves_wikidata.py` does the second hop NRCan → Wikidata
   (bulk SPARQL dump in `registries/external/wikidata/reserves_wd.csv`): 2,930
   of 3,317 modern reserves matched, and **1,012 of the 1,032 historical
   reserves now carry a QID** with coordinates, 961 with GeoNames and 420 with
   a StatCan CSD code. On Wikidata P2887 "reserve number" actually holds the
   5-digit CLSS code, which is a direct join to NRCan's ALCODE — 267 such
   identifier matches, and held out as gold they confirm the name-based rules
   on 244 of 248 (the 4 disagreements are duplicate Wikidata items).
   `build/link_bands_modern.py` then carries historical bands to modern band
   governments (ISC band number ∪ Wikidata P2865 ∪ the StatCan band↔CSD
   table): **368 of 3,377 linked, 358 with a QID**, 310 to review. Recall is
   low by design — a *succeeded-by* claim about a First Nation is worth less
   than the review queue it skipped, so name similarity is symmetric (Dice,
   with a length-ratio guard) rather than the containment score the reserve
   linker uses, which had scored "Halalt" 1.00 against "Haltkum (Adams Lake)".
4. **Column maps + observations** — census, agstat and school **DONE** (see
   §2); recap years attach to inspectorates/provinces. Still to do: Return B,
   after §3.7.
5. **Schools facet** — **registry and series DONE 2026-08-30**;
   Schedule K / NCTR anchors, teachers → persons and the principals' narrative
   reports (1896–1910) still to do. `build/mint_schools.py` →
   `registries/entities/schools.parquet`: **1,115 school identities**
   (795 day, 89 boarding, 89 industrial, 142 untyped), 12,036 attestations,
   2,544 events (TYPE_CHANGED, NAME_VARIANTS, ATTESTATION_GAP), 331 to review.
   `curation/column_maps/school.yaml` → **83,781 observations over 928
   schools**: roll by sex, average attendance, the standards ladder, the grant,
   and the trades taught in the industrial schools. Three things the identity
   depends on, all found by the numbers coming out wrong:
   - the same **header gate** the other families need — without it the registry
     mints schools out of teachers ("Watson, A.M., M.D."), farmers and bands;
   - **the school number is the identity**, not noise: normalising "No. 10"
     away fused all twelve Six Nations day schools into one whose roll was the
     average of the lot (fractional values in the series gave it away);
   - a **blank province must be filled from the same school in another year**,
     or the printer's omission spawns a second identity (this had the Mohawk
     Institute and Shingwauk Home each split in two).
   Spot-checks read true: Kuper Island, Kamloops, Kootenay, Battleford, Mohawk
   Institute, Shingwauk, Coqualeetza, Elkhorn all present with plausible spans
   and denominations; median day-school roll 27 pupils.
   **Repaired 2026-09-02 — see `docs/SCHOOL_REPAIR_PLAN.md`.** The
   extractor's `school_type` defaults to "day" and the printed type is a
   page heading, so ~40% of residential enrolment had been filed under day
   schools. Now: `build/school_type_map.py` (page-positional type from the
   statement titles) → `mint_schools.py` (type in the identity key, unit /
   staff / land-sale / recap rows dropped, stable ids) →
   `apply_column_maps --family school` → **`build/harvest_school_totals.py`
   must pass** (extracted vs the deputy's printed per-class totals, ±15%)
   before the family is promoted. Reference list: Orlandini's TRC/NCTR
   residential-school dataset (`build/link_schools_irs.py`). Rebuild order
   for the school layer: school_type_map → mint_schools → apply_column_maps
   → link_schools_irs → link_teachers → harvest_school_totals → gen_wiki →
   index.
6. **Persons v2** — **officers → LINCS DONE 2026-08-31.**
   `build/link_officers_lincs.py` attaches a LINCS agent URI to each Return A
   row: **5,535 of 6,083 rows (91.0%)** across 21 years, reaching **1,361 of
   the 2,468** LINCS persons; the rows carry the per-year designation, salary,
   appointment and first-civil-service dates the LINCS activity model lacks,
   plus page provenance → `registries/annotations/officer_attestations.parquet`
   (`officer_rows_foreign.parquet` holds the 109 non-Return-A rows from the
   seven stray Auditor-General/Returns C–G chunks, kept but not attested as
   officers). Crosswalk `officers_lincs.csv`, review queue
   `officers_lincs_review.csv` (528 name-years: 396 below gate, 82 no
   candidate, 32 ambiguous, 13 demoted conflicts, 5 unparseable).

   **Acceptance: `eval/compare_officers_lincs.py` — precision 0.99, recall
   0.99** on LINCS agent sets per year, against Hoy's rows matched to LINCS by
   the same matcher but from an independent transcription. The only soft years
   are 1899 (rec 0.91) and 1904 (0.97) — the same two `compare_officers_hoy.py`
   flags, so this is the known OCR ceiling, not a linking fault.

   The matcher is `build/person_names.py`, shared by later person work. It does
   not reduce a name to one key: `parse` returns *surname variants* plus given
   initials, and `score` weighs them separately (0.65 surname / 0.35 initials,
   surname floor 0.87). Four rules each fixed a wrong link, and calibrating on
   Hoy→LINCS — where near-100% is the known answer — took it from 61.7% to
   **98.0%**: strip post-nominals *before* reading a comma as `Surname, Given`
   (else "Peter E. Jones, M.D." has the surname "peterejones"); rejoin a
   trailing run of single letters, because `M.D.` tokenises to `m`,`d` and left
   "J. Macdonald, M.D." matching *M.A.* McDonald; keep particles with the
   surname ("Van Abbott", "D'Amour"); and compare full given names, since
   "Agnes Cameron" and "Angus Cameron" tie on the initial alone. A consonant-
   skeleton blocking key catches "McNiell"/"McNeill", which a prefix key misses
   because "Mc" eats the prefix.

   **Teachers DONE 2026-09-01.** `build/link_teachers.py` mints person
   identities from the 10,454 teacher school-years (5,225 distinct strings,
   1896–1930): **3,608 identities** (1,757 multi-year, 457 multi-school) →
   `registries/entities/persons_teachers.parquet` +
   `registries/annotations/teacher_attestations.parquet`. LINCS holds almost
   no teachers (~38 rows behind its 14,477), so teachers are minted — but
   each identity is checked against the LINCS agents first and **54 carry a
   LINCS URI** instead (Rev. principals who were salaried missionaries, and
   22 rows where the printed string itself says "…, Agent": Nova Scotia
   county rows print the AGENT in the teacher column, so those carry
   `role=agent`). Identity model is deliberately conservative: complete
   linkage within a school at score ≥0.90; across schools only exact folded
   equality within one province; a Rev./Mr. vs Miss/Mrs./Sister class
   conflict blocks any merge, decided by majority vote so one OCR "Miss" on
   26 "Rev. C.D. White" rows doesn't split him. Auto-accepting a LINCS link
   requires two agreeing initials or a matching spelled-out given name AND a
   non-female title class — **"Mrs. W.R. Tucker" is the agent W.R. Tucker's
   wife carrying his initials**, the 19th-century convention, and initially
   linked to him at 1.00. Identities the authority unifies are merged (de
   Molitor taught in Nova Scotia then B.C.; the name alone kept them apart).
   721 rows in `teachers_review.csv` (163 same-name-two-provinces, 518 LINCS
   candidates below gate).

   **Letter signatories re-anchored to LINCS DONE 2026-09-01.**
   `build/link_signatories_lincs.py`: the 412 persons `link_mentions.py`
   minted from agency-report signatures predate §1.3 (LINCS as person
   authority) and are exactly the agent population LINCS resolved — **284 of
   412 now carry a LINCS agent URI** (person_ids unchanged, so wiki mention
   anchors still hold; `persons_minted.parquet` gains a `lincs_agent`
   column). 61 ambiguous — mostly LINCS-internal duplicates ("J. Harlow" and
   "John Harlow" as two agents), 51 no candidate, 16 below gate/no overlap →
   `signatories_lincs.csv`. Cross-source check that fell out for free: Neil
   Gilmour appears as a teacher 1898–1902 and a signatory 1905–06 and both
   resolve to the same LINCS agent.

   Still to do for step 6: reconciling the `officers_lincs_review.csv`
   queue (historian). Note 1904 and 1910 rows carry no
   `paper_id` — those volumes are the standing `pending_catalog_post1900`
   cases, not a gap introduced here.
7. **Return B parser** — **parser + validation DONE 2026-08-31**, band
   linking still to do. `build/parse_return_b.py`: **23,283 entries across
   2,703 accounts, 13 volumes 1882–1897**. Of the 1,825 two-sided accounts
   **1,118 (61.3%) balance Dr == Cr**, and **1,020 of 1,723 (59.2%) match the
   total the return prints for itself** — an independent check that catches a
   dropped or doubled line leaving both sides equally wrong. Department-wide
   closing balances reproduce the printed figures exactly (3,594,206.20 in
   1895 → 3,692,516.01 in 1897).

   Three structural findings, each surfaced by an imbalance, not by reading:
   (a) **the `tabstmt` dirs are not one table** — they hold land sales, "TO
   WHOM PAID" lists and census returns, so rows need the same governing-headers
   gate the column maps use, else county acreages parse as money into the fund
   totals; (b) **an account is bounded by its own printed `total` row, not by
   `section`** — the extractor leaves `section` null on continuation rows so a
   stale name runs on (p.717 rows were joining Batchewana's p.683 account,
   which balances exactly alone); adding a stated-section change as a boundary
   took the balance rate 34.9% → 61.3%; (c) **the header can misstate the
   column count** — under one "Debit | Credit" header on p.802 of 1893, Gibson
   prints four columns and Texas Lake two, so effective shape comes from the
   widest row in the account.

   Only 28 of the remaining failures are cent-level OCR slips; the rest are
   structural. `trustfund_reextract.csv` lists **23 (year, shape) page-range
   blocks covering 707 accounts** for the "failures → Qwen on the halves"
   branch — narrow page ranges, so the re-run is cheap, and Narval is idle.

   The balance series carries its account's verdict rather than publishing
   silently: **4,037 balance lines, 2,027 of them (796 accounts) from accounts
   that balance**; 742 of those carry band-style names ("Gibson Indians
   (No. 123)"), which is the input to the band-linking pass.

   **Band linking DONE 2026-08-31.** `build/link_trustfund_bands.py` links the
   1,052 distinct account names to the census band registry: **658 names
   (1,775 of 2,703 account-years) linked to 167 bands**, 52 classified as
   department funds (Land Management, School Fund, Salaries…), 12 as personal
   accounts, 160 in `trustfund_bands_review.csv` (83 below gate, 35 ambiguous,
   28 number conflicts, 12 collective annuity funds like the Robinson-treaty
   "Ojibbewas of Lake Huron" that must not land on one band). Crosswalk:
   `trustfund_bands.csv`. Design points that earned their keep:

   - The 1893–97 lists print a **stable account number "(No. N)"** — the same
     band keeps its number across editions and spellings — so linked spellings
     propagate their band to unlinked same-numbered ones. The ledger era's
     "in Account No. N" is a *different* numbering (Shawanaga is ledger 33 but
     list 34, where list 33 is Six Nations) and is recorded but never trusted.
   - **Embedded band numbers are identity** (the schools lesson again):
     without the guard, "Hungry Hall Band No. 1" linked arbitrarily to Hungry
     Hall No. 2, and "Shoal Lake Reserve 39" to Shoal Lake (Crees).
   - An exact hit on a band's **own printed name outranks a variant hit** —
     Walpole Island's attestation variants contain "Chippewas of Beausoleil"
     (a mis-clustered census row), which scored 1.00 until primaries won ties.
   - Variants under three characters are dropped: one census variants list
     holds a stray "t" that matched every "(t)"-annotated account at 1.00.
   - Number-group conflicts whose linked names are mutually similar
     (Sampson/Samson, Munceys/Munsees) settle on the best-attested identity —
     these mirror the duplicate pairs in `band_registry_merge_review.csv` and
     the account number is itself evidence for that merge; genuinely different
     names (Enoch vs Paspaschase, Manitoulin Unceded vs Wikwemikong) stay in
     review.

   The trusted, band-linked balance lines are published as observations:
   **1,034 rows (`trustfund_capital`, `trustfund_interest`), 156 bands,
   1881–1897**, deduplicated per (band, fund, date) preferring the closing
   line (the 30 June balance is printed twice: closing of year Y and opening
   of Y+1) and summed over a band's funds (St. Regis holds a main account and
   a Land Fund). Total observations **389,446**. Wiki band pages render the
   block ("Trust fund balances (Return B)"); 34 pages carry it now, the rest
   of the 156 wait on the band-facet switch to the census registry (step 8).
   Left for step 7: only the optional targeted re-extraction in
   `trustfund_reextract.csv`.
8. **Wiki + MCP regeneration** — **first pass DONE 2026-08-30**: the wiki now
   builds **10,379 pages** (was 9,263) and `index.json` carries 3,493 entities
   (was 2,378). `build/series_render.py` renders `observations.parquet` as
   grouped tables with an inline-SVG sparkline for the headline series (no
   scripts, no external assets — the site is static files), and `gen_wiki.py`
   calls it on band, agency and the new school pages: **1,165 pages now carry a
   series block** (928 schools, 134 bands, 103 agencies) and 858 a sparkline.
   The new `schools/` facet is 1,116 pages plus an index. Additive throughout —
   no entity id changed, so mentions and passages render exactly as before.
   **Person pages DONE 2026-09-01.** The wiki now builds **15,032 pages**
   (5,115 persons, was 462) and index.json carries **8,137 entities**. One
   person, one page: identities are unified on the LINCS agent URI when the
   authority owns them (`canon_person` over the `lincs_agent` columns of
   `persons_minted` and `persons_teachers`), so Neil Gilmour's single page
   carries his LINCS postings, Return A service record (year, designation,
   salary, appointed), the schools he taught at, and the reports he signed.
   Teacher-only persons get minted pages ("Miss Mary Moffitt — 25 years at
   Cape Croker" reads as one row per year with the OCR variants clustered).
   School pages' teacher tables now link to person pages and person pages
   link back to schools. `person_slug` learned viaf-/wd- forms for the
   officers rows whose identity is VIAF/Wikidata (Macdonald, Vankoughnet).
   Stale pages from earlier layouts must be cleaned before regenerating —
   `git clean -fdX site/` — because `gen_wiki` writes but never deletes.

   **MCP `series` DONE 2026-09-01.** The SQLite index gains `observations`
   (389,446 rows) and `obs_map` (the curated-band → census-band bridge), and
   the server a seventh tool: `series(ref, series_id?)` lists an entity's
   series or returns the year-value points with paper and page. Schools are
   now MCP entities too (`school:SCH-…` with name-variant aliases), and a
   bare `band_c…`/`AG-…`/`SCH-…` id is accepted directly even where no
   entity page exists yet — which is exactly the situation of the ~990
   agstat agencies below. Verified: `canada50 series school:SCH-00421`,
   `… series BAND-abenakis-of-becancour population` (through the bridge),
   `… series band_c01807 trustfund_capital`.

   Still to do: the agstat agencies that have no chain page yet — **blocked
   on a classification pass, found 2026-09-01**: of the 437 unlinked
   `AGT-…` ids that carry observations, many are not agencies at all but
   headquarters STAFF from the expenditure tables ("Duncan C. Scott",
   "Sarah M. O'Grady", pool `HEADQUARTERS - INSIDE SERVICE`) and
   appropriation lines ("Repairs to roads and bridges Tyendinaga") — the
   header-class-gate warning striking again, this time inside the minted
   registry. Making wiki pages for them as agencies would be wrong; the
   registry needs an `identity_kind` column (agency / staff / appropriation)
   first. Their series stay reachable meanwhile through the MCP `series`
   bare-id fallback. Then: the regenerated validation-gate packet, and the
   band-facet switch below.

   **Prerequisite done 2026-08-30.** `build/gen_wiki.py` builds band pages from
   `bands.parquet` — the 236 bands of the 1902 Schedule, which carry the
   hand-adjudicated Wikidata grounding but none of the series, since the
   observations are keyed by the 3,377-identity census registry. §1.1 makes the
   census registry canonical, so `build/merge_band_registries.py` moves the
   curated grounding onto it: **146 of 236 curated bands matched, 125 census
   bands now carry an adjudicated Wikidata succession**, 358 a modern band
   government, **433 at least one anchor** →
   `registries/entities/bands_census_grounded.parquet`. Every unmatched curated
   band is reported in `band_registry_merge_review.csv` — 62 still hold a QID
   that has not landed, almost all because the census registry holds two
   near-identical variants of the same band ("Opet-ches-aht" and
   "Opitchisaht" tie at 0.80) and those two should be merged first.
   Matching keeps the parenthetical ("Nicola (Lower)" vs "(Upper)" are
   different bands and stripping it made them tie), closes the printer's
   hyphens ("Nim-keesh" → Nimkeesh), and accepts a sub-threshold winner only
   when it stands ≥0.10 clear of the runner-up, recording
   `curated_match_score` on the row so the claim stays auditable.
   **Retrieval graph v2 DONE 2026-09-02** — the review of 2026-09-01 found the
   SQLite index the MCP serves was still built from the superseded registries
   (236 bands, 395 persons, a 1902-only edge snapshot) while the wiki had 15K
   pages; the GraphRAG-without-cosine thesis was untested. Rebuilt:

   - `canada50_mcp/index.py` v2 indexes **every registry**: 12,617 entities
     (600 agencies, 3,330 bands incl. census bands, 6,150 persons incl. all
     2,468 LINCS agents, 1,422 reserves, 1,115 schools) and **44,737
     time-scoped edges** (was 2,831): band ADMINISTERED_BY agency per census
     year (12.4K), school IN_AGENCY / ON_RESERVE per year, person TAUGHT_AT
     school, POSTED_TO agency (LINCS postings), SIGNED_FOR / REPORTED_ON,
     reserve ADMINISTERED_BY per Schedule edition, SUCCEEDED_BY for 1,471
     bands and reserves (reserve QIDs from `reserve_wikidata.csv` now land).
     A `redirects` table carries superseded ids to canonical ones so old
     mention anchors and observation keys keep working. Aliases get an FTS5
     trigram table for OCR-tolerant lookup.
   - `neighbors(ref, edge_type, year)` collapses edges to one row per related
     entity with attested year ranges ("1894–1910") and takes a `year=` for a
     snapshot; `series` points carry the citable segment address; `search`
     filters by agency *or school* by id or name; `open_page` synthesises a
     hub for entities without a page.
   - Three identity passes, all alias-not-rename with review CSVs:
     `build/alias_census_bands.py` (**530 census identities in 229 groups**:
     split by a missing province — Pi-a-pot/Piapot, Six Nations ×2 — or
     overlapping in years without ever disagreeing: no shared year with two
     different populations, compatible agency strings, not a province/recap
     line; 135 conflicting same-name pairs left to review), `build/alias_agency_chains.py`
     (35 OCR-variant chains into 21 — Williams Lake ×5, Coutcheeching ×4,
     Cowichan ×3; guards on ordinals/compass words), and
     `build/classify_agstat_identities.py` (`identity_kind` on the 1,102
     agstat identities: 533 agency, 500 staff, 39 appropriation, 20
     ledger_person, 5 school, 5 band_row; only agencies become entities).
   - `build/attribute_segments.py`: letters attributed to a unit **3,159 of
     4,437 (71%, was 40%)** by heading, dateline (a trailing dateline after
     the closing formula is the *next* letter's header block in every era —
     the printer set place/date before the salutation and the segmenter's
     walk-back left it behind; verified 1880 and 1909), phrase, the reserves
     and bands the letter names (voted through their administering unit,
     with province and treaty-section guards), LINCS-posting signature and
     prior signatures; 626 principals' reports attributed to schools. Every
     row carries a `letter_kind` (agency_report / school_report / audit /
     commission / inspection / survey / table_fragment / unknown), so the
     residue is classified: 1,015 unattributed of which 759 "unknown" (mostly
     1880s–90s letters whose dateline names a place no unit list carries).
     Commission, audit and survey letters are deliberately not attributed
     to an agency. `segment_attribution.parquet` feeds
     `segments.agency_id/school_id/letter_kind` and the wiki; single-mention
     (low) attributions stay in the parquet for review but are not indexed.
   - Mention rule: a lower-case common-noun surface ("fishing station") on a
     contents/title page is demoted to low (the Fishing Station No. 10
     audit: 13 of 15 links correct via the agency-context rule, 2 contents-page hits wrong).
   - Wiki: **18,092 pages** — 3,094 census-band pages (`bands/band-c…/`,
     agency-by-year table, series, modern succession), attributed letters on
     agency pages (method badge), principals' reports on school pages,
     variant chains folded into their canonical page; index.json 11,231.
   - Eval: `eval/run_eval_headless.py` drives `claude -p` with the MCP server
     (no SDK); `eval/questions.jsonl` gains q13–q18 (series, time-scoped
     edges, teachers, LINCS postings).

   Also emits `agstat_chain_dateline_links.csv`: agstat county units named
     in the same dateline as a chain ("Micmacs of Hants County" beside
     "Shubenacadie") redirect to that chain in the index (4 units so far).

   Known and left: 759 "unknown" letters (`segment_attribution_review.csv`
   holds the ambiguous 441); 137 same-name census-band pairs that conflict
   on population or agency (`band_canonical_review.csv`); the 1900 census table is
   inside a segment captioned "School Statement" (segmenter caption miss);
   agstat series ids still fragment printed wording ("Cattle cows and milch"
   ×3); no roll-up pages per year/province/treaty yet.
9. **AG Part J payee layer** — after the sessional mention-detection design.

Each step ends with a review CSV for the historian and a coverage report;
nothing auto-applies below the confidence gate.

## 4. External datasets (found 2026-08-30)

| dataset | what it gives | status |
|---|---|---|
| ISC First Nations Location (open.canada.ca b6567c5c…) | 638 bands: band number, name, coordinates; daily | downloaded → `registries/external/isc/` |
| Wikidata band governments (Q2882257) | 627 items, 611 with P2865 band number, 582 with coords | query via WikidataMCP |
| Wikidata Indian reserves (Q155239) | 3,489 items; GeoNames 3,026; coords 3,397; StatCan code 935; reserve no. 263 | query via WikidataMCP |
| NRCan Aboriginal Lands of Canada (GeoBase AL) | every current reserve polygon: name+number, CLSS code, type | SK sample kept; full 55 MB zip on demand |
| StatCan Aboriginal Population Profile — band ↔ CSD table (2016) | First Nation/band → census subdivision (IRI) codes (~1,000 rows) | web table only; scrape if needed (P3012 on Wikidata covers 935 reserves) |
| Schedule K — Federal Indian Day Schools | 699 schools, variants, dates, location, denomination | downloaded → `registries/external/schools/` (PDF + text; needs a multi-line row parser) |
| NCTR / IRSSA residential schools | 140 schools; per-school pages with dates, denominations | scrape per-region pages when building the schools facet |
| LAC "Indexes of Western First Nations Bands: languages, agencies, inspectorates, regional offices" (FA 10-12, ~1,500 names, 1871–1959) | historical band ↔ agency ↔ inspectorate crosswalk — the closest thing to our agency chains, from LAC | canada.ca blocks scripted fetches; **user to save the page as HTML** into `registries/external/lac/` |
| LAC "Indian Affairs Annual Reports 1864–1990" (40,700 pp, transcribed) | a second text witness for every DIA AR incl. the unusable 1922 volume; item-level records (`app=indaffannrep&IdNumber=`) | bot-blocked; check whether bulk text is obtainable (user login / request) |
| LAC treaty annuity paylists (RG10; Héritage reels) | per-band, per-year paylists 1870s–1950s: band membership counts and names | images only; out of scope now; note for the consultation |
| Canadian Geographical Names DB (P821 on Wikidata) | reserve/settlement place names | via Wikidata |

## 5. Open questions for the historian
- Which modern identifier is the canonical "successor" link on a band page:
  Wikidata QID (with P2865) or the ISC band number? (Proposal: store both;
  display the First Nation's current name.)
- Treatment of named Indigenous individuals in ledgers/paylists (deferred;
  consultation).
- Whether 1918–23/1927/1930 recap-only years should appear on band pages as
  "no band-level census printed" rather than silently missing.
