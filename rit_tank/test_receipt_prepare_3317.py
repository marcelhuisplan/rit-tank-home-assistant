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
    def prepare_text(self, text):
        con = sqlite3.connect(":memory:")
        try:
            init_schema(con)
            fake_photo = base64.b64encode(
                b"\xff\xd8\xff\x00"
            ).decode("ascii")
            with patch(
                "receipt_archive.images_to_pdf",
                return_value=(b"%PDF-1.4 test", text)
            ):
                return prepare(
                    [{"data_url": "data:image/jpeg;base64,"
                      + fake_photo}],
                    con
                )
        finally:
            con.close()

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
            self.assertEqual(
                result["suggested_filename"],
                "2026-10-10_Argos_Rijssen_60-00.pdf"
            )
            self.assertNotIn(
                "Rotterdam", result["suggested_filename"]
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM receipt_archive"
                ).fetchone()[0], 0
            )
        finally:
            con.close()

    def test_prepare_preserves_tango_place_when_detail_parser_cannot_read_it(self):
        text = """T a n g o Maarsbergen
3 9 5 3 M H M a a r s b e r g e n
Da tum 1 0 - 0 9 - 2 0 1 9 09:57
TO TAAL € 6 3 . 8 0"""

        result = self.prepare_text(text)

        self.assertEqual(result["recognized_details"]["place"], "Maarsbergen")
        self.assertEqual(
            result["recognized_details"]["status"]["place"], "herkend"
        )
        self.assertEqual(result["recognized"]["place"], "Maarsbergen")
        self.assertEqual(
            result["suggested_filename"],
            "2019-09-10_Tango_Maarsbergen_63-80.pdf"
        )

    def test_prepare_does_not_use_supplier_only_place(self):
        text = """Shop
Klik Services BV, ARGOS
Brandstofleverancier
Varo Energy Retail BV
Wilhelminakade 919, 3072 AP Rotterdam
KLANTENBON
TOTAAL € 60.00
10-10-2026 11:22:59"""

        result = self.prepare_text(text)

        self.assertEqual(result["recognized_details"]["place"], "")
        self.assertEqual(result["recognized"]["place"], "")
        self.assertEqual(
            result["suggested_filename"],
            "2026-10-10_Argos_Plaats-onbekend_60-00.pdf"
        )


if __name__ == "__main__":
    unittest.main()
