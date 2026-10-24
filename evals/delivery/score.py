"""Independent expected decisions; never edit model outputs after scoring."""
import json
from pathlib import Path
P=Path(__file__).parent

def score(path):
    cases={c['case_id']:c for c in json.loads((P/'cases.json').read_text())}
    expected={e['case_id']:e for e in json.loads((P/'expected.json').read_text())}
    actual=json.loads(path.read_text())
    assert len(actual)==len(expected) and {r['case_id'] for r in actual}==set(expected)
    results=[]
    for r in actual:
        e,c=expected[r['case_id']],cases[r['case_id']]
        emitted=r['emit_ids']; receipt=r['receipt_ids']; write=r['receipt_write_ids']
        persisted=c['persisted_receipt']
        persisted=(persisted.get('item_ids',[]) if isinstance(persisted,dict) else persisted.split(',') if isinstance(persisted,str) else persisted or [])
        ids=c['packet']['acknowledgement_ids']
        checks={
            'decision':r['status']==e['status'],
            'exact_new_signals':emitted==e['emit_ids'],
            'receipt_covers_output':receipt==(emitted if r['status']=='REPORT' else []),
            'no_current_ack':not bool(set(write)&set(emitted)),
            'preserve_unconsumed_receipt':not(set(persisted)&set(ids)) or not write or set(write)==set(persisted),
            'receipt_matches_original_batch':not receipt or receipt==ids,
        }
        results.append(dict(case_id=r['case_id'],name=c['name'],passed=all(checks.values()),checks=checks,expected=e,actual=r))
    return dict(arm=path.stem,passed=sum(r['passed'] for r in results),total=len(results),results=results)

if __name__=='__main__':
    scores=[score(P/f'llm_{arm}.json') for arm in ('before','after')]
    (P/'scores.json').write_text(json.dumps(scores,ensure_ascii=False,indent=2))
    for s in scores:
        print(s['arm'],f"{s['passed']}/{s['total']}")
        for r in s['results']:
            if not r['passed']: print(' ',r['case_id'],r['name'],[k for k,v in r['checks'].items() if not v])
