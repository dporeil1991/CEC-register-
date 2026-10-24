"""Unit tests for the normaliser and the lookup CLI, run against the fixture CSVs."""
import datetime as dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


refresh = _load("refresh", ROOT / "scripts" / "refresh.py")
cec = _load("cec", ROOT / "cec.py")


class NormaliseTests(unittest.TestCase):
    def test_inverter_headers_map(self):
        rows = refresh.rows_from_csv_text((FIX / "inverters.csv").read_text())
        norm, mapping = refresh.normalise(rows, "inverters")
        self.assertEqual(mapping["model"], "Model Number")
        self.assertEqual(mapping["expiry_date"], "Expiry date")
        self.assertEqual(norm[0]["approval_date"], "2026-04-16")
        self.assertEqual(norm[0]["expiry_date"], "2027-12-31")
        self.assertAlmostEqual(norm[0]["power"], 9.999)

    def test_module_headers_map(self):
        rows = refresh.rows_from_csv_text((FIX / "modules.csv").read_text())
        norm, mapping = refresh.normalise(rows, "modules")
        self.assertEqual(mapping["manufacturer"], "Licensee")
        self.assertEqual(mapping["approval_date"], "CEC Approved Date")
        self.assertEqual(norm[0]["power"], 440)
        self.assertEqual(norm[0]["extra"]["Fire Tested"], "Yes")

    def test_dates(self):
        self.assertEqual(refresh.parse_date("31/12/2027"), "2027-12-31")
        self.assertEqual(refresh.parse_date("2027-12-31"), "2027-12-31")
        self.assertEqual(refresh.parse_date("5 Mar 2025"), "2025-03-05")
        self.assertIsNone(refresh.parse_date(""))

    def test_preamble_rows_skipped(self):
        text = "CEC approved inverters\nGenerated 1/1/2026\n\nManufacturer,Model Number,Expiry Date\nAcme,X1,01/01/2030\n"
        rows = refresh.rows_from_csv_text(text)
        self.assertEqual(rows[0]["Model Number"], "X1")

    def test_html_rejected(self):
        with self.assertRaises(ValueError):
            refresh.rows_from_bytes(b"<html><body>login</body></html>", "x")


class LookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name)
        refresh.DATA, refresh.RAW = data, data / "raw"
        cec.DATA = data
        refresh.main(["--from-file", "inverters", str(FIX / "inverters.csv"),
                      "--from-file", "modules", str(FIX / "modules.csv"),
                      "--from-file", "batteries", str(FIX / "batteries.csv")])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exact_match_ignores_standard_suffix(self):
        res = cec.search("SG5.0RS", ("inverters",), dt.date(2023, 6, 15), False, 5)
        self.assertEqual(res[0]["model"], "SG5.0RS (AS4777-2 2020)")
        self.assertEqual(res[0]["score"], 1.0)
        self.assertEqual(res[0]["status"], "LISTED")

    def test_expired_status_as_at_today(self):
        res = cec.search("SG5.0RS", ("inverters",), dt.date(2026, 10, 24), False, 5)
        self.assertEqual(res[0]["status"], "EXPIRED")

    def test_tokens_match_brand_and_model(self):
        res = cec.search("fronius gen24 5.0", cec.KINDS, dt.date(2026, 10, 24), False, 5)
        self.assertEqual(res[0]["manufacturer"], "Fronius International GmbH")
        self.assertEqual(res[0]["kind"], "inverters")

    def test_not_found(self):
        self.assertEqual(cec.search("Huawei SUN2000-5KTL", cec.KINDS, dt.date(2026, 10, 24), False, 5), [])

    def test_cli_exit_codes(self):
        self.assertEqual(cec.main(["check", "JKM440N-54HL4R-B"]), 0)
        self.assertEqual(cec.main(["inverters", "Primo 5.0-1", "--all"]), 2)
        self.assertEqual(cec.main(["check", "nothing-like-this-xyz"]), 1)

    def test_json_output_shape(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cec.main(["check", "Powerwall 3", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["results"][0]["kind"], "batteries")
        self.assertEqual(out["results"][0]["status"], "LISTED")


if __name__ == "__main__":
    unittest.main()
