# CEC register

A local, searchable copy of the Clean Energy Council (CEC) approved products
lists for Australia: **inverters / power conversion equipment**, **PV modules**
and **batteries**. Built so approval checks for Solar PV Specialist Reports
take one command instead of a trip to the website.

Data comes from the official copies the Clean Energy Regulator publishes:

- https://cer.gov.au/document_page/cec-approved-inverters
- https://cer.gov.au/document_page/cec-approved-pv-modules
- https://cer.gov.au/document_page/cec-approved-solar-batteries

## Quick start

```bash
python3 cec.py check "Fronius Primo GEN24 5.0"
python3 cec.py check "JKM440N-54HL4R-B" --as-at 14/03/2025   # status at install date
python3 cec.py modules trina 440 --limit 20
python3 cec.py stats
```

Output shows `LISTED`, `EXPIRED` or `NOT FOUND`, with the exact listed model,
approval and expiry dates. Add `--all` to include expired listings, `--json`
for machine-readable output.

## Web UI

`index.html` is a static page that searches the same data. Serve the folder
(`python3 -m http.server`) or enable GitHub Pages on this repo (Settings →
Pages → Deploy from branch → `master` / root) and open it from any device.

## Keeping it fresh

The GitHub Action in `.github/workflows/refresh.yml` downloads the lists twice
a week (and on demand via *Run workflow*) and commits any changes. Because raw
files are committed too, `git log -p data/raw` shows exactly what was added or
de-listed and when.

To refresh locally: `python3 scripts/refresh.py`. To load a file you downloaded
by hand: `python3 scripts/refresh.py --from-file inverters ~/Downloads/x.csv`.

## Notes on status

- **LISTED**: on the current CEC list and the listing has not expired.
- **EXPIRED**: still in the published file but past its expiry date. New STCs
  cannot be claimed; it is not by itself a defect for an existing system.
- **NOT FOUND**: not in the current file. Products the CEC removes disappear
  from the list entirely, so check the git history of `data/raw` if you need
  to prove a product was listed at a past date.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
