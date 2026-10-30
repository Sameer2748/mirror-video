import json,sys
W=sys.argv[1]; L=int(sys.argv[2]) if len(sys.argv)>2 else 100
p=json.load(open(W+'/prep.json')); au=p['auto']; dub=p['dub']; src=p['src']
def srcs(i):
    x=au.get(str(i))
    if not x or x.get('src') is None: return []
    s=x['src']; return s if isinstance(s,list) else [s]
out=[]; last=-1; si=0
for i,d in enumerate(dub):
    ss=srcs(i)
    if ss and (au.get(str(i)) or {}).get('kind','').startswith('DP'):
        while si<=max(ss) and si<len(src):
            s=src[si]; print(f"    S{si:3d} {s['start_ms']/1000:6.1f}-{s['end_ms']/1000:6.1f} {s['text'][:L]}"); si+=1
    x=au.get(str(i)) or {}
    tag=(x.get('kind','UNMATCHED') or '')[:6]
    print(f"D{i:3d} {d['start_ms']/1000:6.1f}-{d['end_ms']/1000:6.1f} {d['text'][:L]}  -> {ss} {tag} {x.get('score',0):.2f}" if ss else f"D{i:3d} {d['start_ms']/1000:6.1f}-{d['end_ms']/1000:6.1f} {d['text'][:L]}  -> ??")
while si<len(src):
    s=src[si]; print(f"    S{si:3d} {s['start_ms']/1000:6.1f} {s['text'][:L]}"); si+=1
