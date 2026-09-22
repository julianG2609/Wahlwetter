# Ground truth: official Bundestag election results

The dawum API carries no election results (see [`data_source.md`](data_source.md)),
so Phase 2 onwards needs an external source. This document records where the
numbers come from and how they were checked.

Retrieved 2026-09-22.

## Source

**Die Bundeswahlleiterin / Statistisches Bundesamt (Destatis)**, open data:

- Results: [`btw_ab49_datenbank_ergebnisse.csv`](https://www.bundeswahlleiterin.de/dam/jcr/24d8e745-920d-431a-893a-12805bc7ef40/btw_ab49_datenbank_ergebnisse.csv)
  — "Wahlberechtigte, Wählende, Stimmabgabe und Sitzverteilung bei den
  Bundestagswahlen seit 1949 nach Ländern". One file, every election since 1949,
  including seat distributions.
- Licence: **Datenlizenz Deutschland – Namensnennung – Version 2.0**
  (<https://www.govdata.de/dl-de/by-2-0>). Attribution is required wherever
  these numbers appear, and must be shown on the site alongside the dawum
  attribution.

The site publishes no `robots.txt`; nothing there restricts retrieval, and we
fetch this file at most once per run.

## File structure

Semicolon-separated, UTF-8 with BOM, CRLF line endings, German number format
(`.` as thousands separator, `,` as decimal), and **EN DASH (`–`) meaning "no
value"** rather than an empty field.

Five comment lines, then a three-row header: region, vote type, unit. 138
columns:

| Columns | Content |
| --- | --- |
| 0–2 | `Merkmal/Partei`, `Jahr der Wahl`, `Bemerkungen` |
| 3–6 | Deutschland: Erststimmen/Zweitstimmen as counts, then as percentages |
| 7–70 | the same four columns per Land, 16 Länder |
| 71–78 | Früheres Bundesgebiet und Berlin-West / Neue Länder und Berlin-Ost |
| 79–87 | Deutschland seats: total / constituency / list, with and without "Abgeordnete BE" |
| 88–135 | seats per Land: total, constituency, list |

Rows are either a metric (`Wahlberechtigte`, `Wählende`, `ungültige Stimmen`,
`gültige Stimmen/Sitze insgesamt`) or a party.

## Election dates

The results file carries only the **year**. Backtesting needs the actual day,
so each date was read from a separate official page and is stored in
`ELECTION_DATES` together with the URL and the sentence it came from:

| Election | Date | Read from |
| --- | --- | --- |
| 19. Bundestag | 2017-09-24 | `btw17_kerg.csv` header: "Wahl zum 19. Deutschen Bundestag (24. September 2017)" |
| 20. Bundestag | 2021-09-26 | 2021 results page: "Deutschen Bundestag vom 26. September 2021" |
| 21. Bundestag | 2025-02-23 | 2025 landing page: "Wahltag auf Sonntag, den 23. Februar 2025 , bestimmt (BGBl. 2024 I Nr. 435)" |

## Mapping onto dawum party IDs

`config/party_mapping.json` is a reviewed mapping, not an inferred one.

The significant case is **CDU/CSU**: official results list CDU and CSU
separately, while dawum reports a single `CDU/CSU` (party ID `1`) for the
Bundestag. The two are summed. Because of that, and because the published
percentages are rounded to one decimal, **shares are recomputed from absolute
vote counts** rather than summed from published percentages.

Everything not in the mapping is aggregated into the residual party ID `0`
(`Sonstige`) — which is what institutes do when reporting polls, so the poll
and the result use the same categories.

## Verification

`validate_rows()` enforces four checks, and the CLI refuses to write the table
if any of them fails:

1. computed shares sum to 100 (±0.01)
2. party vote counts sum to the official `gültige Stimmen` total, exactly
3. party seats sum to the official seat total, exactly
4. each recomputed share is within 0.05 of the published percentage — half the
   rounding grid, i.e. the largest difference a correct recomputation can show

Result of those checks against the real file:

| Election | Date | Valid votes | Seats | Shares sum | Seats reconcile |
| --- | --- | --- | --- | --- | --- |
| 2017 | 2017-09-24 | 46,515,492 | 709 | 100.00 | yes |
| 2021 | 2021-09-26 | 46,298,387 | 735 | 100.00 | yes |
| 2025 | 2025-02-23 | 49,649,512 | 630 | 100.00 | yes |

Largest deviation between a recomputed and a published share: **0.05 points**,
exactly half the rounding grid.

## Notes and caveats

- **2021 carries a footnote.** `Bemerkungen` reads "einschl. Wiederholungswahl
  in Teilen Berlins", so the figures are post-repeat-election. The file also
  reports 735 seats in the "einschl. Abgeordnete BE" column and 736 in the
  "ohne Abgeordnete BE" column for that year. We use the former consistently.
  This discrepancy has not been chased down and should be before 2021 is used
  as a seat-allocation test case.
- **2025 had 276 constituency seats, not 299.** Under the current law not every
  constituency winner receives a seat. That is a real feature of the result and
  matters for Phase 4.
- **Only 2017, 2021 and 2025 are extracted**, because those are the elections
  dawum's poll coverage overlaps. The source file goes back to 1949; earlier
  elections would need their own party mapping.
- **Seat counts here are the actual outcome**, including any overhang and
  balance seats under the law in force at the time. They are not what a
  Sainte-Laguë allocation of national vote shares alone would produce, so the
  Phase 4 unit test must reproduce the *method*, not expect these totals to
  fall out of national shares.
