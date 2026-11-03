#!/usr/bin/env python3
"""Presentation-only renderer. Input: validated Packet and delivery plan.

No network, Notion writes, acknowledgement, new enrollment, or state decisions.
All raw events and all context rows are server-rendered; JavaScript only filters
context. Default context is expanded/all, including when JS is unavailable.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from html import escape
from itertools import groupby
import json
from pathlib import Path
from zoneinfo import ZoneInfo

TFS = ('1W', '1D', '4H', '30m')
RANK = {tf: i for i, tf in enumerate(TFS)}

def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None: raise ValueError('Timezone required')
    return dt

def relative(value, as_of, zone):
    try:
        dt, now = stamp(value), stamp(as_of)
        seconds = (now-dt).total_seconds()
        if seconds < 0: return '待核'
        if seconds < 60: return '刚刚'
        if seconds < 3600: return f'{int(seconds//60)}分钟前'
        if seconds < 86400: return f'{int(seconds//3600)}小时前'
        if seconds < 604800: return f'{int(seconds//86400)}天前'
        if not zone or zone == 'unknown': return '时区待核'
        local = dt.astimezone(ZoneInfo(zone))
        return local.strftime('%Y/%m/%d' if local.year != now.year else '%m/%d')
    except (ValueError, TypeError, AttributeError, KeyError):
        return '待核'

def local(value, zone):
    if not value: return '缺'
    try:
        if not zone or zone == 'unknown': return str(value) + '（市场时区待核）'
        return stamp(value).astimezone(ZoneInfo(zone)).strftime('%Y-%m-%d %H:%M %Z')
    except (ValueError, TypeError, AttributeError, KeyError): return '待核'

def esc(value): return escape(str(value), quote=True)

def direction(value):
    if value not in ('Bottom','Sell'): raise ValueError('Unsupported raw direction')
    return ('抄底','bottom') if value == 'Bottom' else ('卖出','sell')

def status(cell, zone):
    if not cell.get('structure'): return '缺'
    f=cell.get('freshness')
    if f=='current': return ''
    if f=='stale':
        try: return '旧 '+stamp(cell['confirmed_at']).astimezone(ZoneInfo(zone)).strftime('%m/%d')
        except (ValueError, TypeError, KeyError): return '旧 · 待核'
    return '待核'

def cell_html(row, tf, as_of):
    cell=row.get('cells',{}).get(tf,{})
    zone=row.get('market_timezone')
    state=cell.get('structure') or '缺'
    badge=status(cell, zone)
    badge=f'<span class="quality">{esc(badge)}</span>' if badge else ''
    signal_lines=[]
    signals=cell.get('signals',[])
    for sig in signals:
        text, cls=direction(sig['signal'])
        anchor=(tf==row.get('highest_signal_tf') and sig.get('confirmation_at')==row.get('anchor_confirmed_at'))
        exact=local(sig.get('confirmation_at'),zone)
        value=f'{text} {relative(sig.get("confirmation_at"),as_of,zone)}'
        if anchor: value=f'<strong>{esc(value)}</strong>'
        else: value=esc(value)
        signal_lines.append(f'<div class="signal {cls}" title="{esc(exact)}">{value}</div>')
    audit=[f'结构K线：{local(cell.get("bar_at"),zone)}',f'结构确认：{local(cell.get("confirmed_at"),zone)}',f'来源：{cell.get("stream", "缺")}',f'新鲜度：{cell.get("freshness", "missing")}']
    for sig in signals:
        label=direction(sig['signal'])[0]
        audit.extend([f'{label} K线：{local(sig.get("timestamp"),zone)}',f'{label} 确认：{local(sig.get("confirmation_at"),zone)}'])
    return f'''<div class="tf-cell" data-tf="{tf}" data-stream="{esc(cell.get('stream',''))}">
<div class="tf-label">{tf}</div><div class="cell-content">
<div class="ladder">{esc(state)}</div>{''.join(signal_lines)}
<div class="cell-time">{esc(relative(cell.get('confirmed_at'),as_of,zone))} {badge}</div>
<details class="audit"><summary>时间与来源</summary>{''.join('<div>'+esc(a)+'</div>' for a in audit)}</details>
</div></div>'''

CSS='''
:root{color-scheme:light dark;--bg:#fff;--fg:#18202a;--muted:#526174;--line:#dce2e8;--tint:#f4f6f8;--bottom:#b91c1c;--sell:#14723d}
@media(prefers-color-scheme:dark){:root{--bg:#171b22;--fg:#f1f4f8;--muted:#b9c3d0;--line:#414b5a;--tint:#232a34;--bottom:#ff9e9e;--sell:#84d9a5}}
*{box-sizing:border-box}html{overflow-y:auto;scroll-behavior:auto}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}main{width:100%;padding:16px;min-width:0;height:auto;max-height:none;overflow:visible}h1{font-size:22px;margin:0 0 6px}h2{font-size:19px;margin:28px 0 8px}h3{font-size:16px;margin:22px 0 4px}h4{font-size:15px;margin:14px 0 6px}.meta,.cell-time{color:var(--muted);font-size:12px}.jump{display:inline-block;margin:8px 0;padding:9px 13px;border:1px solid var(--line);border-radius:8px;color:var(--fg);text-decoration:none}button,input,select{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);padding:10px;border-radius:7px;min-height:44px;max-width:100%}button{cursor:pointer}a:focus-visible,summary:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #5289cf;outline-offset:3px}.filters{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.filters label{display:flex;flex-direction:column;font-size:12px;gap:4px;min-width:0}.filters input{width:160px}.count{margin:8px 0;color:var(--muted)}.column-head,.ticker-block{display:grid;grid-template-columns:80px repeat(4,minmax(0,1fr));gap:0}.column-head{background:var(--tint);font-weight:600;border-radius:5px}.column-head>div{padding:8px}.ticker-block{border-bottom:1px solid var(--line);height:auto;max-height:none;overflow:visible}.ticker{font-weight:700;padding:12px 8px;overflow-wrap:anywhere}.tf-cell{padding:12px 8px;min-width:0;overflow:visible}.tf-label{display:none}.cell-content{min-width:0}.ladder{white-space:normal;overflow-wrap:anywhere;word-break:normal;line-height:1.8;font-weight:500}.signal{display:block;white-space:normal;overflow-wrap:anywhere}.bottom{color:var(--bottom)}.sell{color:var(--sell)}.quality{display:inline-block;border:1px solid var(--line);padding:0 4px;border-radius:4px}.audit{margin-top:5px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}.audit summary{cursor:pointer;min-height:30px;display:list-item}.audit div{margin:4px 0}.event{display:grid;grid-template-columns:80px 50px 60px minmax(65px,1fr) minmax(100px,1fr);gap:8px;padding:9px 4px;border-bottom:1px solid var(--line);min-width:0}.event>*{min-width:0;overflow-wrap:anywhere}.empty{padding:12px;border:1px dashed var(--line)}[hidden]{display:none!important}footer{padding:20px 0 8px;color:var(--muted);font-size:12px}.market,.date-group,#structures,#events{height:auto;max-height:none;overflow:visible}
@media(max-width:850px){main{padding:12px}.column-head{display:none}.ticker-block{display:block;border:1px solid var(--line);border-radius:9px;margin-bottom:12px}.ticker{background:var(--tint);border-radius:9px 9px 0 0;padding:10px 12px}.tf-cell{display:grid;grid-template-columns:38px minmax(0,1fr);padding:10px 12px;gap:8px;border-top:1px solid var(--line)}.tf-label{display:block;font-weight:600}.ladder{font-size:15px}.event{grid-template-columns:65px 40px 46px minmax(55px,1fr);font-size:13px}.event .event-time{grid-column:2/-1}.filters{display:grid;grid-template-columns:1fr 1fr}.filters label:last-of-type{grid-column:1/-1}.filters input{width:100%}}
'''
JS='''
(()=>{const blocks=[...document.querySelectorAll('.ticker-block')],search=document.querySelector('#search'),market=document.querySelector('#market'),scope=document.querySelector('#scope');
function apply(){let count=0;const q=search.value.trim().toUpperCase();for(const b of blocks){const show=(!q||b.dataset.ticker.toUpperCase().includes(q))&&(!market.value||b.dataset.market===market.value)&&(scope.value==='all'||b.dataset.related==='true');b.hidden=!show;if(!show)b.querySelectorAll('details').forEach(d=>d.open=false);count+=show;}
for(const g of document.querySelectorAll('.date-group,.market'))g.hidden=![...g.querySelectorAll('.ticker-block')].some(b=>!b.hidden);
document.querySelector('#count').textContent=`显示 ${count} / ${blocks.length} 个标的 · ${count*4} 个周期单元格`;
document.querySelector('#empty').hidden=count!==0;}
for(const el of [search,market,scope])el.addEventListener('input',apply);
document.querySelector('#reset').addEventListener('click',()=>{search.value='';market.value='';scope.value='all';apply();});apply();})();
'''

def render(packet,plan,*,context_only=False):
    watch=packet.get('watch_context')
    if not isinstance(watch,dict) or not isinstance(watch.get('rows'),list): raise ValueError('Context unreadable, not zero rows')
    rows=watch['rows']; n=watch.get('row_count')
    if type(n) is not int or n!=len(rows): raise ValueError('Context count mismatch')
    if len({r['ticker'] for r in rows})!=n: raise ValueError('Duplicate context tickers')
    for r in rows:
        if set(r.get('cells',{}))!=set(TFS): raise ValueError('Four explicit cells required; preserve missing labels')
    events=plan.get('report_items',[])
    if not context_only:
        ids=[e['id'] for e in events]
        if len(set(ids))!=len(ids) or ids!=(plan.get('receipt') or {}).get('item_ids',[]): raise ValueError('Display/receipt ID mismatch')
    as_of=packet['as_of'];stamp(as_of)
    related={e['ticker'] for e in events}
    title='近期信号 · 结构快照' if context_only else f'新信号（{len(events)}）'
    output=[f'<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>{CSS}</style></head><body><main><h1>{title}</h1><div class="meta">快照生成：{esc(as_of)} · 结构变化：未核验</div><a class="jump" href="#structures">查看四周期结构（{n}） ↓</a>']
    if context_only: output.append('<p class="meta">仅恢复已存快照的可见内容；不生成新信号回执，不更新交付状态。</p>')
    else:
        output.append('<section id="events" aria-label="全部新信号">')
        key=lambda e:(e.get('market_timezone') or 'unknown',RANK[e['origin_tf']],-stamp(e['confirmed_at']).timestamp(),e['id'])
        for zone,items in groupby(sorted(events,key=key),key=lambda e:e.get('market_timezone') or 'unknown'):
            output.append(f'<h3>{esc(zone)}</h3>')
            for e in items:
                label,cls=direction(e['signal'])
                value=str(e['price'])+(' %' if e.get('value_unit')=='Yield %' else '')
                output.append(f'<div class="event" data-event-id="{esc(e["id"])}"><b>{esc(e["ticker"])}</b><span>{esc(e["origin_tf"])}</span><span class="{cls}">{label}</span><span>{esc(value)}</span><span class="event-time" title="{esc(local(e["confirmed_at"],zone))}">{esc(relative(e["confirmed_at"],as_of,zone))}<details class="audit"><summary>确认时间</summary>{esc(local(e["confirmed_at"],zone))}</details></span></div>')
        output.append('</section>')
    output.append('<section id="structures" aria-labelledby="structure-heading"><h2 id="structure-heading">近期信号 · 当前结构</h2><div class="filters"><label>范围<select id="scope"><option value="all">全部</option><option value="related">本轮相关</option></select></label><label>市场<select id="market"><option value="">全部市场</option>')
    zones=sorted({r.get('market_timezone') or 'unknown' for r in rows})
    output.extend(f'<option value="{esc(z)}">{esc(z)}</option>' for z in zones)
    output.append('</select></label><label>查找标的<input id="search" type="search" placeholder="Ticker" aria-label="查找标的"></label><button id="reset" type="button">重置筛选</button></div>')
    output.append(f'<p id="count" class="count" role="status" aria-live="polite">显示 {n} / {n} 个标的 · {n*4} 个周期单元格</p><p id="empty" class="empty" hidden>当前筛选无匹配；不代表没有数据。</p>')
    for zone in zones:
        output.append(f'<section class="market"><h3>{esc(zone)}</h3>')
        selected=[r for r in rows if (r.get('market_timezone') or 'unknown')==zone]
        selected.sort(key=lambda r:(-int(r['group_date'].replace('-','')),RANK[r['highest_signal_tf']],-stamp(r['anchor_confirmed_at']).timestamp(),r['ticker']))
        for date,group in groupby(selected,key=lambda r:r['group_date']):
            output.append(f'<section class="date-group"><h4>{esc(date[5:].replace("-","/"))}</h4><div class="column-head"><div>Ticker</div>'+''.join(f'<div>{tf}</div>' for tf in TFS)+'</div>')
            for r in group:
                output.append(f'<article class="ticker-block" aria-label="{esc(r["ticker"])} 四周期结构" data-ticker="{esc(r["ticker"])}" data-market="{esc(zone)}" data-related="{str(r["ticker"] in related).lower()}"><div class="ticker">{esc(r["ticker"])}</div>')
                output.extend(cell_html(r,tf,as_of) for tf in TFS)
                output.append('</article>')
            output.append('</section>')
        output.append('</section>')
    output.append('</section><footer id="report-end">结构区结束 · 全部已载入；待核单元格不代表当前状态已验证。</footer></main>')
    payload=json.dumps({'packet':packet,'plan':plan},ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\u003c').replace('>','\u003e').replace('&','\u0026')
    output.append(f'<script type="application/json" id="report-data">{payload}</script><script>{JS}</script></body></html>')
    return '\n'.join(output)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packet',type=Path,required=True);p.add_argument('--plan',type=Path)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--context-only',action='store_true')
    a=p.parse_args()
    if not a.context_only and not a.plan:p.error('--plan required for a new report')
    packet=json.loads(a.packet.read_text(encoding='utf-8'))
    plan=json.loads(a.plan.read_text(encoding='utf-8')) if a.plan else {'report_items':[]}
    a.out.write_text(render(packet,plan,context_only=a.context_only),encoding='utf-8')
if __name__=='__main__':main()
