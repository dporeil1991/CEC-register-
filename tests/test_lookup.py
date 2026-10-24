"""Matching, classification and CLI behaviour, using a small register built from the fixture CSVs."""
import contextlib
import csv
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from ceclib import cli, match, refresh, report, store

FIX = Path(__file__).resolve().parent / "fixtures"
TODAY = dt.date(2026, 10, 24)


class FixtureRegister(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name)
        cls._saved = (refresh.DATA, refresh.RAW, store.DATA, store.DB_PATH, store.JOBS_CSV)
        refresh.DATA, refresh.RAW = data, data / "raw"
        store.DATA, store.DB_PATH, store.JOBS_CSV = data, data / "cec.db", data / "jobs.csv"
        (data / "aliases.json").write_text((store.ROOT / "data" / "aliases.json").read_text())
        refresh.main(["--from-file", "inverters", str(FIX / "inverters.csv"),
                      "--from-file", "modules", str(FIX / "modules.csv"),
                      "--from-file", "batteries", str(FIX / "batteries.csv")])
        cls.conn = store.connect()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        refresh.DATA, refresh.RAW, store.DATA, store.DB_PATH, store.JOBS_CSV = cls._saved
        cls.tmp.cleanup()

    def check(self, q, kind=None, as_at=TODAY):
        return cli.do_check(self.conn, q, kind, as_at, TODAY, 6)

    def test_exact_ignores_standard_bracket(self):
        r = self.check("Sungrow SG5.0RS", "inverters", dt.date(2023, 6, 15))
        self.assertEqual((r["result"], r["listed_name"], r["match_type"]), ("APPROVED", "SG5.0RS (AS4777-2 2020)", "exact"))

    def test_variant_suffix(self):
        r = self.check("SG5.0RS-ADA", "inverters")
        self.assertEqual(r["listed_name"], "SG5.0RS-ADA (AS4777-2 2020)")
        r = self.check("Sungrow SG5.0RS", "inverters")
        self.assertEqual(r["result"], "EXPIRED")  # fixture SG5.0RS expired 2024

    def test_relisted_prefers_current(self):
        r = self.check("Jinko JKM440N-54HL4R-B", "modules")
        self.assertEqual((r["result"], r["listed_name"]), ("APPROVED", "JKM440N-54HL4R-B (IEC 61215-2021)"))
        self.assertEqual(r["near"][0]["status"], "EXPIRED")

    def test_wrong_brand_is_penalised(self):
        r = self.check("Fronius SG5.0RS", "inverters")
        self.assertNotEqual(r["confidence"], "confident")

    def test_wattage_mismatch_flagged(self):
        r = self.check("Jinko JKM440N-54HL4R-B 415W", "modules")
        self.assertEqual(r["rating_check"], "mismatch")

    def test_brand_and_rating_only(self):
        r = self.check("Trina 440W", "modules")
        self.assertEqual(r["result"], "NOT_LISTED")
        self.assertTrue(all(n["match_type"] == "brand-rating" for n in r["near"]))
        self.assertIn("without the full model number", r["sentence"])

    def test_not_listed_brand_absent(self):
        r = self.check("Samil Power SR2K8TLA1", "inverters")
        self.assertEqual(r["result"], "NOT_LISTED")
        self.assertIn("no Samil Power inverters appear", r["sentence"])

    def test_sentences(self):
        r = self.check("Fronius Primo 5.0-1", "inverters")
        self.assertEqual(r["result"], "EXPIRED")
        self.assertEqual(r["sentence"], 'The Fronius Primo 5.0-1 inverter is not currently listed on the CEC approved products list '
                                        '(the listing for "Primo 5.0-1 AUS (AS4777-2 2020)" expired on 14 March 2022; checked 24 October 2026).')
        r = self.check("Fronius Primo 5.0-1", "inverters", dt.date(2020, 1, 1))
        self.assertTrue(r["sentence"].startswith("As at 1 January 2020, the Fronius Primo 5.0-1 inverter was listed"))
        self.assertIn("has since expired on 14 March 2022", r["sentence"])
        r = self.check("Tesla Powerwall 3", "batteries")
        self.assertEqual(r["result"], "APPROVED")
        self.assertIn("with approval valid until 17 October 2027 (checked 24 October 2026).", r["sentence"])

    def test_delisted_status(self):
        rows = json.loads((store.DATA / "inverters.json").read_text())
        rows[-1]["delisted_date"] = "2026-09-01"
        gone = rows[-1]["model"]
        (store.DATA / "inverters.json").write_text(json.dumps(rows))
        conn = store.connect()
        r = cli.do_check(conn, gone, "inverters", TODAY, TODAY, 3)
        self.assertEqual(r["result"], "DELISTED")
        self.assertIn("was removed from the list on or about 1 September 2026", r["sentence"])
        conn.close()
        rows[-1]["delisted_date"] = None
        (store.DATA / "inverters.json").write_text(json.dumps(rows))
        type(self).conn = store.connect()

    def test_cli_check_logs_and_exit_codes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["check", "battery", "Tesla", "Powerwall", "3", "--job", "T1", "--kwh", "14.4"])
        self.assertEqual(code, 0)
        self.assertIn("RESULT: APPROVED", buf.getvalue())
        rows = store.read_jobs()
        self.assertEqual((rows[-1]["job"], rows[-1]["result"], rows[-1]["listed_name"]), ("T1", "APPROVED", "1707000-10 kVA (AS4777-2 2020)"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["check", "inverter", "Sungrow", "SG5.0RS", "--no-log"]), 2)
            self.assertEqual(cli.main(["check", "inverter", "Samil", "SR2K8TLA1", "--no-log"]), 1)

    def test_batch_and_expiring(self):
        inp = store.DATA / "batch.csv"
        inp.write_text("job,kind,model,as_at\nJ1,inverter,Sungrow SG5.0RS,15/06/2023\nJ2,battery,Tesla Powerwall 3,\nJ3,panel,Jinko JKM440N-54HL4R-B,\n")
        out = store.DATA / "out.csv"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["batch", str(inp), "--out", str(out)])
        res = list(csv.DictReader(out.open()))
        self.assertEqual([r["result"] for r in res], ["APPROVED", "APPROVED", "APPROVED"])
        self.assertEqual(res[0]["as_at"], "2023-06-15")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["expiring", "--days", "300", "--sentences"])
        text = buf.getvalue()
        self.assertIn("J1", text)  # SG5.0RS expired since the as-at check
        self.assertIn("J3", text)  # Jinko lapses June 2027 (247 days), inside 300
        self.assertNotIn("J2 ", text)  # Powerwall until Oct 2027 (358 days) is outside


class SeedCases(unittest.TestCase):
    """Products checked by hand for real reports: the tool must reach the same answers."""

    @classmethod
    def setUpClass(cls):
        cls.seed = json.loads((Path(__file__).resolve().parent / "seed_cases.json").read_text())
        if not (store.DATA / "inverters.json").exists():
            raise unittest.SkipTest("register data not present")
        cls.conn = store.connect()

    def test_seed_cases(self):
        today = dt.date.fromisoformat(self.seed["today"])
        failures = []
        for c in self.seed["cases"]:
            r = cli.do_check(self.conn, c["query"], c["kind"], today, today, 6)
            got = {"result": r["result"], "listed_name": r["listed_name"], "approved_until": r["approved_until"],
                   "match_type": r["match_type"], "manufacturer": r["manufacturer"]}
            for k in ("result", "listed_name", "approved_until", "match_type", "manufacturer"):
                if k in c and c[k] != got[k]:
                    failures.append(f"{c['job']} {c['query']}: {k} expected {c[k]!r}, got {got[k]!r}")
            if c.get("brand_absent"):
                bir = r.get("brand_in_register") or {}
                if any(bir.values()):
                    failures.append(f"{c['job']} {c['query']}: expected no {r['brand']} products in register")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
