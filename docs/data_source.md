# Data source: the dawum.de API

Observed on **2026-09-22** against a snapshot with
`Database.Last_Update = 2026-09-21T07:46:18+02:00` (3956 surveys).

Everything below was read off the actual payload, not from dawum's prose
documentation. Where the documentation and the data disagree, the data wins and
the difference is noted.

## Endpoints

| URL | Purpose | Observed |
| --- | --- | --- |
| `https://api.dawum.de/` | full database, JSON | ~1005 KB raw, **95 KB gzipped** |
| `https://api.dawum.de/last_update.txt` | update check | **25 bytes**, `text/plain` |
| `https://api.dawum.de/newest_surveys.json` | compact: newest poll per institute per parliament | not used by us |

Both endpoints we use return `ETag`, `Last-Modified` and
`Access-Control-Allow-Origin: *`; the full database is served `gzip`-encoded
when `Accept-Encoding` allows it. Server is Apache over HTTP/2.

### Update detection

Three mechanisms are available, in increasing cost:

1. **Conditional GET** on `last_update.txt` using the stored `ETag`
   (`"19-65bf7c558049b"`) — a `304` costs no body at all.
2. **`last_update.txt` body** (25 bytes) compared against our stored value.
3. `Database.Last_Update` inside the full payload — same value, but requires
   downloading everything.

dawum's own documentation recommends (2): comparing `Database.Last_Update` of
your copy against the contents of `last_update.txt`.

**Plan:** conditional GET on `last_update.txt` first; only on a change do we
fetch the full database. That is one small request on a no-op day and two
requests on an update day — comfortably within the brief's politeness budget.

Note that `last_update.txt` and the full database shared the same
`Last-Modified` timestamp in this observation, which is consistent with dawum's
claim that the API is regenerated whenever a survey is entered.

## Top-level structure

Six documented blocks; the payload has **seven** keys:

```
Database      dict   metadata (License, Publisher, Author, Last_Update)
Parliaments   dict   18 entries
Institutes    dict   22 entries
Taskers       dict  116 entries
Methods       dict    6 entries
Parties       dict   26 entries
Surveys       dict 3956 entries
```

Every block except `Database` is a **JSON object keyed by a numeric string ID**
(`"0"`, `"5"`, `"4328"`), not an array. IDs are not contiguous and not
zero-padded.

### `Database`

```json
{
  "License": {
    "Name": "ODC Open Database License",
    "Shortcut": "ODC-ODbL",
    "Link": "https://opendatacommons.org/licenses/odbl/1-0/"
  },
  "Publisher": "dawum.de",
  "Author": "Dipl.-Jur. Philipp Guttmann",
  "Last_Update": "2026-09-21T07:46:18+02:00"
}
```

`Last_Update` is a W3C/ISO 8601 datetime **with a local offset** (`+02:00`),
not UTC. Parse it as an aware datetime; do not assume the offset is stable
across DST.

### Dimension blocks

| Block | Fields | Notes |
| --- | --- | --- |
| `Parliaments` | `Shortcut`, `Name`, `Election` | 16 Länder + `Bundestag` (id `0`) + `Europäisches Parlament` (id `17`) |
| `Institutes` | `Name` | 22 pollsters |
| `Taskers` | `Name` | 116 commissioning clients (media outlets, parties, associations) |
| `Methods` | `Name` | `0 Unbekannt`, `1 Telefonisch`, `2 Persönlich`, `3 Online`, `4 Telefon & Online`, `5 Persönlich & Online` |
| `Parties` | `Shortcut`, `Name` | 26 parties incl. `0 Sonstige` |

### `Surveys`

One entry per poll, keyed by survey ID. Example (`"4328"`):

```json
{
  "Date": "2026-09-20",
  "Survey_Period": { "Date_Start": "2026-09-14", "Date_End": "2026-09-18" },
  "Surveyed_Persons": "1202",
  "Parliament_ID": "0",
  "Institute_ID": "5",
  "Tasker_ID": "3",
  "Method_ID": "4",
  "Results": { "7": 29, "1": 20, "4": 14, "2": 13, "5": 11, "0": 5, "3": 5, "23": 3 }
}
```

Field presence across all 3956 surveys:

| Field | Present | Type | Empty / null |
| --- | --- | --- | --- |
| `Date` | 100.00% | `str` (ISO date) | 0 |
| `Survey_Period.Date_Start` | 100.00% | `str` (ISO date) | 0 |
| `Survey_Period.Date_End` | 100.00% | `str` (ISO date) | 0 |
| `Surveyed_Persons` | 100.00% | `str` of digits | 0 |
| `Parliament_ID` | 100.00% | `str` | 0 |
| `Institute_ID` | 100.00% | `str` | 0 |
| `Tasker_ID` | 100.00% | `str` | 0 |
| `Method_ID` | 100.00% | `str` | 0 |
| `Results` | 100.00% | `dict[str, int \| float]` | 0 |

**This is the single most consequential finding for the modeling phases:**
fieldwork start/end and sample size are present and non-empty for *every*
survey in the database. The brief's fallback ("place the poll at its fieldwork
midpoint; fall back to publication date if fieldwork is missing and flag it")
is currently a dead branch. It should still be implemented and tested — the
guarantee is empirical, not contractual — but no poll needs it today.

## Value ranges and distributions

**Dates.** Publication dates run `2017-01-18 .. 2026-09-20`, matching dawum's
claim of coverage from 2017.

**Fieldwork span** (`Date_End - Date_Start`): min 0, median 4, p95 13, max 74
days. 28 surveys have a single-day fieldwork period.

**Publication lag** (`Date - Date_End`): min 0, median 1, p95 6, max 75 days.

