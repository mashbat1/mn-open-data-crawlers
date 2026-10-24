import unittest
from crawlers.tender_archive import validate_rows


class ArchiveTests(unittest.TestCase):
    def meta(self, count=2):
        return {"quality": {"rows_count": count}, "DataDictionary": [
            {"col_name": "invitation_id", "name": "Урилгын дугаар"},
            {"col_name": "year", "name": "Жил"},
            {"col_name": "publish_date", "name": "Зарласан огноо"}]}

    def test_preserves_translated_rows_and_reports_actual_archive_scope(self):
        rows = [{"Урилгын дугаар": 101, "Жил": 2024, "Зарласан огноо": "2023-12-15"},
                {"Урилгын дугаар": 102, "Жил": 2025, "Зарласан огноо": "2025-06-01"}]
        data, scope = validate_rows(self.meta(), rows)
        self.assertIs(data, rows)
        self.assertEqual(scope["years"], {"2024": 1, "2025": 1})
        self.assertEqual(scope["publish_date_min"], "2023-12-15")

    def test_accepts_documented_original_column_names_in_result_wrapper(self):
        rows = [{"invitation_id": 101, "year": 2024, "publish_date": "2024-01-01"}]
        self.assertEqual(validate_rows(self.meta(1), {"result": rows})[1]["unique_invitation_ids"], 1)

    def test_rejects_truncation_duplicate_ids_and_unrecognised_columns(self):
        row = {"invitation_id": 101, "year": 2024, "publish_date": "2024-01-01"}
        for body in ([row], [row, row], [{"bad": 1}, {"bad": 2}]):
            with self.assertRaises(RuntimeError):
                validate_rows(self.meta(), body)
