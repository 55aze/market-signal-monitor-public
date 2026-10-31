#!/usr/bin/env python3
"""Offline delivery gate for Trading Signal Watch.

The chat response carries the complete event evidence, not a workspace path.
Only a later, actually read automation result can authorize acknowledgement.
This module neither writes Notion nor marks the current response delivered.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import zlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_EVIDENCE_BYTES = 256_000
MAX_RESULT_BYTES = 1_000_000
ID = re.compile(r"[0-9a-f]{24}\Z")
MARKER = 'SignalEvidence-v1: '
RECEIPT = 'Receipt: '
RECOVERY = 'AcknowledgementRecovery-v1: '
LINE = re.compile(
    r'^(.*?) · (1W|1D|4H|30m) (Bottom|Sell) · (\S+) (Price|Yield %)'
    r' · bar_at=(\S+) · confirmed_at=(\S+)$')
DELIVERY_KEYS = ('version', 'presentation', 'as_of', 'packet_id', 'batch',
                 'report_items', 'acknowledgement_ids', 'packet_end')


class GateError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f'{code}: {detail}')


def require(condition: bool, detail: str, code: str = 'READ_FAILED') -> None:
    if not condition:
        raise GateError(code, detail)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


def loads(text: str):
    require(isinstance(text, str), 'JSON transport must be text')
    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(
                              ValueError(f'non-finite JSON: {x}')))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, GateError):
            raise
        raise GateError('READ_FAILED', f'invalid JSON: {exc}') from exc


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def instant(value: str) -> datetime:
    require(isinstance(value, str), 'timestamp must be text')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise GateError('READ_FAILED', 'invalid timestamp') from exc
    require(dt.tzinfo is not None and dt.utcoffset() is not None, 'timezone required')
    return dt.astimezone(timezone.utc)


def identity(*parts) -> str:
    # Keep default json.dumps spaces: this is the existing backend's identity.
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


def item_ids(value, *, nonempty=False) -> list[str]:
    require(isinstance(value, list), 'item_ids must be an array')
    require(all(isinstance(x, str) and ID.fullmatch(x) for x in value), 'invalid item ID')
    require(len(value) == len(set(value)), 'duplicate item IDs')
    require(not nonempty or bool(value), 'empty delivery')
    return value


def visible_receipt(value: dict) -> dict:
    require(isinstance(value, dict), 'receipt must be an object')
    ids = item_ids(value.get('item_ids'), nonempty=True)
    require(value.get('packet_id') == identity('packet', sorted(ids)), 'receipt identity mismatch')
    return {'packet_id': value['packet_id'], 'item_ids': list(ids)}


def persisted_receipt(text: str, now: str) -> dict | None:
    """Input is persisted plain text, not a Markdown presentation wrapper."""
    try:
        require(isinstance(text, str), 'receipt property unreadable')
        if not text.strip():
            return None
        try:
            result = loads(text)
        except GateError:
            values = [x.strip() for x in text.split(',')]
            require(values and all(ID.fullmatch(x) for x in values), 'malformed receipt')
            result = values
        if isinstance(result, list):
            require(all(isinstance(x, str) and ID.fullmatch(x) for x in result), 'invalid legacy IDs')
            return {'item_ids': list(dict.fromkeys(result)), 'legacy': True}
        require(isinstance(result, dict), 'double-serialized receipt is not an object')
        ids = result.get('item_ids')
        require(isinstance(ids, list) and all(isinstance(x, str) for x in ids), 'invalid item_ids')
        require(all(ID.fullmatch(x) for x in ids), 'invalid item ID')
        require(result.get('packet_id') == identity('packet', sorted(set(ids))), 'receipt identity mismatch')
        require('delivered_at' in result, 'explicit delivered_at required')
        if result['delivered_at'] is None:
            require(result.get('delivery_evidence') == 'prior_complete_report', 'unknown time needs evidence')
        else:
            require(instant(result['delivered_at']) <= instant(now), 'future delivery timestamp')
        if result.get('report_ref') is not None:
            require(isinstance(result['report_ref'], str) and bool(result['report_ref'].strip()), 'invalid report_ref')
        return deepcopy(result)
    except GateError as exc:
        raise GateError('RECEIPT_READ_FAILED', exc.detail) from exc


def validate_packet(packet: dict, now: str, *, fresh=True) -> dict:
    require(isinstance(packet, dict), 'Packet must be an object')
    require(all(k in packet for k in DELIVERY_KEYS), 'missing delivery field')
    require(type(packet['version']) is int and packet['version'] == 2, 'Packet v2 required')
    require(packet['presentation'] == 'signals-and-current-structure-v1', 'signal presentation required')
    ids = item_ids(packet['acknowledgement_ids'])
    require(packet['packet_id'] == identity('packet', sorted(ids)), 'Packet identity mismatch')
    require(packet['packet_end'] == packet['packet_id'], 'packet_end mismatch')
    items, batch = packet['report_items'], packet['batch']
    require(isinstance(items, list) and isinstance(batch, dict), 'invalid batch/items')
    require(all(isinstance(i, dict) for i in items), 'invalid item')
    require([i.get('id') for i in items] == ids, 'ordered ID coverage mismatch')
    require(type(batch.get('included')) is int and batch['included'] == len(items), 'included mismatch')
    require(type(batch.get('remaining')) is int and batch['remaining'] >= 0, 'invalid remaining')
    require(type(batch.get('total_pending')) is int and
            batch['total_pending'] == len(items) + batch['remaining'], 'pending count mismatch')
    require(batch.get('order') == 'oldest_first', 'unsupported batch order')
    as_of, current = instant(packet['as_of']), instant(now)
    require(as_of <= current, 'future Packet as_of')
    for item in items:
        require(item.get('kind') == 'RAW_SIGNAL', 'non-RAW_SIGNAL in delivery')
        require(isinstance(item.get('line'), str), 'missing item line')
        match = LINE.fullmatch(item['line'])
        require(match is not None, 'incomplete or invalid item line')
        ticker, tf, signal, number, unit, bar, confirmed = match.groups()
        require(ticker == item.get('ticker') and tf == item.get('origin_tf') and
                signal == item.get('signal') and unit == item.get('value_unit'), 'line/field mismatch')
        price = item.get('price')
        require(type(price) in (int, float) and math.isfinite(price), 'invalid signal value')
        try:
            require(Decimal(number) == Decimal(str(price)), 'line/value mismatch')
        except InvalidOperation as exc:
            raise GateError('READ_FAILED', 'invalid line value') from exc
        require(bar == item.get('bar_at') and confirmed == item.get('confirmed_at'), 'line/time mismatch')
        require(instant(bar) < instant(confirmed) <= as_of, 'invalid/future confirmation')
        require('market_timezone' in item, 'missing market timezone')
        zone = item['market_timezone']
        if zone not in (None, 'unknown'):
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
                raise GateError('READ_FAILED', 'invalid market timezone') from exc
    # Validate ALL lines before checking delivery overlap, even for covered IDs.
    if fresh:
        require((current - as_of).total_seconds() <= 7200, 'Packet older than two hours', 'PACKET_NOT_READY')
    return packet


def make_receipt(ids: list[str]) -> dict | None:
    item_ids(ids)
    return {'packet_id': identity('packet', sorted(ids)), 'item_ids': ids[:]} if ids else None


def seal(packet: dict, displayed_ids: list[str], now: str, *, quarantine=None) -> str:
    """Append the result to the SAME final response as the rendered raw events."""
    validate_packet(packet, now)
    item_ids(displayed_ids, nonempty=True)
    selected = set(displayed_ids)
    require([i for i in packet['acknowledgement_ids'] if i in selected] == displayed_ids,
            'displayed IDs must be an exact parent-order subset')
    evidence = {'schema_version': 1, 'prepared_at': now,
                'source_packet': {k: deepcopy(packet[k]) for k in DELIVERY_KEYS},
                'displayed_ids': displayed_ids,
                'quarantine_receipts': [visible_receipt(x) for x in quarantine or []],
                'evidence_end': make_receipt(displayed_ids)['packet_id']}
    raw = compact(evidence).encode('utf-8')
    require(len(raw) <= MAX_EVIDENCE_BYTES, 'evidence budget exceeded')
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode('ascii')
    digest = hashlib.sha256(raw).hexdigest()
    return f'{MARKER}{digest}:{encoded}\n{RECEIPT}{compact(make_receipt(displayed_ids))}'


def unseal(content: str, now: str) -> dict:
    require(isinstance(content, str) and len(content.encode()) <= MAX_RESULT_BYTES, 'invalid result size')
    lines = content.splitlines()
    proof_lines = [line for line in lines if line.startswith(MARKER)]
    receipt_lines = [line for line in lines if line.startswith(RECEIPT)]
    require(len(proof_lines) == 1 and len(receipt_lines) == 1, 'one complete proof and Receipt required')
    require(lines.index(proof_lines[0]) < lines.index(receipt_lines[0]), 'Receipt must follow evidence')
    try:
        digest, encoded = proof_lines[0][len(MARKER):].split(':', 1)
        require(re.fullmatch(r'[0-9a-f]{64}', digest) is not None, 'invalid evidence digest')
        require(len(encoded) <= MAX_EVIDENCE_BYTES * 2, 'encoded evidence too large')
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_EVIDENCE_BYTES + 1)
        require(len(raw) <= MAX_EVIDENCE_BYTES and decoder.eof and not decoder.unused_data
                and not decoder.unconsumed_tail, 'truncated/oversized evidence')
        require(hashlib.sha256(raw).hexdigest() == digest, 'evidence digest mismatch')
        evidence = loads(raw.decode('utf-8'))
    except (ValueError, binascii.Error, zlib.error, UnicodeError) as exc:
        if isinstance(exc, GateError):
            raise
        raise GateError('EVIDENCE_READ_FAILED', 'corrupt evidence encoding') from exc
    require(isinstance(evidence, dict) and evidence.get('schema_version') == 1, 'unsupported evidence')
    require(instant(evidence.get('prepared_at')) <= instant(now), 'future report preparation')
    parent = validate_packet(evidence.get('source_packet'), evidence['prepared_at'])
    ids = item_ids(evidence.get('displayed_ids'), nonempty=True)
    require([i for i in parent['acknowledgement_ids'] if i in set(ids)] == ids, 'invalid displayed subset')
    receipt = visible_receipt(loads(receipt_lines[0][len(RECEIPT):]))
    require(receipt == make_receipt(ids) and evidence.get('evidence_end') == receipt['packet_id'],
            'evidence/Receipt mismatch')
    return {'receipt': receipt, 'report_items': [i for i in parent['report_items'] if i['id'] in set(ids)],
            'prepared_at': evidence['prepared_at'], 'parent_packet_id': parent['packet_id'],
            'quarantine_receipts': [visible_receipt(x) for x in evidence.get('quarantine_receipts', [])]}


def extract_legacy_receipt(content: str) -> dict | None:
    """A receipt without a body is UNCERTAIN, never acknowledgement evidence."""
    candidates = []
    for line in content.splitlines():
        value = line.strip()
        if value.startswith(RECEIPT):
            value = value[len(RECEIPT):].strip()
        elif value.startswith('Receipt '):
            value = value[len('Receipt '):].strip()
        value = value.strip('`')
        if value.startswith('{') and '"packet_id"' in value and '"item_ids"' in value:
            candidates.append(visible_receipt(loads(value)))
    require(len(candidates) <= 1, 'ambiguous legacy receipts', 'EVIDENCE_READ_FAILED')
    return candidates[0] if candidates else None


def read_prior_result(result: dict | None, now: str, *, current_run_id=None, _depth=0) -> dict:
    """Use an actual Automations.peek latest_result, not a reconstructed report.

