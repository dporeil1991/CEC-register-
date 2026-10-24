# CEC register – instructions for Claude

Local copy of the Clean Energy Council (CEC) approved products lists (inverters/PCE,
PV modules, batteries) from the Clean Energy Regulator's published CSV files, with
a lookup CLI, a jobs log and a static web UI. Use it to answer "is X CEC approved?"
for Daniel's Solar PV Specialist Reports. **Never web-search the CEC site for
approval status when this repo is available.**

## Checking a product

```bash
./cec check inverter "Sungrow SG5K-D" --job 51814          # one product, logged against the job
./cec check panel "RSM108-9-440NDG" --watts 440 --job 51814
./cec check battery "Sungrow SBR192" --job 51911
./cec check inverter "Fronius Primo 5.0-1" --as-at 14/03/2025   # status at the install date
./cec check "Trina 440W"                                     # brand + rating only: lists candidates
./cec check ... --json                                       # structured output
./cec batch jobs.csv --out results.csv                       # columns: job, kind, model[, watts, kw, as_at]
./cec expiring --days 90 --sentences                        # logged products lapsing soon
./cec jobs --job 51814 ; ./cec stats
```

Category word is optional (`inverter|panel|battery`); without it all three lists are
searched. Every check is appended to `data/jobs.csv` unless `--no-log`; always pass
`--job` when the check is for a job. Exit codes: 0 approved, 2 expired/removed, 1 not found.

## Reading the result

- `APPROVED` – current listing. `APPROVED - EXPIRING SOON` – current but lapses within
  6 months: say so to Daniel, because a report issued after the date must change.
- `LISTING EXPIRED` – still in the published file, expiry passed. `REMOVED FROM LIST` –
  vanished from a later pull (date shown). `NOT LISTED` – no current or expired listing;
  the output shows nearest listings with ratings so "same family, different wattage"
  can be judged. If the brand has no products at all, the output says so.
- `[variant: suffix differs]` means the key matched after stripping -NI/-ADA/AUS/SC/
  Plus/DG; quote the listed name, not the nameplate text, in the report.
- A `probable match` or `MISMATCH` flag (wattage/kW differs from the nameplate) means
  confirm the model from the label photo before relying on it.
- Use the **Report sentence** line verbatim in the report. It is written in formal
  insurer tone; do not add commentary about how the match was made.

## Data discipline

- `data/<kind>.json` holds every listing ever seen. Rows are never deleted on
  re-import; missing rows get `delisted_date`. `data/raw/*.csv` are the source files
  (sha256 in `data/meta.json`), so every result traces to a file and pull date.
- Every result prints the pull date. Over 30 days old = stale: say so, then refresh.
- Refresh is manual by design: GitHub Actions → "Refresh CEC register" → Run workflow,
  then `git pull`. Locally `./cec refresh` (needs cer.gov.au access; cloud sessions
  usually do not have it). A file downloaded by hand: `./cec refresh --from-file inverters path.csv`.
- `data/cec.db` is a derived SQLite index (gitignored, rebuilt automatically).
  `data/jobs.csv` and `data/aliases.json` are the record for jobs and brand aliases;
  commit them when they change.
- Brand spelt differently from the manufacturer name? Add it to `data/aliases.json`.

## Layout

- `cec`, `cec.py` – entry points; `ceclib/` – `normalise` (keys, ratings, aliases),
  `match` (ranking), `report` (result + sentence), `store` (SQLite, jobs CSV), `refresh`
  (download, merge, delist), `cli`.
- `index.html` – static search UI over the same JSON (GitHub Pages ready).
- `tests/` – `python3 -m unittest discover -s tests -t .`; `tests/seed_cases.json` are
  products checked by hand for real reports and must keep passing.
