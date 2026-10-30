# how the lip decision changes with the thresholds (min_len = shortest lip stretch kept, min_lip = seconds needed in a sentence)
import os,sys,json,argparse; sys.path.insert(0,os.path.expanduser('~/mnt/adaptation-tool/scripts')); import dubstitch as D
W=sys.argv[1]; L=json.load(open(W+'/lipsync.json'))['sentences']
res={}
for name,ml,mp in [('now 0.4/0.4',0.4,0.4),('0.2/0.2',0.2,0.2),('stretch>=0.4, any overlap',0.4,0.001),('no threshold (any lip frame)',0.0,0.001)]:
    opt=argparse.Namespace(use="all",mouth_thr=0.007,merge=0.3,min_len=ml,min_lip=mp)
    iv,_=D.merged_lip_intervals(W,opt)
    sp=[sum(max(0,min(y,s['end_ms']/1000)-max(x,s['start_ms']/1000)) for x,y in iv)>=mp for s in L]
    res[name]=sp
base=res['now 0.4/0.4']
for k,v in res.items():
    add=[s['idx'] for s,a,b in zip(L,base,v) if b and not a]; rem=[s['idx'] for s,a,b in zip(L,base,v) if a and not b]
    print(f"{k:30s} speaking {sum(v):3d}/{len(L)} | added {len(add)} {add[:25]} | removed {len(rem)}")
json.dump({k:v for k,v in res.items()},open(W+'/thr_test.json','w'))
