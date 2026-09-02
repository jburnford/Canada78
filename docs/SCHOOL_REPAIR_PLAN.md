# School-layer repair plan

Written 2026-09-02 after the day-school review found that the school
registry and series cannot state a day/residential balance. The faults, in
order of damage, and the fix for each. Nothing here needs GPU time: every
fault is in Claude-side typing, identity or gating, and the rows already
exist in `eval/results/school*_qwen38_medium/`.

## What is wrong, with the evidence

| # | Fault | Evidence | Effect |
|---|---|---|---|
| 1 | **School type comes from the extractor's `school_type` field, which defaults to "day".** The printed statement gives the type as a heading, not a column: 1896–1916 as `DAY SCHOOLS / BOARDING SCHOOLS / INDUSTRIAL SCHOOLS` sub-headings per province; 1917–1930 as a second table titled `STATEMENT of Indian Residential Schools…` that begins mid-page. A 6-page chunk that starts after the heading has no way to know. | 1928: the heading sits at chunk_02 p.95; chunk_03 (p.97–102) types all 73 rows "day", including Lejac (`"school":"Fraser Lake","reserve":"Lejac"`). Raw counts 1921: 336 day, 0 boarding; 1927: 288 day, 0 boarding. Printed: 74–77 residential schools. | Register shows ~38 residential schools/yr with rolls against 74–77 printed; extracted residential enrolment ≈ 60% of printed, day enrolment ≈ 120% of printed (1925: 9,795 vs 8,191). Any day/residential share is 15 points too high. Same-name day + boarding schools on one reserve (Crowfoot, Old Sun's, Sarcee, Onion Lake, Gordon's, Thunderchild) collapse into one identity whose type flickers. |
| 2 | **Agency rows minted as schools.** The 1897–1900 tabular statements in the same chunks put agency names in the school column; `is_school_header` passed because the header block also named roll columns. | 16 identities named "… Agency" (Temiscamingue, Viger, Lake St. John, River Desert, Jeune Lorette, Lake of Two Mountains …), 233 `roll_total` rows carrying agricultural values (2,850; 11,604.18). | A fake 10% Quebec residential share in 1899; agstat values in the school series. |
| 3 | **1916 identity break, Prairies/NWT.** | 80 day schools first seen 1916, 86 last seen 1916 (`schools.parquet` first_year/last_year). | Spurious closures/openings; broken spans for the 1920 event study. |
| 4 | **Duplicate rows across overlapping chunks.** | 1913 chunk_05 p.677–681 and chunk_06 p.681–686 share p.681; "Alberni" (out_06) and "Alberni." (out_07). Raw 1913 boarding rows 74 vs 54 schools printed. | Inflated counts unless deduplicated on (year, page, name). `apply_column_maps` takes the max per school-year, which hides but does not fix it. |
| 5 | **No printed-total acceptance test.** The deputy superintendent's narrative prints schools and enrolment per class most years; the family was promoted to observations without being checked against it. | 1910: day 241/6,784, boarding 54/2,229, industrial 20/1,612. 1925 (for 1919–20): day 247/7,477 att 3,516; residential 74/4,719 att 4,133. 1926: 74 residential / 6,327 by denomination. 1928: 77 / 6,795. | Fault 1 went undetected for a week. |
| 6 | **Teacher titles missing on 45% of day-school teacher-years**, and coverage rises 34% → 65% across 1896–1930. Not a data error; a trap. | `teacher_attestations.title` empty on 4,189 of ~10K rows. | Any composition trend over all rows tracks coverage. |
| 7 | (Census family, found the same day) **Roll-up rows in the band population series.** | "West Coast Agency" 71,012; "Mackenzie District" 70,000; recapitulation pools; yearly sums ≈ 2× the Department's totals. | Band-level panels are sound; yearly and provincial sums are not. |

## The fixes, in build order

### Step R1 — page-positional school type (fault 1)
New `build/school_type_map.py`. For each `~/DeptIndianAffairs/markdown/dia_ar_YYYY.md`
walk the `<!-- page N -->` markers and record, per page and character
offset, the type heading in effect:

