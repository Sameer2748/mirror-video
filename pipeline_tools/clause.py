#!/usr/bin/env python3
"""Split a sentence SRT at commas/semicolons/colons/dashes into clauses (each >= MIN words), times shared by characters.
`dubstitch.py words` then re-times every clause exactly from the audio. usage: clause.py IN.srt OUT.srt [MIN=3]"""
import re,sys
src,dst=sys.argv[1],sys.argv[2]; MIN=int(sys.argv[3]) if len(sys.argv)>3 else 3
txt=open(src,encoding='utf-8-sig').read().replace('\r','')
blocks=[b for b in re.split(r'\n\s*\n',txt.strip()) if b.strip()]
ms=lambda h,m,s,f:((int(h)*60+int(m))*60+int(s))*1000+int(f)
fmt=lambda t:f"{t//3600000:02d}:{t%3600000//60000:02d}:{t%60000//1000:02d},{t%1000:03d}"
out=[]; n0=0
for b in blocks:
    L=b.split('\n'); m=re.match(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)',L[1]); a=ms(*m.groups()[:4]); e=ms(*m.groups()[4:])
    text=' '.join(L[2:]).strip(); n0+=1
    toks=text.split(' '); parts=[]; cur=[]
    for k,w in enumerate(toks):
        cur.append(w); rest=len(toks)-k-1
        if re.search(r'[,;:]["»”’]?$',w) or (k+1<len(toks) and toks[k+1] in ('–','—','-')):
            if len(cur)>=MIN and rest>=MIN: parts.append(cur); cur=[]
    if cur: parts.append(cur)
    tot=sum(len(' '.join(p)) for p in parts); t=a
    for i,p in enumerate(parts):
        s=' '.join(p); d=round((e-a)*len(s)/tot) if i<len(parts)-1 else e-t
        out.append((t,t+d,s)); t+=d
with open(dst,'w',encoding='utf-8') as f:
    for i,(a,e,s) in enumerate(out,1): f.write(f"{i}\n{fmt(a)} --> {fmt(e)}\n{s}\n\n")
print(f"{src.split('/')[-1]}: {n0} sentences -> {len(out)} clauses")