created_at is record provenance, NOT delivered_at. A completed run is not proof
that the user received it. The only evidence used is its complete response body.
"""
    require(_depth <= 1, 'nested recovery envelope', 'EVIDENCE_READ_FAILED')
    if result is None:
        return {'kind': 'unavailable', 'ids': []}
    require(isinstance(result, dict), 'invalid result', 'EVIDENCE_READ_FAILED')
    require(result.get('content_truncated') is False, 'result truncated/unverifiable', 'EVIDENCE_READ_FAILED')
    run_id = result.get('run_id')
    require(isinstance(run_id, str) and bool(run_id), 'missing result run ID', 'EVIDENCE_READ_FAILED')
    require(run_id != current_run_id, 'current run or retry cannot acknowledge itself', 'EVIDENCE_READ_FAILED')
    require(instant(result.get('created_at')) < instant(now), 'result is not prior', 'EVIDENCE_READ_FAILED')
    content = result.get('content_text')
    require(isinstance(content, str) and len(content.encode()) <= MAX_RESULT_BYTES,
            'result body unreadable/oversized', 'EVIDENCE_READ_FAILED')
    if RECOVERY in content:
        try:
            lines = [x for x in content.splitlines() if x.startswith(RECOVERY)]
            require(len(lines) == 1 and MARKER not in content,
                    'ambiguous recovery and current delivery evidence')
            digest, encoded = lines[0][len(RECOVERY):].split(':', 1)
            require(len(encoded) <= MAX_RESULT_BYTES * 2, 'recovery too large')
            compressed = base64.b64decode(encoded, validate=True)
            decoder = zlib.decompressobj()
            raw = decoder.decompress(compressed, MAX_RESULT_BYTES + 1)
            require(len(raw) <= MAX_RESULT_BYTES and decoder.eof and
                    not decoder.unused_data and not decoder.unconsumed_tail,
                    'truncated/oversized recovery')
            require(hashlib.sha256(raw).hexdigest() == digest, 'recovery digest mismatch')
            original = loads(raw.decode('utf-8'))
            require(isinstance(original, dict), 'invalid original result')
            require(RECOVERY not in original.get('content_text', ''), 'nested recovery')
            evidence = read_prior_result(original, result['created_at'],
                                         current_run_id=current_run_id, _depth=_depth + 1)
            require(evidence['kind'] == 'complete', 'recovery requires complete original report')
            evidence['recovery'] = True
            return evidence
        except (GateError, ValueError, zlib.error, UnicodeError, TypeError) as exc:
            raise GateError('EVIDENCE_READ_FAILED', str(exc)) from exc
    if MARKER in content:
        try:
            evidence = unseal(content, now)
            require(instant(evidence['prepared_at']) <= instant(result['created_at']), 'evidence newer than result')
        except GateError as exc:
            raise GateError('EVIDENCE_READ_FAILED', exc.detail) from exc
        return {'kind': 'complete', 'ids': evidence['receipt']['item_ids'], 'evidence': evidence,
                'source_run_id': run_id}
    legacy = extract_legacy_receipt(content)
    if legacy:
        return {'kind': 'unconfirmed', 'ids': legacy['item_ids'], 'receipt': legacy}
    # An error without preserved prior evidence cannot reset delivery history.
    require(not result.get('is_failure_message') and not any(
        code in content for code in ('WRITE_FAILED', 'EVIDENCE_READ_FAILED', 'RECEIPT_READ_FAILED')),
        'failed prior report lost its evidence; manual recovery required', 'EVIDENCE_READ_FAILED')
    return {'kind': 'no_claim', 'ids': []}


def recovery_suffix(result: dict, now: str, *, current_run_id=None) -> str:
    """Retain the complete ORIGINAL report on an acknowledgement-write failure.

    This is not a new signal report or a new Receipt. On repeated failures we
    flatten to the original result rather than accumulating nested copies.
    """
    evidence = read_prior_result(result, now, current_run_id=current_run_id)
    require(evidence['kind'] == 'complete', 'cannot preserve receipt-only evidence')
    original = deepcopy(result)
    if RECOVERY in original['content_text']:
        line = next(x for x in original['content_text'].splitlines() if x.startswith(RECOVERY))
        _, encoded = line[len(RECOVERY):].split(':', 1)
        # The bounded read above already validated this exact payload.
        original = loads(zlib.decompress(base64.b64decode(encoded, validate=True)).decode('utf-8'))
    raw = compact(original).encode('utf-8')
    require(len(raw) <= MAX_RESULT_BYTES, 'recovery too large')
    return RECOVERY + hashlib.sha256(raw).hexdigest() + ':' + base64.b64encode(zlib.compress(raw, 9)).decode('ascii')


def plan(packet: dict, delivered_text: str, now: str, *, latest_result=None,
         current_run_id=None, quarantine=None, first_delivery_authorized=False) -> dict:
    validate_packet(packet, now)
    persisted = persisted_receipt(delivered_text, now)
    prior = read_prior_result(latest_result, now, current_run_id=current_run_id)
    old = list(dict.fromkeys((persisted or {}).get('item_ids', [])))
    proven = prior['ids'] if prior['kind'] == 'complete' else []
    covered = set(old) | set(proven)
    claims = list(quarantine or []) + prior.get('evidence', {}).get('quarantine_receipts', [])
    if prior['kind'] == 'unconfirmed':
        claims.append(prior['receipt'])
    checked_claims = {}
    uncertain = set()
    for value in claims:
        checked = visible_receipt(value)
        checked_claims[checked['packet_id']] = checked
        uncertain.update(checked['item_ids'])
    # Confirmed evidence takes precedence; quarantine never consumes the outbox.
    uncertain -= covered
    pending = packet['acknowledgement_ids']
    withheld = [i for i in pending if i in uncertain]
    selected = [i for i in pending if i not in covered and i not in uncertain]
    ack = None
    if proven and not set(proven).issubset(set(old)):
        # Preserve ALL persisted evidence, not only its current Packet overlap.
        # Some already-acknowledged records may be absent from a bounded batch.
        union = list(dict.fromkeys(old + proven))
        ack = {**make_receipt(union), 'delivered_at': None, 'delivery_evidence': 'prior_complete_report'}
    established = bool(old or proven or first_delivery_authorized)
    if selected and not established:
        raise GateError('DELIVERY_UNCONFIRMED', 'no established prior delivery or first-run authorization')
    return {'status': 'READY' if selected else ('DELIVERY_UNCONFIRMED' if withheld else 'SILENT'),
            'ack_receipt': ack, 'covered_ids': [i for i in pending if i in covered],
            'unconfirmed_ids': withheld, 'report_items': [i for i in packet['report_items'] if i['id'] in set(selected)],
            'receipt': make_receipt(selected), 'source_packet_id': packet['packet_id'],
            'batch_remaining': packet['batch']['remaining'], 'prior_kind': prior['kind'],
            'quarantine_receipts': list(checked_claims.values())}


def notion_transport(receipt: dict, now: str) -> str:
    persisted_receipt(compact(receipt), now)
    # Equivalent to JSON.stringify once, then the connector's inline escaping.
    return re.sub(r'[\\*~`$\[\]<>{}|^]', lambda m: '\\' + m.group(), compact(receipt))


def verify_readback(expected: dict, actual: str, now: str, *, notion_markdown=False) -> None:
    if notion_markdown:
        # Only remove the known presentation layer, exactly once. Never strip
        # arbitrary JSON escapes. Raw SQL plain text must use the default False.
        actual = re.sub(r'\\([\\*_~`$\[\]<>{}|^:])', lambda m: m.group(1), actual)
    parsed = persisted_receipt(actual, now)
    require(parsed == expected, 'persisted receipt differs from requested object/order', 'WRITE_FAILED')


def persist_verified(receipt: dict, *, write, read, now: str) -> None:
    """Adapter contract: write a STRING to Delivered IDs, read plain text back.

