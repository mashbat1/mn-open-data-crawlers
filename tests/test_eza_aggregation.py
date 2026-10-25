import unittest
from reconcile_eza import merge_groups, payer_totals


class AggregationTests(unittest.TestCase):
    def test_official_payer_recovery_preserves_exact_amount_and_count(self):
        self.assertEqual(payer_totals([{"transactionCount": 1, "transactionAmount": "0.1"},
                                      {"transactionCount": 2, "transactionAmount": "0.2"}]), (3, "0.3"))
        with self.assertRaises(RuntimeError):
            payer_totals([{"transactionCount": 1, "transactionAmount": None}])
    def test_exact_decimal_sum_and_transaction_counts_across_disjoint_codes(self):
        rows = merge_groups([[{"register": "1234567", "name": "Company", "count": 2, "amount": "0.1"}],
                             [{"register": "1234567", "name": "Company", "count": 1, "amount": "0.2"}]])
        self.assertEqual(rows[0]["amount"], "0.3")
        self.assertEqual(rows[0]["count"], 3)

    def test_unknown_amount_is_not_silently_treated_as_zero(self):
        with self.assertRaises(RuntimeError):
            merge_groups([[{"register": "1234567", "name": "Company", "count": 1, "amount": None}]])
