import unittest

from ceclib.normalise import (brand_short, brands_for_manufacturer, extract_kw, extract_watts, model_key,
                              parse_rating_from_query, split_query, split_standard, strip_suffixes)


class StandardsBracket(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_standard("SH8.0RS (AS4777-2 2020)"), ("SH8.0RS", "AS4777-2 2020"))
        self.assertEqual(split_standard("SRP-440-BTD-BG (IEC 61215-2021)"), ("SRP-440-BTD-BG", "IEC 61215-2021"))
        self.assertEqual(split_standard("Primo GEN24 10.0 Plus"), ("Primo GEN24 10.0 Plus", None))
        self.assertEqual(split_standard("1707000-xx-y (Powerwall 3 with Backup Gateway 2)")[1], None)


class ModelKeys(unittest.TestCase):
    def test_awkward_examples(self):
        self.assertEqual(model_key("Primo GEN24 10.0 Plus"), "PRIMOGEN2410.0")
        self.assertEqual(model_key("SH8.0RS (AS4777-2 2020)"), "SH8.0RS")
        self.assertEqual(model_key("RSM108-9-440NDG"), "RSM1089440N")
        self.assertEqual(model_key("X1-MINI-1.5K-G4"), "X1MINI1.5KG4")
        self.assertEqual(model_key("SG5K-D-NI (AS4777-2 2020)"), "SG5KD")
        self.assertEqual(model_key("SG5.0RS-ADA (AS4777-2 2020)"), "SG5.0RS")
        self.assertEqual(model_key("Primo 5.0-1 AUS (AS4777-2 2020)"), "PRIMO5.01")
        self.assertEqual(model_key("Primo 5.0-1 SC"), "PRIMO5.01")

    def test_variants_share_a_key(self):
        self.assertEqual(model_key("SG5K-D"), model_key("SG5K-D-NI (AS4777-2 2020)"))
        self.assertEqual(model_key("JKM440N-54HL4R-V"), model_key("JKM440N-54HL4R-V (IEC 61215-2021)"))
        self.assertNotEqual(model_key("SG5.0RS"), model_key("SH5.0RS"))
        self.assertNotEqual(model_key("JAM60S10-330/PR/1000V"), model_key("JAM60S10-330/MR/1000V"))

    def test_suffix_strip_is_safe(self):
        self.assertEqual(strip_suffixes("SRP-440-BTD-BG"), "SRP-440-BTD-BG")  # BG is not DG
        self.assertEqual(strip_suffixes("DG"), "DG")  # never strip to nothing
        self.assertEqual(strip_suffixes("Powerwall 3"), "Powerwall 3")


class Ratings(unittest.TestCase):
    def test_watts(self):
        for model, w in [("RSM108-9-440NDG", 440), ("TP6L60M-370", 370), ("JAM60S10-330/PR/1000V", 330), ("YL250P-29b", 250),
                         ("EG-265P60-C", 265), ("JKM415N-54HL4-B", 415), ("1KOMMA5°-Fullblack-01-425", 425), ("TSM-DE09.08-390", 390)]:
            self.assertEqual(extract_watts(model), w, model)

    def test_kw(self):
        for model, kw in [("SG5K-D", 5.0), ("Primo 5.0-1", 5.0), ("X1-MINI-1.5K-G4", 1.5), ("SH8.0RS", 8.0),
                          ("GW5000-DNS-30", 5.0), ("SUN2000-5KTL-L1", 5.0), ("Primo GEN24 10.0 Plus", 10.0), ("TLC20K", 20.0)]:
            self.assertEqual(extract_kw(model), kw, model)

    def test_query_ratings(self):
        self.assertEqual(parse_rating_from_query("Jinko JKM440N 440W"), ("Jinko JKM440N", {"watts": 440}))
        self.assertEqual(parse_rating_from_query("SG5.0RS 5kW inverter"), ("SG5.0RS inverter", {"kw": 5.0}))
        self.assertEqual(parse_rating_from_query("Powerwall 3 13.5kWh"), ("Powerwall 3", {"kwh": 13.5}))
        # a rating inside the model number is left alone
        self.assertEqual(parse_rating_from_query("TP6L60M-370")[0], "TP6L60M-370")


class Brands(unittest.TestCase):
    def test_aliases(self):
        self.assertIn("Solis", brands_for_manufacturer("Ginlong Technologies Co Ltd"))
        self.assertIn("Qcells", brands_for_manufacturer("Hanwha Q CELLS Australia pty ltd"))
        self.assertIn("EGing", brands_for_manufacturer("Changzhou EGing Photovoltaic Technology Co Ltd"))
        self.assertIn("LONGi", brands_for_manufacturer("LONGi Green Energy Technology Co Ltd"))
        self.assertIn("Jinko", brands_for_manufacturer("Jinko Solar Co Ltd"))
        self.assertIn("Energizer", brands_for_manufacturer("8 Star Energy Pty Ltd"))

    def test_brand_short(self):
        self.assertEqual(brand_short("Sungrow Power Supply Co Ltd"), "Sungrow")
        self.assertEqual(brand_short("Fronius Australia Pty Ltd"), "Fronius")
        self.assertEqual(brand_short("Tesla Inc"), "Tesla")

    def test_split_query(self):
        q = split_query("Sungrow SG5K-D")
        self.assertEqual((q["brands"], q["model"]), ({"Sungrow"}, "SG5K-D"))
        q = split_query("JA Solar JAM60S10-330/PR/1000V 330W")
        self.assertEqual((q["brands"], q["model"], q["ratings"]), ({"JA Solar"}, "JAM60S10-330/PR/1000V", {"watts": 330}))
        q = split_query("Hanwha Q CELLS Q.PEAK DUO")
        self.assertEqual((q["brands"], q["model"]), ({"Qcells"}, "Q.PEAK DUO"))
        q = split_query("Fronius Primo GEN24 10.0 Plus", ["Solar N Plus New Energy Technology Co LTD", "Fronius Australia Pty Ltd"])
        self.assertEqual(q["model"], "Primo GEN24 10.0 Plus")  # 'Plus' is a model word, not a brand
        q = split_query("Yingli YL250P-29b", ["Yingli Energy Development Co Ltd"])
        self.assertEqual((q["brands"], q["model"]), ({"Yingli"}, "YL250P-29b"))
        q = split_query("Pytes V10", ["Shanghai PYTES Energy Co Ltd"])
        self.assertEqual((q["brands"], q["model"]), ({"Shanghai PYTES"}, "V10"))
        q = split_query("Trina 440W")
        self.assertEqual((q["brands"], q["model"], q["ratings"]), ({"Trina"}, "", {"watts": 440}))


if __name__ == "__main__":
    unittest.main()
