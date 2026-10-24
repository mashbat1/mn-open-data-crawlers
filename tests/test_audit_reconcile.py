import tempfile
import unittest
from unittest.mock import patch
from crawlers import audit
from crawlers.core import Ctx, TimeBudgetExpired


class AuditTests(unittest.TestCase):
    def test_office_filter_recovers_a_row_skipped_by_global_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx('audit', d)
            ctx.state.update(total=2, audit_ids={'1': 'Office'}, opinion_totals={},
                             offices={'10': 'Office'}, reconcile_bootstrapped=True)
            row = {'id': 2, 'url': 'https://www.audit.mn/audit/report_detail/2',
                   'cells': ['2', 'Recovered report', 'Office', 'Financial audit']}
            try:
                with patch.object(audit, 'list_page', return_value=(2, [row])):
                    self.assertTrue(audit.reconcile(ctx))
                self.assertEqual(len(ctx.state['audit_ids']), 2)
                self.assertEqual(ctx.sinks['audits'].n, 1)
            finally:
                ctx.close()

    def test_time_budget_pause_does_not_consume_retry(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx('audit', d)
            def pause(_):
                raise TimeBudgetExpired()
            self.assertFalse(ctx.run_items('office', ['10'], pause))
            self.assertEqual(ctx.state['attempts']['office'], {})
            self.assertEqual(ctx.state['errors']['office'], {})
