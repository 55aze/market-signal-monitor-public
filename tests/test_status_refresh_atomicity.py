"""Polling must not invalidate a previously committed market-data assessment."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from market_signal_monitor.backfill import StatusStore


NOW = '2026-11-10T10:37:00+00:00'
CURRENT = {'status': 'current', 'expected_latest': '2026-11-10T06:30:00+00:00',
           'actual_latest': '2026-11-10T06:30:00+00:00', 'missing_count': 0}
GAP = {**CURRENT, 'status': 'interior_gap', 'missing_count': 2}


class StatusRefreshAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.store = StatusStore(self.client, 'status-source')
        self.store.freshness_fields = {'Freshness Status', 'Expected Through', 'Missing Count'}

    def props(self):
        return self.client.request.call_args.args[2]['properties']

    def test_attempt_does_not_erase_committed_assessment(self):
        self.store.live_attempt('page', NOW)
        for name in ('Freshness Status', 'Expected Through', 'Missing Count',
                     'Current Structure', 'Continuous Through', 'Last Success'):
            self.assertNotIn(name, self.props())
        self.assertIn('Last Attempt', self.props())

    def test_attempt_does_not_clear_previous_error_or_claim_success(self):
        self.store.live_attempt('page', NOW)
        for name in ('Scan Status', 'Error', 'Unresolved Gap'):
            self.assertNotIn(name, self.props())

    def test_success_publishes_snapshot_assessment_and_checkpoint_together(self):
        structure = {'values': {'Close': 1.337}, 'freshness': deepcopy(CURRENT)}
        self.store.live_success('page', CURRENT['actual_latest'], NOW, structure)
        self.client.request.assert_called_once()
        p = self.props()
        raw = ''.join(x['text']['content'] for x in p['Current Structure']['rich_text'])
        self.assertEqual(json.loads(raw), structure)
        self.assertEqual(p['Freshness Status']['select']['name'], 'Current')
        self.assertEqual(p['Continuous Through']['date']['start'], CURRENT['actual_latest'])

    def test_fetch_failure_invalidates_latest_attempt_without_advancing_snapshot(self):
        self.store.live_attempt('page', NOW)
        self.store.live_failure('page', 'Fetch Failed', 'provider timed out', NOW)
        p = self.props()
        self.assertEqual(p['Freshness Status']['select']['name'], 'Unverifiable')
        self.assertIsNone(p['Missing Count']['number'])
        self.assertNotIn('Current Structure', p)
        self.assertNotIn('Continuous Through', p)
        self.assertNotIn('Last Success', p)

    def test_write_failure_does_not_leave_current_badge(self):
        self.store.live_attempt('page', NOW)
        self.store.live_failure('page', 'Write Failed', 'event write failed', NOW)
        self.assertEqual(self.props()['Freshness Status']['select']['name'], 'Unverifiable')
        self.assertNotIn('Continuous Through', self.props())

    def test_newly_proven_gap_is_not_downgraded_to_unknown(self):
        self.store.live_attempt('page', NOW)
        self.store.live_freshness('page', GAP)
        self.store.live_failure('page', 'Fetch Failed', 'confirmed bar gap', NOW)
        p = self.props()
        self.assertEqual(p['Freshness Status']['select']['name'], 'Gap')
        self.assertEqual(p['Missing Count']['number'], 2)
        self.assertNotIn('Continuous Through', p)

    def test_assessment_cannot_leak_between_attempts(self):
        self.store.live_freshness('page', GAP)
        self.store.live_attempt('page', NOW)
        self.store.live_failure('page', 'Fetch Failed', 'timeout before validation', NOW)
        self.assertEqual(self.props()['Freshness Status']['select']['name'], 'Unverifiable')

    def test_failed_assessment_write_is_not_cached_as_persisted(self):
        self.store.live_attempt('page', NOW)
        self.client.request.side_effect = RuntimeError('write unavailable')
        with self.assertRaises(RuntimeError):
            self.store.live_freshness('page', GAP)
        self.client.request.side_effect = None
        self.store.live_failure('page', 'Write Failed', 'assessment write failed', NOW)
        self.assertEqual(self.props()['Freshness Status']['select']['name'], 'Unverifiable')

    def test_optional_columns_remain_optional(self):
        self.store.freshness_fields = set()
        self.store.live_attempt('page', NOW)
        self.store.live_failure('page', 'Fetch Failed', 'timeout', NOW)
        self.assertNotIn('Freshness Status', self.props())


if __name__ == '__main__':
    unittest.main()
