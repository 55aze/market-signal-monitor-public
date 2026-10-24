import json
from pathlib import Path
from market_signal_monitor.state_engine import identity
P=Path(__file__).parent
A,B,C=[identity('eval-event',n) for n in range(3)]
def case(n, name, ids, delivered, expected_ids, status, **kw):
    packet=dict(version=2,presentation='signals-and-current-structure-v1',as_of='2026-10-18T06:00:00Z',packet_id=identity('packet',sorted(ids)),packet_end=identity('packet',sorted(ids)),batch={'included':len(ids)},acknowledgement_ids=ids,report_items=[dict(id=i,kind='RAW_SIGNAL',ticker='MSFT',origin_tf='1D',signal='Sell',price=420+j,confirmed_at='2026-10-16T20:00:00Z',market_timezone='America/New_York') for j,i in enumerate(ids)])
    receipt=None if delivered is None else dict(packet_id=identity('packet',sorted(set(delivered))),item_ids=delivered,delivered_at=None,delivery_evidence='prior_complete_report')
    row=dict(case_id=n,name=name,now='2026-10-18T06:10:00Z',packet=packet,persisted_receipt=receipt,receipt_read='success',prior_report=None,prior_history='unavailable',first_delivery_authorized=False,ack_write='not_attempted',operational_changes=False)
    row.update(kw)
    return row,dict(case_id=n,emit_ids=expected_ids,status=status)
cases=[]; expected=[]
def add(*a,**kw):
    r,e=case(*a,**kw);cases.append(r);expected.append(e)
add(1,'first authorized',[A,B],None,[A,B],'REPORT',first_delivery_authorized=True)
add(2,'persisted all; new chat',[A,B],[A,B],[],'SILENT')
add(3,'reordered persisted IDs',[A,B],[B,A],[],'SILENT')
add(4,'mixed acknowledged and new',[A,B,C],[A,B],[],'ACK_PENDING')
add(5,'new only after consumption',[C],[A,B],[C],'REPORT')
add(6,'empty packet',[],[A,B],[],'SILENT')
add(7,'receipt network failure',[A,B],None,[],'RECEIPT_READ_FAILED',receipt_read='network_error')
add(8,'truncated receipt',[A,B],None,[],'RECEIPT_READ_FAILED',receipt_read='truncated')
add(9,'malformed receipt',[A,B],[A,B],[],'RECEIPT_READ_FAILED');cases[-1]['persisted_receipt']['packet_id']='wrong'
add(10,'truncated packet despite acknowledged IDs',[A,B],[A,B],[],'READ_FAILED');del cases[-1]['packet']['packet_end']
add(11,'duplicate packet IDs',[A,A],[A],[],'READ_FAILED')
add(12,'unreadable history and empty receipt',[A,B],None,[],'DELIVERY_UNCONFIRMED')
prior={'complete':True,'visible_ids':[A,B],'receipt_ids':[A,B],'packet_id':identity('packet',sorted([A,B]))}
add(13,'complete prior; write fails',[A,B],None,[],'WRITE_FAILED',prior_report=prior,prior_history='readable',ack_write='failed_after_retry')
add(14,'complete prior; write verified',[A,B],[A,B],[],'SILENT',prior_report=prior,prior_history='readable',ack_write='verified')
add(15,'receipt-only prior',[A,B],None,[],'DELIVERY_UNCONFIRMED',prior_history='receipt_only')
add(16,'old prior must not replace outstanding receipt',[A,B],[A,B],[],'SILENT',prior_report={'complete':True,'visible_ids':[C],'receipt_ids':[C],'packet_id':identity('packet',[C])},prior_history='readable')
add(17,'legacy array receipt',[A,B],[A,B],[],'SILENT');cases[-1]['persisted_receipt']=[A,B]
add(18,'legacy CSV receipt',[A,B],[A,B],[],'SILENT');cases[-1]['persisted_receipt']=','.join([A,B])
add(19,'fresh packet new as_of same acknowledged signals',[A,B],[A,B],[],'SILENT');cases[-1]['packet']['as_of']='2026-10-18T06:09:00Z'
add(20,'acknowledged packet plus operational error',[A,B],[A,B],[],'OPERATIONAL',operational_changes=True)
(P/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
(P/'expected.json').write_text(json.dumps(expected,indent=2))
print('Created 20 synthetic tool-snapshot scenarios, NOT production traces')
