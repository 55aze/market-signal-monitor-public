import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

spec = importlib.util.spec_from_file_location('session_replay', Path(__file__).resolve().parents[1] / 'scripts/replay_session_coverage.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class SessionReplayTests(unittest.TestCase):
    def run_replay(self, stamps, checkpoint='2026-10-29T21:00Z'):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / 'bars.csv'
            pd.DataFrame({'time': stamps, 'open': 10., 'high': 11., 'low': 9., 'close': 10.}).to_csv(csv, index=False)
            args = ['replay', str(csv), '--instrument', 'XAUUSD', '--timeframe', '1D',
                    '--checkpoint', checkpoint, '--strict-since', '2026-10-01T00:00Z',
                    '--now', '2026-11-03T22:00Z']
            output = io.StringIO()
            with patch('sys.argv', args), contextlib.redirect_stdout(output):
                code = replay.main()
            return code, json.loads(output.getvalue())

    def test_complete_dst_native_shaped_fixture_and_missing_bar(self):
        # Synthetic OHLC, explicit UTC native-style labels; not a provider capture.
        stamps = ['2026-10-29T21:00Z', '2026-11-01T22:00Z', '2026-11-02T22:00Z']
        code, result = self.run_replay(stamps)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'current')
        code, result = self.run_replay(stamps[::2])
        self.assertEqual(code, 1)
        self.assertEqual(result['missing_bar_opens'], ['2026-11-01T22:00:00+00:00'])

    def test_checkpoint_outside_capture_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'checkpoint absent'):
            self.run_replay(['2026-11-01T22:00Z', '2026-11-02T22:00Z'])