- 1896–1916: `^(day|boarding|industrial) schools\.?$` sub-headings,
  matched case-insensitively — 1913 prints them as "Boarding Schools." /
  "Industrial Schools." (lines 1197–1264 of `dia_ar_1913.md`), 1928 as
  `DAY SCHOOLS` (line 689). They repeat per province; the most recent one
  governs.
- 1917–1930: `STATEMENT of Indian (Day|Residential|Boarding|Industrial)
  Schools` table titles; "Residential" maps to `residential`, and the
  boarding/industrial distinction is recovered from the school's own earlier
  attestations where it has any (the Department stopped printing it).
- Output `registries/external/school_tables/type_by_page.csv`
  (`year, page, char_lo, type, heading_as_printed`).

In `mint_schools.iter_school_rows`, set `_type` from the map by `(year,
page)` and only fall back to the extractor's field when the map has no
heading for that page. Record both in the attestation (`type_printed`,
`type_extracted`) so the override is auditable. Where a page straddles two
tables, the row order within the page and the heading offset decide.

Acceptance: per year, distinct residential schools with a roll within ±5 of
the printed count (74 in 1910, 74 in 1920, 74 in 1926, 77 in 1928); Lejac,
Alberni, Old Sun's, Onion Lake, Gordon's, Alert Bay, Sechelt, Grouard,
Cross Lake typed residential in the 1920s.

### Step R2 — type is part of the identity key (fault 1, second half)
In `mint_schools.main`, cluster within `(pool, type_class)` where
`type_class ∈ {day, residential}` (boarding and industrial share a class
because the Department merged them in 1923 and names carried over). A day
school and a boarding school of the same name on the same reserve become
two identities linked by a `SAME_RESERVE` edge rather than one. Keep
`TYPE_CHANGED` events only for genuine conversions (a day school that
became a boarding school keeps its identity if no day school of that name
continues alongside it).

Acceptance: Crowfoot, Old Sun's, Sarcee, Onion Lake (R.C.), Gordon's and
Thunderchild each yield one day and one residential identity with
non-overlapping type per year; the 12 Six Nations day schools stay
distinct (school number guard unchanged).

### Step R3 — drop unit rows (fault 2)
In `iter_school_rows`, reject a row whose school name matches
`\b(agency|superintendency|inspectorate|district)\s*$` unless the row's
own type heading is a school heading and the name also carries a school
word. Also tighten `is_school_header` to require `Teacher` or
`Denomination` in the header block, not only roll columns, so the
1897–1900 agency tables (which print roll totals per agency) fail the
gate. Re-run and confirm the 16 identities and 233 rows are gone and
Quebec's residential enrolment is zero before 1930.

### Step R4 — dedup across chunk overlap (fault 4)
Before attestation, drop rows that repeat `(year, page, norm(name),
roll_total)` from a neighbouring chunk; keep the row from the chunk in
which the page is not the first page (the first page of a chunk is the
overlap). Report the count dropped per year; expect ~10–20 per year in
1910–1916.

### Step R5 — 1916 alias pass (fault 3)
Mirror `build/alias_census_bands.py`: for Prairie day schools whose span
ends in 1916 and whose agency/reserve matches one whose span starts in
1916, propose an alias if the name similarity ≥ 0.72 or the reserve
agrees. Write `registries/crosswalks/school_canonical.csv` and a review
file for the rest; never rename an id. Expect most of the 80/86 to pair.

### Step R6 — printed totals as a series and a gate (fault 5)
New `build/harvest_school_totals.py`: regex over `site/reports/*/…` (or the
segmented narrative) for the deputy superintendent's per-class sentences
and tables; write `registries/annotations/school_totals_printed.csv`
(`year, class, n_schools, enrolment, avg_attendance, page`). Then
`eval/compare_school_totals.py`: extracted vs printed per class per year;
print the ratio table; fail the build if any class is outside 0.85–1.15 in
a year with a printed total. Add this to the rebuild order in
`docs/KG_BUILD_PLAN.md` before `apply_column_maps --family school`.