The agent can perform these two connector operations itself. This function
exercises the same exact comparison/retry protocol in offline integration tests.
"""
    transport = notion_transport(receipt, now)
    last = None
    for _ in range(2):
        try:
            write(transport)
            verify_readback(receipt, read(), now)
            return
        except Exception as exc:
            last = exc
    raise GateError('WRITE_FAILED', f'write/readback failed after two attempts: {last}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'seal', 'inspect', 'transport', 'verify', 'recovery'))
    parser.add_argument('--packet', type=Path)
    parser.add_argument('--delivered', type=Path)
    parser.add_argument('--latest-result', type=Path)
    parser.add_argument('--quarantine', type=Path)
    parser.add_argument('--ids', type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--actual', type=Path)
    parser.add_argument('--current-run-id')
    parser.add_argument('--now', required=True)
    parser.add_argument('--first-delivery-authorized', action='store_true')
    parser.add_argument('--notion-markdown', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    def read(path, *, plain=False):
        require(path is not None, 'missing CLI input')
        text = path.read_text(encoding='utf-8')
        return text if plain else loads(text)
    try:
        if args.command == 'plan':
            result = plan(read(args.packet), read(args.delivered, plain=True), args.now,
                          latest_result=read(args.latest_result) if args.latest_result else None,
                          current_run_id=args.current_run_id,
                          quarantine=read(args.quarantine) if args.quarantine else [],
                          first_delivery_authorized=args.first_delivery_authorized)
        elif args.command == 'seal':
            result = seal(read(args.packet), read(args.ids), args.now,
                          quarantine=read(args.quarantine) if args.quarantine else [])
        elif args.command == 'inspect':
            result = read_prior_result(read(args.latest_result), args.now, current_run_id=args.current_run_id)
        elif args.command == 'recovery':
            result = recovery_suffix(read(args.latest_result), args.now, current_run_id=args.current_run_id)
        elif args.command == 'transport':
            result = notion_transport(read(args.receipt), args.now)
        else:
            verify_readback(read(args.receipt), read(args.actual, plain=True), args.now,
                            notion_markdown=args.notion_markdown)
            result = {'status': 'VERIFIED'}
        output = result if isinstance(result, str) else compact(result)
        if args.out:
            args.out.write_text(output + '\n', encoding='utf-8')
        else:
            print(output)
        return 0
    except (GateError, OSError) as exc:
        print(compact({'status': getattr(exc, 'code', 'READ_FAILED'), 'detail': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
