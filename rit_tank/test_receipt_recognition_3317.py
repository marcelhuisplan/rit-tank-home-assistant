import unittest
from receipt_recognition import recognize_receipt_fields

class ArgosReceiptTests(unittest.TestCase):
    def test_supplier_address_is_not_fuel_location(self):
        text = """Shop
Klik Services BV, ARGOS
Reggesingel 40, 7461 AK Rijssen
Brandstofleverancier
Varo Energy Retail BV
Wilhelminakade 919, 3072 AP Rotterdam
KLANTENBON
TOTAAL € 60,00"""
        result = recognize_receipt_fields(text)
        self.assertEqual(result["station"].lower(), "argos")
        self.assertEqual(result["place"], "Rijssen")
        self.assertEqual(result["postcode"], "7461 AK")
        self.assertEqual(
            result["address"],
            "Reggesingel 40, 7461 AK Rijssen"
        )
        self.assertNotIn("Rotterdam", result["address"])
        self.assertEqual(result["status"]["place"], "herkend")

if __name__ == "__main__":
    unittest.main()
