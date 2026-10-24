# CEC register

A local, searchable copy of the Clean Energy Council (CEC) approved products lists for
Australia: **inverters / power conversion equipment**, **PV modules** and **batteries**,
with a lookup tool that answers in insurer-report language and keeps a log per job.

Data comes from the official copies the Clean Energy Regulator publishes:

- https://cer.gov.au/document_page/cec-approved-inverters
- https://cer.gov.au/document_page/cec-approved-pv-modules
- https://cer.gov.au/document_page/cec-approved-solar-batteries

## Quick start

```bash
./cec check inverter "Sungrow SG5K-D" --job 51814
./cec check panel "Jinko JKM440N-54HL4R-V" --watts 440 --job 51814
./cec check "Fronius Primo 5.0-1" --as-at 14/03/2025     # status at the install date
./cec batch jobs.csv --out results.csv                    # job, kind, model[, watts, kw, as_at]
./cec expiring --days 90 --sentences                     # anything in the log lapsing soon
./cec jobs            ./cec stats            ./cec refresh
```

A check prints the result, the exact listed name, approval and approved-until dates,
the register pull date (with a warning past 30 days), a ready-to-paste report sentence,
and the nearest listings with their ratings when there is no exact hit.

```
Sungrow SG5K-D   ·   inverter   ·   status as at 24/10/2026
  Inverter  register pulled 24/10/2026 (0d ago), 4850 listings, source inverters.csv

RESULT: LISTING EXPIRED
  listed as:       SG5K-D-NI (AS4777-2 2020)   [variant: suffix differs]
  manufacturer:    Sungrow Power Supply Co Ltd
  rating:          4.999 kW
  approved:        02/08/2022      approved until: 02/08/2025
  checked:         24/10/2026

Report sentence:
  The Sungrow SG5K-D inverter is not currently listed on the CEC approved products list
  (the listing for "SG5K-D-NI (AS4777-2 2020)" expired on 2 August 2025; checked 24 October 2026).
```

Results: **Approved**, **Approved, expiring within 6 months**, **Listing expired** (date),
**Removed from the list** (date it vanished from a pull), **Not listed**.

## How matching works

- Model numbers are reduced to a key: standards bracket removed (`(AS4777-2 2020)`,
  `(IEC 61215-2021)`), known suffixes removed (`-NI`, `-ADA`, `AUS`, `SC`, `Plus`, `DG`),
  spaces/hyphens/case ignored. The listed text is always kept and quoted.
- Brand words are recognised through `data/aliases.json` (Solis = Ginlong, Qcells =
  Hanwha Q CELLS, EGing = Changzhou EGing, ...). Add your own lines there.
- Wattage / kW on the nameplate (`--watts 440`, `--kw 5`, or just `440W` in the text) is a
  secondary key: it flags a mismatch and, with a brand alone, lists that brand's
  products at that rating.
- Nothing exact: ranked near-matches with similarity scores and ratings are shown.

## Data model

- `data/inverters.json`, `data/modules.json`, `data/batteries.json` – one row per listing
  ever seen: manufacturer, model as listed, normalised key, standard, approved date,
  approved-until date, first/last seen, `delisted_date`, source file and pull stamp.
  Rows are never deleted on re-import; a row missing from a new pull is marked delisted.
- `data/raw/*.csv` – the CER files as downloaded (sha256 recorded in `data/meta.json`).
- `data/jobs.csv` – every check: job, model, result, approved-until, date checked.
- `data/aliases.json` – manufacturer aliases.
- `data/cec.db` – SQLite index rebuilt automatically from the files above (not committed).

## Keeping it fresh

Refresh is manual. On GitHub: Actions → *Refresh CEC register* → *Run workflow*; it
downloads the three lists, merges them, runs the tests and commits. Locally:
`./cec refresh`, or `./cec refresh --from-file modules ~/Downloads/file.csv` for a file
you downloaded yourself. `git log -p data/raw` shows exactly what changed between pulls.

## Skill for everyday Claude

`dist/cec-register-check.skill` packages the same lookup and a data snapshot as a Claude
skill, so the Claude that drafts reports can run checks itself. It fills the form rows
("Yes – listed as …, approved until …"), writes the flag lines and the formal sentence.

- Install: download the file and add it in Claude under Settings → Capabilities → Skills.
- Update: each run of the refresh workflow rebuilds the file with the new data. Download it
  again and re-upload it when the skill reports data older than 30 days.
- Build by hand: `python3 scripts/build_skill.py`. The instructions live in `skill/SKILL.md`.

## Web UI

`index.html` searches the same data in a browser. Serve the folder (`python3 -m
http.server`) or enable GitHub Pages (Settings → Pages → Deploy from branch → `master`, root).

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```

`tests/seed_cases.json` holds products checked by hand for real reports and the answers
reached; the suite fails if the tool ever disagrees with them.
