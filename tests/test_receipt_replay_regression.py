"""Actual production packet/receipt/state functions, external writes mocked."""
import copy
import json
import unittest
from unittest.mock import Mock
from market_signal_monitor.report_packet import packet, delivery_receipt
from market_signal_monitor.state_engine import acknowledge, seed, identity

NOW='2026-10-18T06:00:00Z'
SINCE='2026-10-17T00:00:00Z'
def event(n):
    return dict(id=identity('replay',n),kind='RAW_SIGNAL',ticker='MSFT',at=SINCE,origin_tf='1D',signal='Sell',price=420+n,source_event_id=f'source-{n}')
def read(value):
    client=Mock()
    client.request.return_value={'properties':{'Packet':{'type':'rich_text','rich_text':[]},'Delivered IDs':{'type':'rich_text','rich_text':[{'plain_text':json.dumps(value)}]}}}
    return delivery_receipt(client,'report')
class ReceiptReplayRegression(unittest.TestCase):
    def test_persisted_receipt_does_not_itself_consume_pending(self):
        state=seed({'Ticker':'MSFT'});state['pending']=[event(1),event(2)]
        first=packet([state],now=NOW,since=SINCE)
        receipt=read(dict(packet_id=first['packet_id'],item_ids=first['acknowledgement_ids'],delivered_at=None,delivery_evidence='prior_complete_report'))
        retry=packet([state],now=NOW,since=SINCE)
        self.assertEqual(retry['acknowledgement_ids'],list(receipt)) # reproduced window
        before=copy.deepcopy(state)
        consumed=acknowledge(state,receipt,confirmed_at=NOW)
        self.assertEqual(state,before)
        self.assertEqual(packet([consumed],now=NOW,since=SINCE)['acknowledgement_ids'],[])
        self.assertEqual(acknowledge(consumed,receipt,confirmed_at=NOW),consumed)
        consumed['pending'].append(event(3))
        self.assertEqual(packet([consumed],now=NOW,since=SINCE)['acknowledgement_ids'],[event(3)['id']])
    def test_partial_output_cannot_keep_original_packet_hash(self):
        ids=[event(1)['id'],event(2)['id']]
        with self.assertRaisesRegex(ValueError,'does not match'):
            read(dict(packet_id=identity('packet',sorted(ids)),item_ids=ids[1:],delivered_at=None,delivery_evidence='prior_complete_report'))
    def test_invalid_receipt_does_not_remove_pending(self):
        state=seed({'Ticker':'MSFT'});state['pending']=[event(1)]
        original=copy.deepcopy(state)
        with self.assertRaises(ValueError):read({'packet_id':'bad','item_ids':[event(1)['id']],'delivered_at':None,'delivery_evidence':'prior_complete_report'})
        self.assertEqual(state,original)
