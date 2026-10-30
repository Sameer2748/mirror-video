# builds W/decisions.json from a compact spec; every index is checked against a keyword from its text
import json,sys
W=sys.argv[1]; spec=sys.argv[2]
p=json.load(open(W+'/prep.json')); dub=p['dub']; src=p['src']
D={"groups":[],"assign":{},"confirm":{},"repeat":{},"gap":{},"unused_notes":{}}
def chk(lst,i,kw,lab):
    t=lst[i]['text']
    assert kw.lower() in t.lower(), f"{lab}{i} '{t}' lacks '{kw}'"
    return t[:30].strip()
def s0(s): return s[0] if isinstance(s,list) else s
def G(members,s,skw,why):
    D["groups"].append({"members":[m for m,_ in members],"dub_prefixes":[chk(dub,m,k,'DUB#') for m,k in members],
      "src":s,"src_prefix":chk(src,s0(s),skw,'SRC#'),"reason":why})
def A(i,kw,s,skw,why): D["assign"][str(i)]={"dub_prefix":chk(dub,i,kw,'DUB#'),"src":s,"src_prefix":chk(src,s0(s),skw,'SRC#'),"reason":why}
def C(i,why): D["confirm"][str(i)]=why
def GAP(i,kw,why): chk(dub,i,kw,'DUB#'); D["gap"][str(i)]=why
def U(s,kw,why): chk(src,s,kw,'SRC#'); D["unused_notes"][str(s)]=why
def R(later,earlier): D["repeat"][str(later)]=earlier
exec(open(spec).read())
json.dump(D,open(W+'/decisions.json','w'),indent=1,ensure_ascii=False)
print("groups",len(D["groups"]),"assign",len(D["assign"]),"confirm",len(D["confirm"]),"gap",len(D["gap"]),"unused",len(D["unused_notes"]))
