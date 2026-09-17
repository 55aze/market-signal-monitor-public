import unittest
from unittest.mock import Mock, patch
from market_signal_monitor.enrollment import enroll_amzn


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.config = {'instruments': [{'id': 'AMZN'}], 'timeframes': ['30m', '4H', '1D', '1W'],
                       'notion': {'status_data_source': 'status'}}
        self.client = Mock()
        self.client.request.return_value = {'properties': {}}

    @patch('market_signal_monitor.enrollment.StatusStore.preflight')
    def test_creates_only_missing_rows_without_checkpoint(self, _):
        self.client.query.side_effect = [[{'id': 'existing'}], [], [], []]
        self.assertEqual(enroll_amzn(self.config, self.client), 3)
        creates = [c for c in self.client.request.call_args_list if c.args[0] == 'POST']
        self.assertEqual(len(creates), 3)
        for call in creates:
            self.assertNotIn('Continuous Through', call.args[2]['properties'])

    @patch('market_signal_monitor.enrollment.StatusStore.preflight')
    def test_existing_rows_are_untouched(self, _):
        self.client.query.return_value = [{'id': 'existing'}]
        self.assertEqual(enroll_amzn(self.config, self.client), 0)
        self.assertFalse(any(c.args[0] == 'POST' for c in self.client.request.call_args_list))

    @patch('market_signal_monitor.enrollment.StatusStore.preflight')
    def test_duplicate_or_ambiguous_create_stops(self, _):
        self.client.query.return_value = [{}, {}]
        with self.assertRaises(ValueError):
            enroll_amzn(self.config, self.client)
        self.client.query.return_value = []
        self.client.request.side_effect = [{'properties': {}}, RuntimeError('unknown create outcome')]
        with self.assertRaises(RuntimeError):
            enroll_amzn(self.config, self.client)
        self.assertEqual(self.client.query.call_count, 2)
