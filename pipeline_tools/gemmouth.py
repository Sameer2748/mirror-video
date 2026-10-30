#!/usr/bin/env python3
"""Gemini 'mouth movement' mode: same question as our local scan (is any visible mouth moving?), not 'who speaks'.
Pieces get a burned-in timer (seconds from piece start); optional slow-down so Gemini sees more frames per second.
usage: gemmouth.py VIDEO WORK OUTNAME  [env: RUNS=1 PIECE=30 SLOW=1 LIMIT=0 WIDTH=640 AUDIO=0]"""
import base64,json,os,sys,time,subprocess,concurrent.futures as cf
sys.path.insert(0,os.path.expanduser('~/mnt/adaptation-tool/mac_lipscan/ai_lip_test')); import ai_faces as A
V,W,NAME=sys.argv[1:4]; RUNS=int(os.environ.get('RUNS','1')); PIECE=float(os.environ.get('PIECE','30')); SLOW=float(os.environ.get('SLOW','1'))
LIMIT=float(os.environ.get('LIMIT','0')); WIDTH=int(os.environ.get('WIDTH','640')); AUDIO=os.environ.get('AUDIO','0')=='1'; FPS=float(os.environ.get('FPS','0'))
L=float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",V],capture_output=True,text=True).stdout)
if LIMIT: L=min(L,LIMIT)
P0=("This clip has a timer in the top-left corner (seconds). Watch the MOUTHS of all people visible on screen. "
   "List every time range in which any visible person's lips/mouth are MOVING - opening and closing as when talking, mouthing words, laughing "
   "or murmuring - no matter whether the voice you hear belongs to them or not (a narrator may be speaking over the picture; ignore the sound). "
   "Do NOT count a mouth that stays still (closed, or held open, or a fixed smile), head or eye movement only, or a face too small/turned away to see the lips. "
   "Use the timer for the times, one decimal, and be precise about when the movement starts and stops. Merge movements less than 0.3 s apart. "
   "Answer only JSON: {\"moving\": [[start, end], ...]}  (empty list if no mouth moves).")
P1=("This clip has a timer in the top-left corner (seconds). Look closely at the LIPS of every person visible on screen, frame by frame. "
   "List every time range in which any visible person's lips move - including SMALL movements: lips parting slightly, lips opening and closing, "
   "talking, mouthing, whispering, murmuring, laughing, a lip twitch while reacting. The sound does not matter (it is often a narrator); judge only the picture. "
   "Do NOT count a mouth that stays completely still (closed, held open or a fixed smile), or a face whose lips you cannot see (turned away, covered, too small). "
   "Use the timer for the times, one decimal, precise start and end of each movement. Merge movements less than 0.3 s apart. "
   "Answer only JSON: {\"moving\": [[start, end], ...]}  (empty list if no lips move).")
P=P1 if os.environ.get('PROMPT','0')=='1' else P0
d=f"{W}/gm_{NAME}"; os.makedirs(d,exist_ok=True); starts=[]; s=0.0
while s<L-0.05: starts.append(s); s+=PIECE
t0=time.time()
def cut(s):
    f=f"{d}/v_{s:06.1f}.mp4"; dur=min(PIECE,L-s)
    vf=f"scale={WIDTH}:-2,drawtext=text='%{{pts\\:flt}}':x=8:y=8:fontsize=28:fontcolor=yellow:box=1:boxcolor=black@0.7"
    if SLOW!=1: vf+=f",setpts={SLOW}*PTS"
    cmd=["ffmpeg","-v","error","-y","-threads","2","-ss",f"{s}","-t",f"{dur}","-i",V,"-vf",vf,"-c:v","libx264","-crf","28","-preset","ultrafast"]
    cmd+=(["-af",f"atempo={1/SLOW}","-c:a","aac","-b:a","48k"] if AUDIO and SLOW in (1,2,0.5) else (["-c:a","aac","-b:a","48k"] if AUDIO else ["-an"]))
    subprocess.run(cmd+[f],check=True); return s,f
def ask(job):
    (s,f),run=job; b=base64.b64encode(open(f,'rb').read()).decode()
    r,dt=A.call('gemini-3.5-flash',[{"type":"text","text":P},{"type":"file","file":dict({"file_data":"data:video/mp4;base64,"+b,"format":"video/mp4"},**({"video_metadata":{"fps":FPS}} if FPS else {}))}],6000)
    p=A.parse(r) or {}; rng=[]
    for x in p.get('moving',[]):
        try: rng.append((s+float(x[0]),s+float(x[1])))
        except Exception: pass
    return run,s,rng,r.get('usage',{}),('error' in r) or A.parse(r) is None
pool=cf.ThreadPoolExecutor(25); futs=[]
with cf.ThreadPoolExecutor(8) as ex:
    for c in ex.map(cut,starts):
        for run in range(RUNS): futs.append(pool.submit(ask,(c,run)))
res=[f.result() for f in futs]; pool.shutdown(); dt=time.time()-t0
runs=[[] for _ in range(RUNS)]
for run,s,rng,u,e in res: runs[run]+=rng
json.dump(dict(name=NAME,runs=runs,limit=L,piece=PIECE,slow=SLOW,errors=sum(r[4] for r in res),tok_in=sum(r[3].get('prompt_tokens',0) for r in res),
               tok_out=sum(r[3].get('completion_tokens',0) for r in res),seconds=dt),open(f"{W}/gm_{NAME}.json","w"))
print(f"{NAME}: {len(starts)} pieces x {RUNS} runs, {dt:.0f}s, errors {sum(r[4] for r in res)}, tokens in {sum(r[3].get('prompt_tokens',0) for r in res)}")
