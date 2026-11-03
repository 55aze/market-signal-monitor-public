"""Public-safe synthetic renderer tests; no private watchlist snapshots."""
import importlib.util
import json
import unittest
from pathlib import Path
from html.parser import HTMLParser

p = Path(__file__).resolve().parents[1] / 'scripts' / 'render_signal_report.py'
s = importlib.util.spec_from_file_location('renderer', p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.events, self.cells = [], []
        self.in_data, self.data = False, ''
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'data-event-id' in a: self.events.append(a['data-event-id'])
        if 'data-tf' in a: self.cells.append(a['data-tf'])
        if a.get('id') == 'report-data': self.in_data = True
    def handle_endtag(self, tag):
        if tag == 'script': self.in_data = False
    def handle_data(self, data):
        if self.in_data: self.data += data

class Tests(unittest.TestCase):
    def setUp(self):
        self.packet = {'as_of': '2026-11-03T08:46:00Z', 'watch_context': {'row_count': 1, 'rows': [{
            'ticker': 'TEST', 'market_timezone': 'America/New_York', 'group_date': '2026-11-02',
            'highest_signal_tf': '4H', 'anchor_confirmed_at': '2026-11-02T21:00:00Z',
            'cells': {tf: {'structure': '🟦 > 🟨 > ● > 🟦 > 🟨 > E200', 'stream': 'TEST:' + tf,
                'bar_at': '2026-11-02T18:30:00Z', 'confirmed_at': '2026-11-02T21:00:00Z',
                'freshness': 'current', 'signals': [{'signal': 'Bottom',
                'timestamp': '2026-11-02T18:30:00Z', 'confirmation_at': '2026-11-02T21:00:00Z'}]}
                for tf in m.TFS}}]}}
        self.plan = {'report_items': [
            {'id': 'a', 'ticker': 'TEST', 'origin_tf': '30m', 'signal': 'Bottom', 'price': 12.3,
             'confirmed_at': '2026-11-02T20:30:00Z', 'market_timezone': 'America/New_York'},
            {'id': 'b', 'ticker': 'TEST', 'origin_tf': '30m', 'signal': 'Bottom', 'price': 12.4,
             'confirmed_at': '2026-11-02T21:00:00Z', 'market_timezone': 'America/New_York'}],
             'receipt': {'item_ids': ['a', 'b']}}
    def parse(self):
        parser = Parser()
        parser.feed(m.render(self.packet, self.plan))
        return parser
    def test_repeated_bars_and_sort(self):
        self.assertEqual(self.parse().events, ['b', 'a'])
    def test_all_four_cells(self):
        self.assertEqual(self.parse().cells, list(m.TFS))
    def test_data_roundtrip(self):
        self.assertEqual(json.loads(self.parse().data), {'packet': self.packet, 'plan': self.plan})
    def test_script_injection_safe(self):
        self.packet['watch_context']['rows'][0]['ticker'] = '</script><script>alert(1)</script>'
        markup = m.render(self.packet, self.plan)
        self.assertNotIn('</script><script>alert(1)', markup)
        self.assertEqual(json.loads(self.parse().data)['packet'], self.packet)
    def test_count_mismatch(self):
        self.packet['watch_context']['row_count'] = 2
        with self.assertRaises(ValueError): self.parse()
    def test_missing_context_not_zero(self):
        del self.packet['watch_context']
        with self.assertRaises(ValueError): self.parse()
    def test_receipt_order_mismatch(self):
        self.plan['receipt']['item_ids'].reverse()
        with self.assertRaises(ValueError): self.parse()
    def test_unknown_and_future_times(self):
        for value in (None, 'garbled', '2026-11-04T00:00:00Z'):
            self.assertEqual(m.relative(value, self.packet['as_of'], 'UTC'), '待核')
    def test_timezone_dst(self):
        self.assertIn('EDT', m.local('2026-10-30T20:00:00Z', 'America/New_York'))
        self.assertIn('EST', m.local('2026-11-02T21:00:00Z', 'America/New_York'))
    def test_structure_default_is_not_collapsed(self):
        markup = m.render(self.packet, self.plan)
        self.assertIn('<section id="structures" aria-labelledby="structure-heading">', markup)
        self.assertIn('<option value="all">全部</option>', markup)
        self.assertNotIn('overflow:hidden', m.CSS.replace(' ', ''))
        self.assertNotIn('max-height:0', m.CSS.replace(' ', ''))

if __name__ == '__main__':
    unittest.main()
