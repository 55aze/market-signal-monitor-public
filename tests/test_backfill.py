import copy
import unittest
from unittest.mock import Mock, patch
import pandas as pd
from market_signal_monitor.backfill import run_backfill, StatusStore
from market_signal_monitor.events import extract_events
from test_delivery import INSTRUMENT, fixture

class Store:
    def __init__(self): self.state = self.status = None
    def preflight(self): pass
    def load(self, key): return key, copy.deepcopy(self.state)
    def save(self, page, state, status='Partial', error=''):
        self.state, self.status = copy.deepcopy(state), status

class BackfillTests(unittest.TestCase):
    def setUp(self):
        calendar_patch = patch('market_signal_monitor.backfill.has_close_policy', return_value=False)
        calendar_patch.start()
        self.addCleanup(calendar_patch.stop)

    def config(self):
        return dict(pine_sha256='abc', warmup=1, timeframes=['4H'],
            instruments=[INSTRUMENT], backfill_months={'4H':36}, backfill_limits={'4H':5})

    def test_window_before_cap_and_confirmation_cutoff(self):
        bars, calc = fixture()
        result = extract_events(bars, calc, INSTRUMENT, '4H', 'v1', bars.index[-1],
            backfill=True, warmup=1, limit=1,
            window_start=bars.index[2], window_end=bars.index[4])
        self.assertEqual([e['timestamp'] for e in result],
                         [bars.index[2].isoformat(), bars.index[3].isoformat()])

    def test_ambiguous_create_resume_freezes_window_and_payload(self):
        bars, calc = fixture()
        store, client, durable = Store(), Mock(), {}
        def ambiguous(event):
            durable[event['event_id']] = copy.deepcopy(event)
            raise RuntimeError('response lost after create')
        client.append.side_effect = ambiguous
        with patch('market_signal_monitor.backfill.calculate', return_value=calc):
            first = run_backfill(self.config(), client=client, fetcher=lambda *a: bars,
                                 selected=['QQQ'], now=bars.index[-1], store=store)
        self.assertEqual(first['status'], 'partial_failure')
        self.assertEqual(store.status, 'Partial')
        frozen = copy.deepcopy(store.state)
        def append(event):
            created = event['event_id'] not in durable
            durable[event['event_id']] = copy.deepcopy(event)
            return created
        client.append.side_effect = append
        fetch = Mock(side_effect=AssertionError('must resume saved manifest'))
        second = run_backfill(self.config(), client=client, fetcher=fetch, selected=['QQQ'],
                             now=bars.index[-1] + pd.Timedelta(days=1), store=store)
        self.assertEqual((second['existing'], second['created'], len(durable)), (1,3,4))
        self.assertEqual(store.state, frozen)
        self.assertEqual(store.status, 'Insufficient History')
        self.assertEqual(second['status'], 'completed_with_warnings')
        fetch.assert_not_called()

    def test_changed_plan_preserves_checkpoint(self):
        bars, calc = fixture()
        store, client, config = Store(), Mock(), self.config()
        with patch('market_signal_monitor.backfill.calculate', return_value=calc):
            run_backfill(config, client=client, fetcher=lambda *a: bars,
                         selected=['QQQ'], now=bars.index[-1], store=store)
        before = copy.deepcopy(store.state)
        config['backfill_limits']['4H'] = 4
        report = run_backfill(config, client=client, fetcher=Mock(),
                             selected=['QQQ'], now=bars.index[-1], store=store)
        self.assertEqual(report['status'], 'partial_failure')
        self.assertEqual(before, store.state)

    def test_explicit_selection_required(self):
        with self.assertRaisesRegex(ValueError, 'explicit'):
            run_backfill({}, client=Mock(), fetcher=Mock(), selected=None)

    def test_live_status_advances_checkpoint_only_on_success(self):
        client = Mock()
        store = StatusStore(client, 'status')
        at = pd.Timestamp('2026-09-07T08:00Z')
        through = pd.Timestamp('2026-09-07T07:30Z')
        store.live_success('page', through, at)
        props = client.request.call_args.args[2]['properties']
        self.assertEqual(props['Scan Status']['select']['name'], 'Success')
        self.assertEqual(props['Continuous Through']['date']['start'], through.isoformat())
        self.assertEqual(props['Error']['rich_text'], [])

        client.reset_mock()
        store.live_failure('page', 'Fetch Failed', 'unreachable', at)
        props = client.request.call_args.args[2]['properties']
        self.assertEqual(props['Scan Status']['select']['name'], 'Fetch Failed')
        self.assertNotIn('Continuous Through', props)