**Sample size** (`Surveyed_Persons`): min 500, p5 1000, median 1412, p95 4000,
max 12297. Always a parseable positive integer; never `0`, never empty.

**Result values.** 29269 values total, `int` (25767) or `float` (3502), range
`0.5 .. 49`. A value is never `0`. Fractional parts are overwhelmingly `.0`
(25767) and `.5` (2745); other tenths occur 800-odd times in total. So the
published rounding grid is mostly 1.0 and 0.5 percentage points, with a small
minority of one-decimal values — relevant for the rounding term in the Phase 3
observation model.

**Share sums.** Per survey: min 98, median 100, max 102.

| Sum | Surveys |
| --- | --- |
| 100 | 3897 |
| 101 | 34 |
| 99 | 19 |
| 98 | 2 |
| 102 | 2 |
| 99.7 / 99.9 | 1 each |

A tolerance of ±2 accepts everything currently in the database; ±1.5 would
quarantine 4 surveys. Recommend ±2 as an error threshold with a warning at ±1.

## Integrity checks

All of the brief's validation rules were run against the full snapshot:

| Rule | Violations |
| --- | --- |
| `Date` parseable | 0 |
| `Survey_Period` dates parseable | 0 |
| `Date_Start <= Date_End` | 0 |
| `Date_End <= Date` (fieldwork ends before publication) | 0 |
| publication date not in the future | 0 |
| `Parliament_ID` / `Institute_ID` / `Tasker_ID` / `Method_ID` resolve | 0 |
| `Results` party IDs resolve | 0 |
| exact duplicate surveys | 0 |
| same parliament + institute + fieldwork end appearing twice | 0 |

The database is, at this moment, fully self-consistent. The validation layer is
therefore about catching *future* upstream regressions, not cleaning current
data — which is an argument for making it strict.

## Gotchas

1. **Key order is not sorted.** The `Surveys` object starts
   `4328, 4326, 4327` and ends `372, 370, 365`. Never rely on JSON key order;
   sort explicitly. Required for idempotent Parquet output.

2. **`CDU/CSU` is three different party IDs.**
   - `1` = `CDU/CSU` — used for `Bundestag` (2664 surveys) and `Europäisches Parlament` (52)
   - `101` = `CDU` — used in all 15 non-Bavarian Länder
   - `102` = `CSU` — used only in `Bayern` (187)

   Any cross-parliament analysis must map these deliberately. For the
   Bundestag-only Phase 3 model this is harmless, but it must not be
   papered over with a generic "normalise party names" step.

3. **A party missing from `Results` does not mean zero.** Since no reported
   value is ever `0`, an absent party has been folded into `Sonstige` by the
   institute. In Bundestag polls: `FDP` is absent from 13 of 2664 surveys and
   `Linke` from 24; `BSW` appears in only 647 (it did not exist earlier).
   Treating absence as `0` would be wrong; treating it as missing-at-random
   would also be wrong, since it correlates with being below the reporting
   threshold. This needs an explicit decision in Phase 3.

4. **`Sonstige` (id `0`) is a residual category, not a party.** It carries the
   remainder that makes shares sum to ~100. A softmax over parties must include
   it, but it must not be given a house effect or a threshold probability.

5. **`Method_ID = 0` ("Unbekannt") dominates the early data**: 100% of 2017
   polls and 85.9% of 2018 polls, then ≤0.5% from 2019 onward. This matches
   dawum's note that the method field was added in November 2022 with values
   backfilled to November 2018. A method effect can only be estimated from
   2019 onward; before that the effect is unidentified, not zero.

6. **Numbers arrive as strings.** `Surveyed_Persons` and every `*_ID` are JSON
   strings; only `Results` values are numeric. Coerce explicitly in the
   pydantic layer rather than relying on incidental behaviour.

7. **Party coverage varies by parliament**, from 9 (Bundestag and most Länder)
   to 15 (Europäisches Parlament). The "known parties per parliament" validation
   rule should be derived from the data and stored as a reviewed config file,
   not hardcoded — a genuinely new party must fail loudly once, then be added.

## Not available from this API

- **Official election results.** There is no result block, and no institute
  named anything like "Wahlergebnis" — `Institutes` contains 22 entries, all of
  them real polling firms. `Parliaments[*].Election` is only the *name* of the
  election ("Bundestagswahl"), not an outcome.

  Phase 2 therefore needs an external ground-truth source. Per the brief, the
  next step is a clearly marked CSV template with a `source_url` column for the
  maintainer to fill in and verify against the Bundeswahlleiterin.

- **Poll-level detail beyond the above**: no margin of error, no weighting
  information, no regional breakdowns, no question wording, no raw N per party.

- **Revision history.** The API exposes only the current state. If dawum
  corrects a survey, the change is visible to us only by diffing our own
  snapshots — which is an additional argument for keeping the `data-raw`
  branch.

## Snapshot storage implications

A full snapshot is **95 KB gzipped**. Publication dates show roughly 300–500
surveys per year, and the API changes only when a survey is entered, so on the
order of 250 changed days per year gives **~24 MB/year** on the `data-raw`
branch. That is comfortable for the foreseeable future; the pruning question in
the README is not urgent.

## Attribution requirements

dawum specifies the wording. Where linking is possible:

> „Daten von dawum.de (Open Database License (ODbL))“ — with links on both
> "dawum.de" and the license name.

Where linking is not possible: `Daten von dawum.de (Open Database License:
odbl.dawum.de)`.

dawum states explicitly that derived databases and collective databases built
on theirs are themselves ODbL, must contain the license text or a link to it in
the database *and its documentation*, and must name and link dawum.de. This
repository's `LICENSE-DATA` and the site's methodology/imprint pages must both
satisfy that.

dawum also disclaims warranty for availability, completeness, correctness and
timeliness of the database.
