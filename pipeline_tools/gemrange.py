#!/usr/bin/env python3
"""Gemini lip label, range mode: 30-s pieces (video+sound) -> time ranges where a person on screen speaks; RUNS runs at once;
a sentence is lip-sync when the majority of runs gives it >= 0.4 s inside its Whisper time. Writes W/lips_range/range_gemini.json."""
import base64,json,os,sys,time,subprocess,concurrent.futures as cf
sys.path.insert(0,os.path.expanduser('~/mnt/adaptation-tool/mac_lipscan/ai_lip_test')); import ai_faces as A
V,W=sys.argv[1],sys.argv[2]; RUNS=int(os.environ.get('RUNS','3')); piece=30.0
L=float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",V],capture_output=True,text=True).stdout)
P=("Watch this video clip (with sound). List every time range (seconds from the start of THIS clip, with one decimal) in which a "
   "person visible on screen is speaking - their lips/mouth move as they talk. Ignore narration while nobody on screen talks. "
   "Answer only JSON: {\"speaking\": [[start, end], ...]}.")
d=W+'/gemini'; os.makedirs(d,exist_ok=True); starts=[]; s=0.0
while s<L: starts.append(s); s+=piece
t0=time.time()
def cut(s):
    f=f"{d}/v_{s:06.1f}.mp4"
    subprocess.run(["ffmpeg","-v","error","-y","-threads","2","-ss",f"{s}","-t",f"{piece}","-i",V,"-vf","scale=480:480","-c:v","libx264","-crf","30","-preset","ultrafast","-c:a","aac","-b:a","48k",f],check=True); return s,f
def ask(job):
    (s,f),run=job; b=base64.b64encode(open(f,'rb').read()).decode()
    r,dt=A.call('gemini-3.5-flash',[{"type":"text","text":P},{"type":"file","file":{"file_data":"data:video/mp4;base64,"+b,"format":"video/mp4"}}],4000)
    p=A.parse(r) or {}; rng=[]
    for x in p.get('speaking',[]):
        try: rng.append((s+float(x[0]),s+float(x[1])))
        except Exception: pass
    return run,rng,r.get('usage',{}),('error' in r),(A.parse(r) is None)
pool=cf.ThreadPoolExecutor(len(starts)*RUNS); futs=[]
with cf.ThreadPoolExecutor(8) as ex:
    for c in ex.map(cut,starts):
        for run in range(RUNS): futs.append(pool.submit(ask,(c,run)))
t_cut=time.time()-t0; res=[f.result() for f in futs]; pool.shutdown(); t_all=time.time()-t0
runs=[[] for _ in range(RUNS)]
for run,rng,u,e,bad in res: runs[run]+=rng
S=json.load(open(W+'/sentences_src.json'))['sentences']; lab=[]
for x in S:
    a,b=x['start_ms']/1000,x['end_ms']/1000
    v=[sum(max(0,min(b,q)-max(a,p)) for p,q in rr)>=0.4 for rr in runs]
    lab.append(dict(idx=x['idx'],start=a,end=b,votes=v,speaking=sum(v)*2>len(v)))
json.dump(dict(mode='range',runs=RUNS,labels=lab,ranges=runs),open(W+'/gemini_labels.json','w'),ensure_ascii=False)
n=int(max(x['end_ms'] for x in S)/1000*25)+50; sc=[-5.0]*n
for l in lab:
    if l['speaking']:
        for f in range(int(l['start']*25),min(int(l['end']*25)+1,n)): sc[f]=5.0
os.makedirs(W+'/lips_range',exist_ok=True)
json.dump(dict(idx=-1,cls='range',window=[0,n/25],start_s=0,end_s=n/25,frames=n,scores=sc,visual=[-5.0]*n,mouth=[0.0]*n,aperture=[0.0]*n,tracks=[],method=dict(det='gemini-3.5-flash range mode',runs=RUNS)),open(W+'/lips_range/range_gemini.json','w'))
print(f"{len(starts)} pieces x {RUNS} runs = {len(res)} requests | cutting done {t_cut:.1f}s, ALL DONE {t_all:.1f}s | errors {sum(r[3] for r in res)}, unreadable {sum(r[4] for r in res)} | all runs agree on {sum(len(set(l['votes']))==1 for l in lab)}/{len(lab)} | lip-sync sentences {sum(l['speaking'] for l in lab)} | tokens in {sum(r[2].get('prompt_tokens',0) for r in res)} out {sum(r[2].get('completion_tokens',0) for r in res)}")
