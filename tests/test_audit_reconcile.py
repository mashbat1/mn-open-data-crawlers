import tempfile
import unittest
from unittest.mock import patch
from crawlers import audit
from crawlers.core import Ctx, TimeBudgetExpired


class AuditTests(unittest.TestCase):
    def test_shifted_offsets_recover_a_tie_missing_at_normal_page_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = Ctx("audit", directory)
            ctx.state.update(total=21, audit_ids={str(n): "Office" for n in range(1, 21)},
                             opinion_totals={}, offices={"10": "Office"}, reconcile_bootstrapped=True)
            def row(n):
                return {"id": n, "url": str(n), "cells": [str(n), "Report", "Office", "Type"]}
            def listing(offset, opinion="", **query):
                if offset == 10:
                    return 21, [row(21)]
                if set(query) in ({"aimag_id"}, {"aimag_id", "audit_category"}) and query.get("audit_category", 1) == 1:
                    return 21, [row(n) for n in range(1, 21)]
                return 0, []
            try:
                with patch.object(audit, "list_page", side_effect=listing):
                    self.assertTrue(audit.reconcile(ctx))
                self.assertEqual(len(ctx.state["audit_ids"]), 21)
            finally:
                ctx.close()
    def test_native_subpartitions_recover_rows_when_parent_paging_repeats(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = Ctx("audit", d)
            ctx.state.update(total=21, audit_ids={str(n): "Office" for n in range(1, 21)},
                             opinion_totals={}, offices={"10": "Office"}, reconcile_bootstrapped=True)
            ctx.state["attempts"]["reconcile"] = {"|10": 3}
            ctx.state["errors"]["reconcile"] = {"|10": "old unstable pagination failure"}
            def row(n):
                return {"id": n, "url": f"https://www.audit.mn/audit/report_detail/{n}",
                        "cells": [str(n), "Public report", "Office", "Financial audit"]}
            calls = []
            def listing(offset, opinion="", **query):
                calls.append((offset, query))
                return (1, [row(21)]) if query.get("audit_category") == 1 else (21, [row(n) for n in range(1, 21)])
            try:
                with patch.object(audit, "list_page", side_effect=listing):
                    self.assertTrue(audit.reconcile(ctx))
                self.assertEqual(len(ctx.state["audit_ids"]), 21)
                self.assertTrue(any(q.get("audit_category") == 1 for _, q in calls))
                self.assertEqual(ctx.state["errors"]["reconcile"], {})
            finally:
                ctx.close()

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
