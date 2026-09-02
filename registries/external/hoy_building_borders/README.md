# Ben Hoy — "Building Borders" HGIS datasets (assessed 2026-08-30)

Source: `G:\HGIS LAB\DataLibrary\Building Boarders Data\` (USask HGIS Lab network
drive; readable from WSL at `/mnt/g/...`). Human-built, from published maps and
the printed Departmental reports — i.e. **an independent witness on the same
sources Canada50 extracts by machine**.

Copied here (attributes only — the polygons are rough, digitised from topo maps,
and the user asked that we not rely on their geometry):

| file | from | rows |
|---|---|---|
| `reserves_1875-1906_attributes.csv` | `Canada Indigenous Reserves 1875-1906.gdb`, 18 layers | 1,265 (854 named, 291 distinct names) |
| `dia_employees_1875-1915.csv` | `Canada Indian Agents 1875-1915.gdb`, 18 layers | 3,894 |

## What they contain

**Reserves** — one layer per source map (`PCA_Map573`, `Map B17_1_S`, …), fields
`Name, Type, Map_Name, Map_Date, Population, Reserve_No`. Map dates 1875, 1878,
1885, 1886, 1888, 1889, 1891, 1894, 1904, 1906. Population on 422 rows (1888,
1889, 1904 only); reserve number on 183.

**DIA employees** — counts of departmental staff by post, for 1875, 1880, 1885,
1890, 1895, 1900, 1905, 1910, 1915, in two cuts: `by_post` (`N_Emp` per location)
and `by_occupation` (`N_Emp` per location × `Occ`), each with `Lat`/`Long` and
the province wording of the year. Derived from the same Return A / "Officers and
Employees" lists this project extracts (`eval/results/officers*`).

## Verdict: a validation set and a geocoding source, not a gold standard

Coverage is far smaller than ours (291 reserve names vs 1,422 reserves in the
1902 Schedule; 164 population points vs 19,700 census observations), so it
cannot stand in for our extraction. Where it overlaps, it is an excellent check:

- **Reserve numbers**: 40 of 47 name-matched reserves agree exactly (Blood 148,
  Peigan 147, Sarcee 145, Bobtail 139, Samson 137, Alexis 133 …). The
  disagreements are blank fields in Hoy or genuine name collisions (several
  "Whitefish Lake" / "Duck Lake" reserves).
- **Populations**: of 17 bands matched on (name, year), 9 agree within 5% and 13
  within 20%; matches tighten when Hoy's map date is read as one year *after*
  the reporting year (a map dated 1904 carries 1903 figures). Outliers —
  Saddle Lake 243 vs 135, Whitefish Lake 331 vs 157 — look like agency totals
  against band figures, worth a historian's eye.
- **DIA employees**: our officers extraction matches Hoy's totals closely for
  1880 (110 vs 121, 0.91), 1890 (368 vs 374, **0.98**) and 1910 (481 vs 485,
  **0.99**) — but falls badly short for **1885 (79 vs 196)** and **1895 (303 vs
  602)**. Our chunk coverage for the short years is 2 chunks / ~20 KB against
  7–11 chunks / 54–84 KB in the years that match, so this is a *chunking* gap in
  `make_table_chunks.py --family officers`, not a model failure. Same signature
  for 1882 (46 rows), 1883 (63), 1886 (79), 1892 (102), 1893 (90).

## Uses

1. Re-cut the officers family for 1882, 1883, 1885, 1886, 1892, 1893, 1895 with
   pinned page ranges and re-run; use Hoy's per-year totals as the acceptance test.
2. Geocode DIA posts and agencies from `Lat`/`Long` (`Loc`, `Loc_Mer`).
3. Independent check on reserve numbers and on band populations for 1888/89/1904.
4. Map provenance: `Map_Name` records which historical map each reserve came from.

Other geodatabases in the same folder, not yet examined: Canada Historic Place
Names 1875-1904, Canada Historic Trails, Canada Indigenous Settlements
1875-1888, Canada Whereabouts Census 1881-1893, Canada NWMP posts/patrols,
Canada Customs Employees 1885-1915 (that last one belongs to the trade session).
