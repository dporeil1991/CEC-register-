# CEC register – instructions for Claude

This repo holds a local copy of the Clean Energy Council (CEC) approved products
lists (inverters/PCE, PV modules, batteries), refreshed from the Clean Energy
Regulator's published CSV files. Use it to answer "is X CEC approved?" for
Daniel's Solar PV Specialist Reports. **Do not web-search the CEC site for
approval status when this repo is available.**

## How to check a product

```bash
python3 cec.py check "<manufacturer and/or model>"          # all three lists
python3 cec.py check "<model>" --as-at 2025-03-14          # status at install/inspection date
python3 cec.py inverters "<model>"   /  modules  /  batteries
python3 cec.py check "<model>" --json                      # structured output
python3 cec.py stats                                       # counts + data age
```

Exit codes: 0 best match LISTED, 2 best match EXPIRED, 1 nothing found / no data.

## Rules for reporting

- Quote the exact listed model string, manufacturer, approval date and expiry
  date from the output. Report wording: "Listed on the CEC approved
  <inverter|PV module|battery> list (approved dd/mm/yyyy, expiry dd/mm/yyyy)".
- For an inspection report, check status **as at the installation date** with
  `--as-at`, and also mention if the listing has since expired.
- EXPIRED means the CEC listing lapsed (no new STCs); it is not itself a
  compliance defect for an existing install. NOT FOUND may mean de-listed,
  never listed, or a different model suffix: show the "nearest listings" and
  say which.
- Never say "approved" without a match. If `cec.py stats` shows data older than
  14 days, say so and run the refresh (see below) or trigger the GitHub Action.
- Model strings often carry a standard tag like `(AS4777-2 2020)`; the search
  ignores it, but quote it in the report.

## Refreshing the data

- GitHub Action `Refresh CEC register` runs Mon/Thu and on `workflow_dispatch`;
  it commits `data/*.json` and `data/raw/*`.
- Locally: `python3 scripts/refresh.py` (needs internet to cer.gov.au).
- Manual file: `python3 scripts/refresh.py --from-file inverters path.csv`.
- Cloud sessions usually cannot reach cer.gov.au; trigger the Action instead
  and `git pull`.

## Layout

- `cec.py` – lookup CLI (stdlib only)
- `scripts/refresh.py` – download + normalise; `data/meta.json` records sources,
  fetch time and the header→field mapping actually used
- `data/<kind>.json` – records: manufacturer, series, model, approval_date,
  expiry_date (ISO), power, power_label, extra (all other source columns)
- `index.html` – static search UI over the same JSON (GitHub Pages ready)
- `tests/` – unit tests with fixture CSVs (`python3 -m unittest discover -s tests`)