### Step R7 — agency school-age series (new, from the same review)
`build/harvest_school_age.py`: the 1910–16 agency-letter sentence
("Number of children of school age, N; number of pupils enrolled at day
schools, N; average attendance at day schools, N; number attending
<residential school>, N …") → observations on the agency
(`school_age_children`, `enrolled_day`, `att_day`, `attending_residential`
with the named school as a cross-reference). 197 agency-years recovered in
the review with a first-pass regex; the residential tail was captured for
only 9 because tags interrupted the sentence — strip markup before
matching, and match the segmented text rather than the rendered HTML.

### Step R8 — teacher composition guard (fault 6)
No data change. In `link_teachers.py` output, add `title_present` and
document in `docs/KG_BUILD_PLAN.md` §6 that sex or clerical composition
must be computed among titled rows only, with the coverage series shown
beside it.

### Step R9 — census roll-ups (fault 7; separate family, same pattern)
In `mint_bands_from_census.load_rows`, classify a row as `unit` when the
band cell matches `\b(agency|district|inspectorate|superintendency)\b`
with no band word, or when it sits in a "Grand Recapitulation" /
"Inspectorates" province pool; keep them in a `unit_attestations` table
rather than in `band_attestations`. Add the Department's printed grand
totals as the acceptance gate the same way as R6.

## Order and cost
R1 → R2 → R3 → R4 (one regeneration of `schools.parquet`, attestations,
events, review) → R6 as the gate → R5 → `apply_column_maps --family
school` → `gen_wiki` → `index`. R7–R9 are independent. All Claude-side;
roughly a day of work, no cluster time. R1 is the one that changes the
history; do it first and run the R6 comparison before anything else.

## What the review established that does not need fixing
Attendance as a share of roll, computed within school before summing,
agrees with the printed figures (day 47–56%, residential 85–91%) and is
robust to faults 1 and 4. Teacher turnover (39% day / 27% boarding / 19%
industrial; surname-adjusted floors 34 / 21 / 15) stands. The standards
ladder and the industrial trades were not tested and should be checked
against the printed totals once R6 exists.

## Status, 2026-09-02 (end of day)

Done, all Claude-side, no cluster time:

- **R1 page-positional type** — `build/school_type_map.py` →
  `registries/external/school_tables/type_by_page.csv`. Signals, in
  strength order: statement running titles ("STATEMENT of Indian Boarding
  Schools…", "SHOWING the Condition of Indian Day Schools…" 1896–1901,
  "Combined Public and Indian Day Schools"); bare sub-headings; header-row
  shape ("School. Situation. Principal." = industrial, "School. District.
  Teacher." = 1900-era boarding, "School Reserve Agency Principal" =
  residential from 1923) as *weak* signals that never demote a residential
  run; a province-order restart (ONTARIO after BRITISH COLUMBIA) as the
  start of a new statement when OCR lost the title (1919, 1921). Carry
  forward within a chunk sequence only — a page gap resets. Only the
  EXTRACT section counts: the chunk's TABLE HEADER block always repeats the
  first (day) statement's title. 9,909 of 11,236 rows typed from the
  printed heading; the rest take the name's printed-class majority from
  other years, and the extractor's field only when the name has no history.
- **R2 type in the identity key** — `mint_schools.py` clusters within
  `(pool, type_class)`; `schools.parquet` gains `type_class`, attestations
  gain `type_printed` / `type_extracted`. Crowfoot, Old Sun's, Sarcee,
  Onion Lake, Alberni each now have a day and a residential identity.
  Ids are stable: 857 of the 1,115 old ids kept, none with a changed class;
  204 new ids for splits and previously fused schools.
- **R3 unit and junk rows** — 701 rows dropped: "… Agency" rows, the NS
  county tables, medical-officer lists (`JUNK_NAME`), and the year-end
  recapitulation's province rows (`PROVINCE_NAME`, `RECAP_COLS` on the
  header gate).
