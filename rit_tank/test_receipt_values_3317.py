import unittest
from receipt_recognition import recognize_receipt_fields

class ArgosValuesTests(unittest.TestCase):
    def test_complete_receipt(self):
        text = """Shop
Klik Services BV, ARGOS
Reggesingel 40, 7461 AK Rijssen
Brandstofleverancier
Varo Energy Retail BV
Wilhelminakade 919, 3072 AP Rotterdam
KLANTENBON
*95 Ongelood
(POMP 4; 24.70 l * € 2.429/l)
TOTAAL € 60.00
BTW 21% € 10.41
10-10-2026 11:22:59"""
        r = recognize_receipt_fields(text)
        expected = {
            "station": "Argos",
            "place": "Rijssen",
            "date": "2026-10-10",
            "time": "11:22:59",
            "fuel_type": "95 ongelood",
            "pump": "4",
            "liters": "24,70",
            "price_per_liter": "2,429",
            "total": "60,00",
            "vat_rate": "21%",
            "vat_amount": "10,41",
        }
        for key, value in expected.items():
            with self.subTest(field=key):
                self.assertEqual(r[key], value)
        self.assertEqual(r["warnings"], [])

if __name__ == "__main__":
    unittest.main()
