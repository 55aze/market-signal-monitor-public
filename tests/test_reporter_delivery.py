"""Delivery regression tests. Synthetic event values only; no private snapshots."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'reporter_delivery.py'
spec = importlib.util.spec_from_file_location('reporter_delivery', SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

NOW = '2026-10-31T04:00:00+00:00'
PREPARED = '2026-10-31T02:00:00+00:00'
RECORDED = '2026-10-31T02:00:03+00:00'


def make_packet(n=3, offset=0, as_of='2026-10-31T01:59:00+00:00'):
    items = []
    for k in range(offset, offset + n):
        identifier = gate.identity('synthetic-signal', k)
        price = 100 + k / 10
        bar = f'2026-10-30T{(k % 20):02d}:00:00+00:00'
        confirmed = f'2026-10-30T{(k % 20):02d}:30:00+00:00'
        ticker = f'TEST{k:03}'
        line = f'{ticker} · 30m Bottom · {price} Price · bar_at={bar} · confirmed_at={confirmed}'
        items.append(dict(id=identifier, kind='RAW_SIGNAL', line=line, ticker=ticker,
                          origin_tf='30m', signal='Bottom', price=price, value_unit='Price',
                          bar_at=bar, confirmed_at=confirmed, market_timezone='America/New_York'))
    ids = [x['id'] for x in items]
    pid = gate.identity('packet', sorted(ids))
    return dict(version=2, presentation='signals-and-current-structure-v1',
                packet_id=pid, as_of=as_of, report_items=items, acknowledgement_ids=ids,
                batch=dict(included=n, remaining=0, total_pending=n, order='oldest_first'),
                packet_end=pid)


def current_packet(n=3, offset=0):
    return make_packet(n, offset, as_of='2026-10-31T03:59:00+00:00')


def result_for(packet=None, ids=None):
    packet = packet or make_packet()
    ids = ids or packet['acknowledgement_ids']
    return dict(run_id='prior-real-tool-result', created_at=RECORDED,
                content_truncated=False,
                content_text='Rendered signals\n' + gate.seal(packet, ids, PREPARED),
                is_failure_message=False)


def persisted(ids):
    return gate.compact({**gate.make_receipt(ids), 'delivered_at': None,
                         'delivery_evidence': 'prior_complete_report'})


def empty_result():
    return dict(run_id='prior-no-update', created_at=RECORDED, content_truncated=False,
                content_text='', is_failure_message=False)


class EvidenceTests(unittest.TestCase):
    def test_backend_identity_known_fixture(self):
        ids = ['abb0759ae5447bef3a45b760', '80e85c54bad590a2aa570abd']
        self.assertEqual(gate.identity('packet', sorted(ids)), '8df02fc0ff77bd42e9a498ff')

    def test_evidence_roundtrip_retains_every_item_and_receipt_order(self):
        packet = make_packet(24)
        evidence = gate.read_prior_result(result_for(packet), NOW)
        self.assertEqual(evidence['kind'], 'complete')
        self.assertEqual(evidence['evidence']['report_items'], packet['report_items'])
        self.assertEqual(evidence['ids'], packet['acknowledgement_ids'])

    def test_subset_receipt_uses_subset_identity(self):
        packet = make_packet(14)
        ids = packet['acknowledgement_ids'][3:]
        result = result_for(packet, ids)
        evidence = gate.read_prior_result(result, NOW)['evidence']
        self.assertEqual(evidence['receipt'], gate.make_receipt(ids))
        self.assertNotEqual(evidence['receipt']['packet_id'], packet['packet_id'])
        self.assertEqual(len(evidence['report_items']), 11)

    def test_current_run_and_retry_cannot_acknowledge(self):
        result = result_for()
        with self.assertRaisesRegex(gate.GateError, 'current run or retry'):
            gate.read_prior_result(result, NOW, current_run_id=result['run_id'])

    def test_future_and_naive_result_timestamps_rejected(self):
        for timestamp in ['2026-10-31T05:00:00Z', '2026-10-31T02:00:03']:
            result = result_for()
            result['created_at'] = timestamp
            with self.assertRaises(gate.GateError):
                gate.read_prior_result(result, NOW)

    def test_preparation_after_recorded_result_rejected(self):
        result = result_for()
        result['created_at'] = '2026-10-31T01:59:59Z'
        with self.assertRaisesRegex(gate.GateError, 'evidence newer'):
            gate.read_prior_result(result, NOW)

    def test_tool_truncation_flag_rejected_even_with_valid_content(self):
        result = result_for()
        result['content_truncated'] = True
        with self.assertRaisesRegex(gate.GateError, 'truncated'):
            gate.read_prior_result(result, NOW)

    def test_truncated_blob_rejected(self):
        result = result_for()
        lines = result['content_text'].splitlines()
        lines[1] = lines[1][:-10]
        result['content_text'] = '\n'.join(lines)
        with self.assertRaises(gate.GateError):
            gate.read_prior_result(result, NOW)

    def test_digest_corruption_rejected(self):
        result = result_for()
        result['content_text'] = result['content_text'].replace(gate.MARKER, gate.MARKER + '0', 1)
        with self.assertRaises(gate.GateError):
            gate.read_prior_result(result, NOW)

    def test_opposite_receipt_order_not_equivalent(self):
        packet = make_packet()
        result = result_for(packet)
        wrong = gate.make_receipt(list(reversed(packet['acknowledgement_ids'])))
        lines = result['content_text'].splitlines()
        lines[-1] = gate.RECEIPT + gate.compact(wrong)
        result['content_text'] = '\n'.join(lines)
        with self.assertRaisesRegex(gate.GateError, 'Receipt mismatch'):
            gate.read_prior_result(result, NOW)

    def test_incomplete_covered_parent_item_cannot_hide_behind_dedup(self):
        packet = current_packet()
        old = persisted(packet['acknowledgement_ids'])
        packet['report_items'][0]['line'] = 'truncated'
        with self.assertRaisesRegex(gate.GateError, 'incomplete'):
            gate.plan(packet, old, NOW)

    def test_missing_duplicate_reordered_and_wrong_count_ids_fail(self):
        good = current_packet()
        variants = []
        for key in gate.DELIVERY_KEYS:
            p = deepcopy(good)
            del p[key]
            variants.append(p)
        p = deepcopy(good)
        p['report_items'][1] = deepcopy(p['report_items'][0])
        variants.append(p)
        p = deepcopy(good)
        p['acknowledgement_ids'].reverse()
        variants.append(p)
        p = deepcopy(good)
        p['batch']['included'] += 1
        variants.append(p)
        p = deepcopy(good)
        p['packet_end'] = '0' * 24
        variants.append(p)
        for packet in variants:
            with self.subTest(packet=packet), self.assertRaises(gate.GateError):
                gate.validate_packet(packet, NOW)

    def test_line_value_direction_and_time_tampering_fail(self):
        for field, value in [('price', 999), ('signal', 'Sell'), ('confirmed_at', '2026-10-30T22:00:00Z')]:
            packet = current_packet()
            packet['report_items'][0][field] = value
            with self.subTest(field=field), self.assertRaises(gate.GateError):
                gate.validate_packet(packet, NOW)

    def test_non_finite_prices_rejected(self):
        for value in [float('inf'), float('nan'), True]:
            packet = current_packet()
            packet['report_items'][0]['price'] = value
            with self.assertRaises(gate.GateError):
                gate.validate_packet(packet, NOW)

    def test_stale_packet_and_future_confirmation_fail(self):
        with self.assertRaisesRegex(gate.GateError, 'PACKET_NOT_READY'):
            gate.validate_packet(make_packet(), NOW)
        packet = current_packet()
        packet['report_items'][0]['confirmed_at'] = '2026-11-01T00:00:00Z'
        packet['report_items'][0]['line'] = packet['report_items'][0]['line'].split('confirmed_at=')[0] + 'confirmed_at=2026-11-01T00:00:00Z'
        with self.assertRaisesRegex(gate.GateError, 'future confirmation'):
            gate.validate_packet(packet, NOW)

    def test_duplicate_json_keys_and_nan_fail(self):
        for value in ['{"a":1,"a":2}', '{"a":NaN}']:
            with self.assertRaises(gate.GateError):
                gate.loads(value)

    def test_decompression_bomb_rejected(self):
        raw = b'x' * (gate.MAX_EVIDENCE_BYTES + 1)
        text = gate.MARKER + hashlib.sha256(raw).hexdigest() + ':' + base64.b64encode(zlib.compress(raw)).decode()
        text += '\n' + gate.RECEIPT + '{}'
        with self.assertRaisesRegex(gate.GateError, 'oversized'):
            gate.unseal(text, NOW)

    def test_all_parent_ids_not_automatically_acknowledged(self):
        packet = make_packet(24)
        subset = packet['acknowledgement_ids'][-10:]
        prior = gate.read_prior_result(result_for(packet, subset), NOW)
        self.assertEqual(prior['ids'], subset)


class DeliveryPlanTests(unittest.TestCase):
    def test_repeated_14_are_silent_with_prior_complete_evidence(self):
        old = persisted(make_packet(1, 100)['acknowledgement_ids'])
        result = gate.plan(current_packet(14), old, NOW, latest_result=result_for(make_packet(14)))
        self.assertEqual(result['status'], 'SILENT')
        self.assertEqual(result['report_items'], [])
        self.assertEqual(result['ack_receipt']['delivered_at'], None)
        self.assertEqual(result['ack_receipt']['delivery_evidence'], 'prior_complete_report')

    def test_24_after_14_emits_only_10_with_subset_receipt(self):
        old = make_packet(14)['acknowledgement_ids']
        result = gate.plan(current_packet(24), persisted(old), NOW, latest_result=empty_result())
        expected = current_packet(24)['acknowledgement_ids'][14:]
        self.assertEqual(len(result['report_items']), 10)
        self.assertEqual(result['receipt'], gate.make_receipt(expected))
        self.assertIsNone(result['ack_receipt'])

    def test_partial_overlap_is_not_ack_pending(self):
        packet = current_packet(14)
        old = packet['acknowledgement_ids'][:3]
        result = gate.plan(packet, persisted(old), NOW)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(len(result['report_items']), 11)

    def test_receipt_only_cannot_acknowledge_legacy_24(self):
        packet = current_packet(24)
        result = empty_result()
        result['content_text'] = 'visualize /workspace/gone.html\n' + gate.compact(gate.make_receipt(packet['acknowledgement_ids']))
        plan = gate.plan(packet, persisted(make_packet(2, 100)['acknowledgement_ids']), NOW, latest_result=result)
        self.assertEqual(plan['status'], 'DELIVERY_UNCONFIRMED')
        self.assertEqual(len(plan['unconfirmed_ids']), 24)
        self.assertEqual(plan['report_items'], [])
        self.assertIsNone(plan['ack_receipt'])

    def test_quarantine_does_not_block_new_subset_or_ack_old(self):
        packet = current_packet(26)
        legacy = gate.make_receipt(packet['acknowledgement_ids'][:24])
        result = gate.plan(packet, persisted(make_packet(2, 100)['acknowledgement_ids']), NOW,
                           quarantine=[legacy], latest_result=empty_result())
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(len(result['unconfirmed_ids']), 24)
        self.assertEqual(len(result['report_items']), 2)
        self.assertEqual(result['receipt']['item_ids'], packet['acknowledgement_ids'][24:])
        self.assertIsNone(result['ack_receipt'])

    def test_verified_evidence_supersedes_quarantine(self):
        packet = current_packet(24)
        legacy = gate.make_receipt(packet['acknowledgement_ids'])
        result = gate.plan(packet, '', NOW, quarantine=[legacy], latest_result=result_for(make_packet(24)))
        self.assertEqual(result['unconfirmed_ids'], [])
        self.assertEqual(result['status'], 'SILENT')
        self.assertEqual(result['ack_receipt']['item_ids'], packet['acknowledgement_ids'])

    def test_union_keeps_unconsumed_persisted_ids_absent_from_current_batch(self):
        old = make_packet(2, 100)['acknowledgement_ids']
        prior = make_packet(3)
        result = gate.plan(current_packet(5), persisted(old), NOW, latest_result=result_for(prior))
        self.assertEqual(result['ack_receipt']['item_ids'], old + prior['acknowledgement_ids'])
        self.assertEqual(len(result['report_items']), 2)

    def test_empty_receipt_not_assumed_first_run(self):
        with self.assertRaisesRegex(gate.GateError, 'DELIVERY_UNCONFIRMED'):
            gate.plan(current_packet(), '', NOW)
        result = gate.plan(current_packet(), '', NOW, first_delivery_authorized=True)
        self.assertEqual(result['status'], 'READY')

    def test_malformed_persisted_receipt_not_empty(self):
        for raw in ['{', '"[1,2]"', 'oops']:
            with self.assertRaisesRegex(gate.GateError, 'RECEIPT_READ_FAILED'):
                gate.plan(current_packet(), raw, NOW)

    def test_legacy_array_and_csv_suppress_exact_ids_without_promoting(self):
        packet = current_packet()
        ids = packet['acknowledgement_ids'][:2]
        for text in [json.dumps(ids), ','.join(ids)]:
            result = gate.plan(packet, text, NOW)
            self.assertEqual(len(result['report_items']), 1)
            self.assertIsNone(result['ack_receipt'])

    def test_cold_process_no_html_required_for_ack_then_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p, d, r = root / 'packet.json', root / 'delivered.txt', root / 'result.json'
            p.write_text(gate.compact(current_packet(24)))
            d.write_text(persisted(make_packet(2, 100)['acknowledgement_ids']))
            r.write_text(gate.compact(result_for(make_packet(24))))
            run = subprocess.run([sys.executable, str(SCRIPT), 'plan', '--packet', str(p), '--delivered', str(d),
                                  '--latest-result', str(r), '--now', NOW], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            output = json.loads(run.stdout)
            self.assertEqual(output['report_items'], [])
            self.assertEqual(len(output['ack_receipt']['item_ids']), 26)
            # Persist exactly the verified prior acknowledgement, destroy the
            # prior output, start a separate process: still no repeated events.
            d.write_text(gate.compact(output['ack_receipt']))
            r.unlink()
            run = subprocess.run([sys.executable, str(SCRIPT), 'plan', '--packet', str(p), '--delivered', str(d),
                                  '--now', NOW], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['status'], 'SILENT')


class ReceiptWriteTests(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads(persisted(make_packet()['acknowledgement_ids']))

    def test_markdown_transport_roundtrip_once(self):
        self.receipt['report_ref'] = 'https://example.test/report?q=\\x*[a]_{b}|~^$'
        encoded = gate.notion_transport(self.receipt, NOW)
        self.assertTrue(encoded.startswith(r'\{'))
        self.assertIn(r'\[', encoded)
        gate.verify_readback(self.receipt, encoded, NOW, notion_markdown=True)
        gate.verify_readback(self.receipt, gate.compact(self.receipt), NOW)

    def test_double_serialized_is_rejected(self):
        with self.assertRaisesRegex(gate.GateError, 'RECEIPT_READ_FAILED'):
            gate.verify_readback(self.receipt, json.dumps(gate.compact(self.receipt)), NOW)

    def test_unknown_time_must_be_null_and_have_evidence(self):
        for edit in [{'delivered_at': '2026-10-31T06:00:00Z'},
                     {'delivered_at': '2026-10-31T02:00:00'},
                     {'delivery_evidence': 'generated_file'}]:
            value = {**self.receipt, **edit}
            with self.assertRaises(gate.GateError):
                gate.persisted_receipt(gate.compact(value), NOW)
        value = deepcopy(self.receipt)
        del value['delivered_at']
        with self.assertRaises(gate.GateError):
            gate.persisted_receipt(gate.compact(value), NOW)

    def test_readback_exact_order_required(self):
        actual = deepcopy(self.receipt)
        actual['item_ids'].reverse()
        with self.assertRaisesRegex(gate.GateError, 'WRITE_FAILED'):
            gate.verify_readback(self.receipt, gate.compact(actual), NOW)

    def test_empty_success_readback_is_failure_and_only_one_retry(self):
        calls = []
        def write(value):
            self.assertIsInstance(value, str)
            calls.append(value)
        with self.assertRaisesRegex(gate.GateError, 'WRITE_FAILED'):
            gate.persist_verified(self.receipt, write=write, read=lambda: '', now=NOW)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_transient_write_failure_retries_identical_receipt(self):
        calls = []
        def write(value):
            calls.append(value)
            if len(calls) == 1:
                raise RuntimeError('connector unavailable')
        gate.persist_verified(self.receipt, write=write, read=lambda: gate.compact(self.receipt), now=NOW)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

class RecoveryTests(unittest.TestCase):
    def test_failed_ack_preserves_complete_prior_result_not_current_receipt(self):
        prior = result_for(make_packet(14))
        recovery = gate.recovery_suffix(prior, '2026-10-31T03:00:00Z')
        self.assertNotIn('\nReceipt:', recovery)
        failed_result = dict(run_id='failed-followup', created_at='2026-10-31T03:01:00Z',
                             content_truncated=False, is_failure_message=True,
                             content_text='WRITE_FAILED: connector unavailable\n' + recovery)
        parsed = gate.read_prior_result(failed_result, NOW)
        self.assertEqual(parsed['kind'], 'complete')
        self.assertEqual(parsed['source_run_id'], prior['run_id'])
        self.assertEqual(len(parsed['ids']), 14)
        result = gate.plan(current_packet(24), persisted(make_packet(2, 100)['acknowledgement_ids']),
                           NOW, latest_result=failed_result)
        self.assertEqual(len(result['report_items']), 10)
        self.assertEqual(len(result['ack_receipt']['item_ids']), 16)

    def test_repeated_failure_flattens_original_evidence(self):
        prior = result_for(make_packet())
        first = gate.recovery_suffix(prior, '2026-10-31T03:00:00Z')
        failed = dict(run_id='failed-followup', created_at='2026-10-31T03:01:00Z',
                      content_truncated=False, content_text='WRITE_FAILED\n' + first)
        second = gate.recovery_suffix(failed, NOW)
        self.assertEqual(first, second)

    def test_legacy_receipt_cannot_be_promoted_via_recovery(self):
        prior = empty_result()
        prior['content_text'] = gate.compact(gate.make_receipt(make_packet()['acknowledgement_ids']))
        with self.assertRaisesRegex(gate.GateError, 'receipt-only'):
            gate.recovery_suffix(prior, NOW)

    def test_current_run_hidden_in_recovery_rejected(self):
        prior = result_for()
        recovery = gate.recovery_suffix(prior, '2026-10-31T03:00:00Z')
        failed = dict(run_id='different-wrapper', created_at='2026-10-31T03:01:00Z',
                      content_truncated=False, content_text='WRITE_FAILED\n' + recovery)
        with self.assertRaisesRegex(gate.GateError, 'current run'):
            gate.read_prior_result(failed, NOW, current_run_id=prior['run_id'])

    def test_failure_without_evidence_fails_closed(self):
        prior = empty_result()
        prior['content_text'] = 'WRITE_FAILED'
        prior['is_failure_message'] = True
        with self.assertRaisesRegex(gate.GateError, 'lost its evidence'):
            gate.plan(current_packet(), persisted(make_packet(2, 100)['acknowledgement_ids']),
                      NOW, latest_result=prior)

    def test_corrupt_recovery_rejected(self):
        prior = result_for()
        recovery = gate.recovery_suffix(prior, '2026-10-31T03:00:00Z')
        failed = dict(run_id='failed-followup', created_at='2026-10-31T03:01:00Z',
                      content_truncated=False, content_text='WRITE_FAILED\n' + recovery[:-10])
        with self.assertRaises(gate.GateError):
            gate.read_prior_result(failed, NOW)


class UncertainClaimRetentionTests(unittest.TestCase):
    def test_unknown_claim_is_not_lost_when_new_items_are_reported(self):
        parent = make_packet(24)
        unknown = gate.make_receipt(parent['acknowledgement_ids'][:14])
        emitted = parent['acknowledgement_ids'][14:]
        text = gate.seal(parent, emitted, PREPARED, quarantine=[unknown])
        actual = dict(run_id='complete-new-report', created_at=RECORDED,
                      content_truncated=False, content_text=text)
        result = gate.plan(current_packet(24), persisted(make_packet(2, 100)['acknowledgement_ids']),
                           NOW, latest_result=actual)
        self.assertEqual(len(result['unconfirmed_ids']), 14)
        self.assertEqual(result['report_items'], [])
        self.assertEqual(len(result['ack_receipt']['item_ids']), 12)
        self.assertEqual(result['quarantine_receipts'], [unknown])

    def test_quarantine_is_not_delivery_evidence(self):
        parent = make_packet(24)
        unknown = gate.make_receipt(parent['acknowledgement_ids'][:14])
        sealed = gate.seal(parent, parent['acknowledgement_ids'][14:], PREPARED, quarantine=[unknown])
        evidence = gate.unseal(sealed, NOW)
        self.assertEqual(len(evidence['receipt']['item_ids']), 10)
        self.assertEqual(len(evidence['quarantine_receipts'][0]['item_ids']), 14)

    def test_no_full_packet_can_be_sealed_as_missing_report(self):
        packet = make_packet()
        del packet['report_items'][0]['line']
        with self.assertRaises(gate.GateError):
            gate.seal(packet, packet['acknowledgement_ids'], PREPARED)


if __name__ == '__main__':
    unittest.main()