- **R4 chunk-overlap dedup** — 146 rows.
- **R6 printed-totals gate** — `build/harvest_school_totals.py` →
  `registries/annotations/school_totals_printed.csv` (9 figures so far)
  and the comparison. Result on the rebuilt register:

  | year | class | printed | extracted | ratio | schools p/e |
  |---|---|---|---|---|---|
  | 1910 | day | 6,784 | 6,162 | 0.91 | 241/218 |
  | 1910 | residential | 3,841 | 3,681 | 0.96 | 74/71 |
  | 1926 | residential | 6,327 | 6,153 | 0.97 | 74/69 |
  | 1928 | residential | 6,795 | 6,774 | 1.00 | 77/76 |
  | 1899 | residential | 1,830 | 2,973 | 1.62 | —/51 (open) |

  Residential schools with a roll per year: 66–78 in every year 1900–1930
  except 1904 (0), 1906 (0), 1914 (39) — see below. Orlandini's dataset
  (`registries/external/schools/irs_locations/`, TRC/NCTR list with IRSSA
  spans) has 53–81 operating in the same years, so the counts now agree
  with two independent references.
- **Orlandini crosswalk** — `build/link_schools_irs.py` →
  `registries/crosswalks/schools_irs.csv`: 187 of 258 residential
  identities → 91 of the 145 IRS schools (103 by curated alias, 41 exact,
  23 containment, 20 via the reserve/agency field); `schools_irs_review.csv`
  holds 71 of ours (mostly 1–2-year OCR variants and the early NWT
  missions) and 11 IRS schools open by 1930 (Shingle Point, Aklavik,
  Squamish St. Paul's — alias collision with the Blood St. Paul's — Dunbow,
  Wabasca, Shubenacadie, Cote, Wawanosh).
- Observations regenerated (`--family school`): 386,104 rows total.
  Teachers relinked to the new ids.

Open, in priority order:

1. **1904, 1906, 1914 — pages the chunker never produced.** 1904's
   boarding (p.817) and industrial (p.824) statements fall in the gap
   between chunks p.809 and p.834; 1906 has only p.803–805 of the statement;
   1914 has the boarding statement but not the day title or the industrial
   pages. These need `make_table_chunks.py --family school` with pinned
   page ranges and a short cluster run (three volumes).
2. **1899 residential 1.62× printed.** 51 schools is right (IRS 49); the
   roll is not. Either the 1899 boarding pages carry the day statement's
   duplicate printing (its title appears twice on p.598/599) or "enrolment
   on June 30" is a different measure from the roll. Check before trusting
   1896–1901 rolls at all: 1897 day roll sums to 29,824, 1900 day
   attendance to 38,140, so the early column map is also wrong.
3. **R5 1916 alias pass** — reduced from 80/86 to 60/57 first/last-seen
   in 1916 by the class split; still needs the alias script.
4. **R7 agency school-age series**; **R9 census roll-ups** — untouched.
5. `gen_wiki` + `index` rebuild (`git clean -fdX site/` first).

## Addendum, 2026-09-02 afternoon

