import base64
import sqlite3
import unittest
from unittest.mock import patch

from receipt_archive import init_schema, prepare


BON = """Shop
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


class ReceiptPrepareTests(unittest.TestCase):
    def test_prepare_returns_recognized_details(self):
        con = sqlite3.connect(":memory:")
        try:
            init_schema(con)
            fake_photo = base64.b64encode(
                b"\xff\xd8\xff\x00"
            ).decode("ascii")

            with patch(
                "receipt_archive.images_to_pdf",
                return_value=(b"%PDF-1.4 test", BON)
            ):
                result = prepare(
                    [{"data_url": "data:image/jpeg;base64,"
                      + fake_photo}],
                    con
                )

            details = result["recognized_details"]
            self.assertEqual(details["station"], "Argos")
            self.assertEqual(details["place"], "Rijssen")
            self.assertEqual(details["liters"], "24,70")
            self.assertEqual(
                details["price_per_liter"], "2,429"
            )
            self.assertEqual(details["total"], "60,00")
            self.assertEqual(details["warnings"], [])
            self.assertTrue(
                result["suggested_filename"].endswith(".pdf")
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM receipt_archive"
                ).fetchone()[0], 0
            )
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
