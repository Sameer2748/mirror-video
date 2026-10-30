# Compare AI Step-2 proposals (W/ai_proposals.json) with a decisions.json made by hand.
# usage: python tools/eval_ai_match.py <work_dir with prep.json + ai_proposals.json> <hand decisions.json>
# note: the import check rewrites W/decisions.json from W/spec_ai.py - run it on a copy of the work folder.
import json,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
from dubstudio.pipeline import decisions as dec
def rng(s): return (s,s) if isinstance(s,int) else (s[0],s[1])
W=sys.argv[1]
p=json.load(open(W+'/prep.json')); H=json.load(open(sys.argv[2]))
hand={}
for g in H['groups']:
    for m in g['members']: hand[m]=rng(g['src'])
for k,v in H['assign'].items(): hand[int(k)]=rng(v['src'])
for k in H['gap']: hand[int(k)]='gap'
for k in H['confirm']: hand[int(k)]=rng(p['auto'][k]['src'])
A=json.load(open(W+'/ai_proposals.json'))['proposals']
agree=0; bad=[]
for d in A:
    r=d['row']; h=hand.get(r, rng(p['auto'][str(r)]['src']) if str(r) in p['auto'] else 'gap')
    if d['action']=='confirm': a=rng(p['auto'][str(r)]['src'])
    elif d['action']=='none': a='none'
    elif d['action']=='gap' or d.get('src') is None: a='gap'
    else: a=rng(d['src'])
    if a==h or (a not in('gap','none') and h!='gap' and a[0]<=h[1] and h[0]<=a[1]): agree+=1
    else: bad.append((r,a,h,d.get('confidence'),p['dub'][r]['text'][:45]))
print(os.path.basename(W), f"{len(A)} flagged | same as hand {agree} | different {len(bad)}", bad, json.load(open(W+'/ai_proposals.json'))['usage'])
print('import check:', dec.from_spec(W,open(W+'/spec_ai.py').read()))
