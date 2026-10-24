import json
from pathlib import Path
import tempfile
import unittest
from crawlers.companies import header_names, registry_record
from crawlers.eiti import scrub, validate_report_year


class SourceTests(unittest.TestCase):
    def test_registry_schema_type_is_not_a_data_column_and_missing_address_does_not_shift_owners(self):
        header = {'type': 'array', 'Регистрийн дугаар': 'number', 'Оноосон нэр': 'string',
                  'Хуулийн этгээдийн хаяг': 'string', 'Owners': {'type': 'array'}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, 'registry.json')
            p.write_text(json.dumps({'Header': header, 'Data': []}), encoding='utf-8')
            rec = registry_record(['1234567', 'Same name', [['Owner']]], header_names(p), 42)
        self.assertEqual(rec['Регистрийн дугаар'], '1234567')
        self.assertEqual(rec['Owners'], [['Owner']])
        self.assertEqual(rec['registry_row'], 42)

    def test_unknown_registry_shape_fails_instead_of_mislabeling(self):
        with self.assertRaises(RuntimeError):
            registry_record(['1234567'], ['Register', 'Name'], 1)

    def test_eiti_money_is_not_redacted_as_a_phone(self):
        self.assertEqual(scrub(['Tax', '88776655']), ['Tax', '88776655'])
        self.assertEqual(scrub(['1', 'Утас', '88776655']), ['1', 'Утас', '(хассан)'])

    def test_eiti_checks_the_annual_form_year(self):
        validate_report_year('<p>2023 ОНЫ ТАЙЛАНГИЙН МАЯГТ</p>', 2023)
        with self.assertRaises(RuntimeError):
            validate_report_year('<p>2024 ОНЫ ТАЙЛАНГИЙН МАЯГТ</p>', 2023)
