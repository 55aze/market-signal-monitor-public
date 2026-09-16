import unittest
from clear_backfill import clean, SOURCE


class CleanupTests(unittest.TestCase):
    def test_pagination_live_protection_and_rerun(self):
        pages = {str(i): {"id": str(i), "parent": {"data_source_id": SOURCE},
                 "properties": {"Backfill": {"checkbox": i != 3}}} for i in range(4)}
        patches = []
        def api(method, path, body=None):
            if path == f"data_sources/{SOURCE}":
                return {"properties": {"Backfill": {"type": "checkbox"}}}
            if path.endswith('/query'):
                rows = [p.copy() for p in pages.values() if p['properties']['Backfill']['checkbox'] and not p.get('in_trash')]
                start = int(body.get('start_cursor', 0))
                return {'results': rows[start:start+2], 'has_more': start+2 < len(rows), 'next_cursor': str(start+2)}
            page = pages[path.split('/')[-1]]
            if method == 'PATCH':
                self.assertTrue(page['properties']['Backfill']['checkbox'])
                self.assertEqual(body, {'in_trash': True})
                patches.append(page['id'])
                page.update(body)
            return page
        report = {'trashed': 0, 'skipped': 0}
        clean(api, report)
        self.assertEqual(patches, ['0', '1', '2'])
        self.assertFalse(pages['3'].get('in_trash', False))
        self.assertTrue(report['remaining_backfill_zero'])
        clean(api, {'trashed': 0, 'skipped': 0})
        self.assertEqual(len(patches), 3)

    def test_unexpected_parent_stops_before_write(self):
        def api(method, path, body=None):
            self.assertNotEqual(method, 'PATCH')
            if method == 'GET':
                return {'properties': {'Backfill': {'type': 'checkbox'}}}
            return {'results': [{'id': 'x', 'parent': {'data_source_id': 'other'},
                                 'properties': {'Backfill': {'checkbox': True}}}]}
        with self.assertRaisesRegex(RuntimeError, 'unexpected'):
            clean(api, {'trashed': 0, 'skipped': 0})
