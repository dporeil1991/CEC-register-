import unittest
from pathlib import Path

from ceclib import refresh

FIX = Path(__file__).resolve().parent / "fixtures"


class Parsing(unittest.TestCase):
    def test_inverter_headers(self):
        rows = refresh.rows_from_csv_text((FIX / "inverters.csv").read_text())
        norm, mapping = refresh.normalise(rows, "inverters")
        self.assertEqual(mapping["model"], "Model Number")
        self.assertEqual(mapping["expiry_date"], "Expiry date")
        self.assertEqual(norm[0]["approval_date"], "2026-04-16")
        self.assertAlmostEqual(norm[0]["power"], 9.999)

    def test_module_headers(self):
        rows = refresh.rows_from_csv_text((FIX / "modules.csv").read_text())
        norm, mapping = refresh.normalise(rows, "modules")
        self.assertEqual(mapping["manufacturer"], "Licensee/Certificate Holder")
        self.assertNotIn("power", mapping)
        self.assertEqual(norm[2]["approval_date"], "2023-03-03")
        self.assertEqual(norm[0]["extra"]["Fire Tested"], "Yes")

    def test_battery_headers(self):
        rows = refresh.rows_from_csv_text((FIX / "batteries.csv").read_text())
        norm, mapping = refresh.normalise(rows, "batteries")
        self.assertEqual(mapping["power"], "Nominal Battery Capacity (kWh)")
        self.assertEqual(norm[0]["extra"]["Brand Name"], "Energizer")

    def test_dates_and_preamble(self):
        self.assertEqual(refresh.parse_date("31/12/2027"), "2027-12-31")
        self.assertEqual(refresh.parse_date("5 Mar 2025"), "2025-03-05")
        self.assertIsNone(refresh.parse_date(""))
        rows = refresh.rows_from_csv_text("Title row\n\nManufacturer,Model Number,Expiry Date\nAcme,X1,01/01/2030\n")
        self.assertEqual(rows[0]["Model Number"], "X1")
        with self.assertRaises(ValueError):
            refresh.rows_from_bytes(b"<html><body>login</body></html>", "x")


def rec(man, model, appr="2024-01-01", exp="2027-01-01"):
    return {"manufacturer": man, "series": "", "model": model, "approval_date": appr, "expiry_date": exp,
            "power": None, "power_label": "", "extra": {}}


class MergeNeverDeletes(unittest.TestCase):
    def test_delist_and_revive(self):
        first, s1 = refresh.merge([], [rec("Acme", "A1"), rec("Acme", "A2")], "inverters", "2026-01-01", "raw/a.csv", "t1")
        self.assertEqual(s1["added"], 2)
        self.assertEqual([r["id"] for r in first], ["inv-1", "inv-2"])
        # A2 disappears from the next pull: kept, stamped, not deleted
        second, s2 = refresh.merge(first, [rec("Acme", "A1", exp="2028-01-01")], "inverters", "2026-02-01", "raw/a.csv", "t2")
        self.assertEqual(len(second), 2)
        a2 = next(r for r in second if r["model"] == "A2")
        self.assertEqual(a2["delisted_date"], "2026-02-01")
        self.assertEqual(a2["last_seen"], "2026-01-01")
        a1 = next(r for r in second if r["model"] == "A1")
        self.assertEqual((a1["expiry_date"], a1["first_seen"], a1["last_seen"]), ("2028-01-01", "2026-01-01", "2026-02-01"))
        self.assertEqual(s2["delisted_now"], 1)
        self.assertEqual(s2["dates_changed"], 1)
        # A2 comes back: revived, same id
        third, s3 = refresh.merge(second, [rec("Acme", "A1"), rec("Acme", "A2")], "inverters", "2026-03-01", "raw/a.csv", "t3")
        a2 = next(r for r in third if r["model"] == "A2")
        self.assertIsNone(a2["delisted_date"])
        self.assertEqual(a2["id"], "inv-2")
        self.assertEqual(s3["revived"], 1)

    def test_identity_ignores_case_and_whitespace_only(self):
        first, _ = refresh.merge([], [rec("Acme Co Ltd", "SG5.0RS (AS4777-2 2020)")], "inverters", "2026-01-01", "f", "t")
        second, s = refresh.merge(first, [rec("ACME CO LTD", "SG5.0RS  (AS4777-2 2020)")], "inverters", "2026-02-01", "f", "t")
        self.assertEqual((len(second), s["added"], s["delisted_now"]), (1, 0, 0))

    def test_plus_and_recertification_are_distinct_rows(self):
        rows = [rec("Hanwha", "Q.MAXX-G4 410"), rec("Hanwha", "Q.MAXX-G4+ 410"),
                rec("Sungrow", "SH5.0RS (AS4777-2 2020)", "2021-12-15", "2025-01-29"),
                rec("Sungrow", "SH5.0RS (AS4777-2 2020)", "2025-01-30", "2028-01-30")]
        merged, s = refresh.merge([], rows, "inverters", "2026-01-01", "f", "t")
        self.assertEqual((len(merged), s["added"]), (4, 4))
        # next pull drops the old SH5.0RS certification only
        merged2, s2 = refresh.merge(merged, [rows[0], rows[1], rows[3]], "inverters", "2026-02-01", "f", "t")
        self.assertEqual((len(merged2), s2["delisted_now"], s2["added"]), (4, 1, 0))
        gone = [r for r in merged2 if r["delisted_date"]]
        self.assertEqual((gone[0]["model"], gone[0]["approval_date"]), ("SH5.0RS (AS4777-2 2020)", "2021-12-15"))

    def test_mixed_encoding(self):
        body = "Manufacturer,Model Number\n".encode() + b"1KOMMA5\xb0 Technology GmbH,X\n" + "Pytes,V10α\n".encode("utf-8")
        rows, fmt = refresh.rows_from_bytes(body, "x")
        self.assertEqual([r["Model Number"] for r in rows], ["X", "V10α"])
        self.assertEqual(rows[0]["Manufacturer"], "1KOMMA5° Technology GmbH")


if __name__ == "__main__":
    unittest.main()
