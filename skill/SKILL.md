---
name: cec-register-check
description: Check whether a solar inverter, PV panel/module or battery is on the Clean Energy Council (CEC) approved products list, using a bundled copy of the full CER register (about 13,000 listings) with suffix-aware model matching. Gives the listed name, approved-until date, a ready answer for the Solar PV Specialist Report form's "Is the Inverter / Are the Solar Panels on the current CEC approved list" rows, and a formal report sentence. Use this whenever drafting or reviewing a Solar PV Specialist Report, filling in CEC approval answers, or whenever anyone asks if a product is CEC approved, CEC listed, expired, delisted or "on the list", even if they only give a brand and model from a nameplate photo. Use it instead of searching the CEC or CER websites.
---

# CEC register check

The Clean Energy Council publishes lists of approved inverters, PV modules and batteries,
and the Clean Energy Regulator hosts them as CSV files. This skill carries a full copy of
those lists plus a lookup that understands how model numbers are written on nameplates
versus how they are listed. It exists because checking the website by hand was slow and
error-prone: suffixes like `-NI`, `-ADA`, `AUS`, `SC`, `Plus` and `DG`, standards tags like
`(AS4777-2 2020)`, and re-certified models listed twice all cause wrong answers.

Use the lookup rather than your own knowledge or a web search. Listings expire and get
re-certified constantly, and an insurer report needs the exact listed name and date.

## Running a check

Find this skill's folder (the directory holding this SKILL.md) and run, with Python 3:

```bash
python3 <skill-folder>/cec.py check inverter "Sungrow SG5K-D"
python3 <skill-folder>/cec.py check panel "Jinko JKM440N-54HL4R-V" --watts 440
python3 <skill-folder>/cec.py check battery "Sungrow SBR192"
python3 <skill-folder>/cec.py check inverter "Sungrow SH5.0RS" --json   # structured output
```

- Category word first: `inverter`, `panel` or `battery`. Leave it out only if unsure.
- Give the brand and the model exactly as read from the label or the technician's notes.
  The brand helps rule out other makers' models with similar numbers.
- Add the nameplate rating when you have it: `--watts` for panels, `--kw` for inverters,
  `--kwh` for batteries. A mismatch is then flagged, which catches misread labels.
- Check every product separately. A site with a solar inverter and a battery inverter
  needs two inverter checks, and a battery gets its own check too.
- Several products at once: write a CSV with columns `job,kind,model` and run
  `python3 <skill-folder>/cec.py batch products.csv`.
- No packages are needed beyond Python 3. The first run builds a small index in the temp
  folder, which takes a second.

## Using the result in the report

Each check prints these lines:

- **RESULT**: `APPROVED`, `APPROVED - EXPIRING SOON`, `LISTING EXPIRED`,
  `REMOVED FROM LIST` or `NOT LISTED`.
- **listed as**: the exact name on the CEC list. The report quotes this name, because it can
  differ from the nameplate. For example, "SG5K-D" is listed as "SG5K-D-NI (AS4777-2 2020)".
- **approved / approved until**: the listing dates.
- **Form answer**: paste this into the form row "Is the Inverter on the current CEC approved
  list" or "Are the Solar Panels on the current CEC approved list". It already follows the
  house style, for example `Yes – listed as SG5.0RS (AS4777-2 2020), approved until 07/07/2028.`
- **Report sentence**: a formal sentence for the recommendation or findings text, when the
  report needs to explain why equipment can or cannot be reused like for like.
- **register pulled**: the date of the data snapshot.

Then add one line per product to the "Flags for Daniel" section of the draft, in this form:

> **CEC check – inverter:** Sungrow SG5.0RS – APPROVED, listed as "SG5.0RS (AS4777-2 2020)",
> approved 12/04/2022, listed until 07/07/2028. Checked 24 Oct 2026, register pulled 24/10/2026.

### When to raise something with Daniel

Daniel signs these reports, so anything uncertain goes in the flags rather than being
smoothed over in the report text.

- **Expiring soon**: say the listing lapses on the date shown. A report sent after that date
  must change its answer to "No".
- **Probable match** or **MISMATCH**: the model or rating did not line up exactly. Use the
  form answer only if the label photo confirms the listed model, and flag it either way.
- **`[variant: suffix differs]`**: normal, for example a model sold with an `-NI` suffix.
  Quote the listed name and mention the variant in the flag line.
- **NOT LISTED**: check the "Nearest listings" lines first. A listed model in the same family
  with a different rating usually means the model number was misread. If the output says the
  brand has listings but none close to this model, the model is most likely an older one that
  has dropped off; ask for the label photo to be confirmed. If the brand has no products on
  the list at all, the form answer already says so.
- **Brand and rating only** (no full model): the answer is "Unable to confirm". Ask for the
  label photo or the model number in the flags.
- **register pulled more than 30 days ago**: the output warns about this. Add a flag that the
  CEC data needs refreshing before the report goes out.

### Dates: issue date and install date

The form asks about the list as it stands when the report goes out. Run form checks without a
date when the report goes out now. If Daniel says it will go out later, add
`--as-at <issue date>` so the form answer reflects any listing that lapses in between.

When someone asks whether a product was approved when it was installed, add
`--as-at <install date>`. The report sentence then describes the status then and now, and
the form answer still describes today.

### What "NOT LISTED" can and cannot prove

The register holds what the CER file publishes now, which reaches back to about 2021.
Older products that dropped off years ago are simply absent. So "NOT LISTED" proves a product
is not on the current list. It does not prove the product was never approved. For gear older
than about 2021, do not let the report say it was never CEC approved, and say in the flags
that its status at install cannot be confirmed from this register.

### If the tool reports an error

An `ERROR:` line with exit code 3 means the data did not load. Never turn that into a "No".
Run the check again, and if it repeats, flag that the CEC check could not be completed.

## Notes on the data

- `data/inverters.json`, `data/modules.json` and `data/batteries.json` hold every listing,
  including expired ones. A product that drops off the list is kept and marked with the date
  it disappeared. `data/meta.json` records the source file and the date it was pulled.
- Brand names that differ from the manufacturer's legal name are mapped in
  `data/aliases.json`. Examples are Solis for Ginlong, Qcells for Hanwha and Canadian Solar
  for CSI Solar.
- This copy is a snapshot. Daniel refreshes it from his CEC-register GitHub repository and
  re-installs the skill when the data is more than a month old.