- **The "1916 identity break" was not a naming change.** The school
  family's chunks run past the statement into "SCHOOL STATEMENT (cont.)"
  segments that are really the staff list (1916 pp. 336–344: "Gaudin, Mrs.
  A.T.", "Milligan, Silas Indian Agent", "Field Matron"), census
  recapitulations ("Treaty No. 6", 1898 p. 710) and the Indian Land
  Statement (land sales in dollars: "Ouiatchouan 11,604.18", 1897 p. 568;
  "Sharphead", 1900 p. 779). Fixes in `mint_schools.py`: rows on pages
  more than 2 beyond the last page the printed headings could type are
  dropped; the type map emits `end` at "SUMMARY OF SCHOOL STATEMENT" /
  "INDIAN LAND STATEMENT"; rows with cent values and no teacher or
  denomination are dropped; `JUNK_NAME` widened to "Surname, Given" and
  staff titles; `norm()` strips footnote digits glued to names
  ("1Patapun", "2Deer Lake"). Result: 883 rows dropped in all, 900
  identities; day schools first/last seen in 1916 down from 80/86 to
  24/16 (Prairies 8/6), i.e. normal churn. **R5 is closed without an
  alias pass.**
- 1897 day roll 29,824 → 6,280; 1900 day attendance 38,140 → 5,041; 1898
  residential roll 5,924 → 3,723. 1896–1903 now read 51–66 residential
  schools/yr, rolls 2.7–3.7K, consistent with 1905–1913.
- **1899 is a measure difference, not a fault.** The deputy's 1,830 is
  "enrolment … on June 30" (pupils present at year end); the statement's
  roll is cumulative. `harvest_school_totals.py` now tags such sentences
  `point_in_time` and excludes them from the gate, which passes on all
  four remaining class-years (1910 day 0.91, 1910 res 0.96, 1926 res 0.98,
  1928 res 1.00).
- **1904/1906/1914 pinned pages cut and submitted.** `make_census_chunks.py
  --pin --append --out-dir registries/external/school_tables` for
  1904:810-816/817-823/824-827, 1906:806-831/832-838/839-844,
  1914:251-255/256-261 (14 chunks, ~600K chars). Narval is under a week-long
  maintenance reservation (2–9 Sept; job 2250717 cancelled); running on
  **plato** as `5861991` (1904, CHUNKS=7-10), `5861992` (1906, 10-17),
  `5861993` (1914, 6-7). Pull with the scratch `pull_and_rebuild.sh`
  (rsync → type map → mint → column map → IRS crosswalk → teachers → gate).
- **R7 done.** `build/harvest_school_age.py` → `registries/annotations/
  agency_school_age.csv` (210 agency-years parsed, 197 plausible and
  resolved to `AG-` chain ids, 62 agencies, 1910–16) and 671 observations
  under `source_family=agency_letter` (`school_age_children`,
  `enrolled_day_schools`, `avg_attendance_day_schools`,
  `attending_residential`, `not_enrolled`). Day enrolment 39–60% of
  school-age children, present on an average day 18–30%. The named
  residential schools were captured for only 40 agency-years; the tail
  regex stops early — extend it from the segmented text rather than the
  rendered page.
- **R9 done by flags, not re-minting** (band ids are sequential, so a
  re-mint would renumber every crosswalk). `build/flag_band_units.py` →
  `registries/crosswalks/band_unit_flags.csv`: 217 of 3,377 census
  identities are units (122 agency/district/inspectorate rows), treaty
  totals (43, the 1880s "Plain and Wood Crees of Treaty No. 6" rows) or
  recapitulation rows (52, incl. the province totals "Ontario 26,305" that
  the minter had filed under a district agency). `apply_column_maps.py`
  stamps their observations `entity_type="unit"`. Band-typed population
  now sums to 95–106K in every year 1896–1929 (Department: ~100–108K);
  1881–91 read 65–88K because those censuses were not itemised by band.
  Units keep their observations — "Mackenzie District 7,000" is the only
  estimate for the North — they are just not bands.
- **1904 / 1906 / 1914 closed (plato `5861991–3`, 2026-09-02, ~2.5 h on
  the 3g.40gb slice, 14 chunks, 0 hard failures; one 1906 window parsed
  on the thinking-off retry).** Residential schools with a roll: 1904 72,
  1906 73, 1914 69 (Orlandini 63 / 70 / 73). Every year 1896–1930 now has
  51–78 residential schools, day 180–258. Register: 901 identities,
  11,589 attestations; observations 389,963; IRS crosswalk 184/244;
  teachers 10,182 school-years, 3,540 persons; gate passes; wiki and index
  regenerated.
- Still open, none blocking: 1902 (17 residential; the recovered volume's
  OCR is rough throughout); 1927 day statement partial (159 schools);
  1896–97 rolls a little high (day 7.5K/6.3K) — the earliest statements
  have no type titles and mix "pupils enrolled since opening" columns;
  the residential tail of the agency school-age sentence (40 of 197
  agency-years captured).
