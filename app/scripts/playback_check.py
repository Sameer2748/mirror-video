"""Plays an episode start to end in the editor (headless Google Chrome, muted) and measures how far the program
monitor's picture is from where the timeline says it should be, every 200 ms (design: "Preview without rendering").

    app/backend/.venv/bin/python app/scripts/playback_check.py 1            # episode id 1, whole episode
    app/backend/.venv/bin/python app/scripts/playback_check.py 3 --from 300 --seconds 60

Prints the error spread (ms), the moments where the picture was more than 100 ms off, whether the subtitle on screen
is the right one, how long the voice was held for the picture to catch up, and saves every sample as JSON
(playback_check.json). Needs the app running and Google Chrome installed.

A busy Mac can be simulated: --cpu-throttle 6 (Chrome runs 6x slower) and/or --slow-raf 500 (screen redraws only
every 500 ms, like a window that is not in front)."""
import argparse, asyncio, json, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

SAMPLER = r"""
(async () => {
  const d = await fetch('/api/episodes/%(eid)d/timeline').then(r => r.json());
  const clips = d.timeline.clips;
  const A = document.querySelector('.monitor audio');
  const vids = [...document.querySelectorAll('.monitor video')].slice(1);     // the program monitor's two players
  const find = t => { let lo = 0, hi = clips.length - 1; while (lo < hi) { const m = (lo + hi) >> 1; if (t >= clips[m].t1) lo = m + 1; else hi = m; } return clips[lo]; };
  const cues = d.subs.dub;
  const cueAt = t => cues.find(c => c.t0 <= t && t < c.t1);
  window.__pc = { samples: [], skipped: 0, started: Date.now(), subs: [0, 0], held_ms: 0, clock: [] };
  let lastWall = performance.now();
  window.__pciv = setInterval(() => {
    const now = performance.now(), dt = now - lastWall; lastWall = now;
    if (document.querySelector('.mon-hold')) window.__pc.held_ms += dt;
    window.__pc.clock.push([Math.round(now), Math.round(A.currentTime * 1000)]);
    if (A.paused) return;
    { const t = A.currentTime * 1000, c = cueAt(t), el = document.querySelector('.mon-sub span');   // the subtitle on screen
      if (!c || (t - c.t0 > 250 && c.t1 - t > 250)) { window.__pc.subs[1]++; if ((el ? el.textContent : '') === (c ? c.text : '')) window.__pc.subs[0]++; } }
    const t = A.currentTime * 1000, c = find(t);
    if (t - c.t0 < 150 || c.t1 - t < 150) { window.__pc.skipped++; return; }     // the swap at a cut is not drift
    const V = vids.find(v => !v.classList.contains('hidden'));
    const want = c.s0 + (t - c.t0) * (c.s1 - c.s0) / (c.t1 - c.t0);
    window.__pc.samples.push([Math.round(t), c.row, Math.round(V.currentTime * 1000 - want)]);
  }, 200);
  // the frames really put on screen (requestVideoFrameCallback): the voice's time when the frame is displayed
  window.__pc.shown = [];
  vids.forEach(v => { const cb = (now, meta) => {
      if (!v.classList.contains('hidden') && !A.paused) {
        const at = A.currentTime * 1000 + (meta.expectedDisplayTime - now), c = find(at);
        if (at - c.t0 > 120 && c.t1 - at > 120)
          window.__pc.shown.push([Math.round(at), c.row, Math.round(meta.mediaTime * 1000 - (c.s0 + (at - c.t0) * (c.s1 - c.s0) / (c.t1 - c.t0)))]);
      }
      v.requestVideoFrameCallback(cb); };
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(cb); });
  return clips.length;
})()
"""


async def main(a):
    port = 9333; prof = tempfile.mkdtemp(prefix="dubstudio-chrome-")
    url = f"http://localhost:{a.port}/episodes/{a.episode}/editor"
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                               "--autoplay-policy=no-user-gesture-required", "--mute-audio", "--window-size=1500,900",
                               "--no-first-run", "--no-default-browser-check", url],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                page = next(t for t in tabs if t["type"] == "page" and "editor" in t["url"]); break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2 ** 26) as ws:
            n = [0]

            async def ev(expr, await_promise=True):
                n[0] += 1; i = n[0]
                await ws.send(json.dumps(dict(id=i, method="Runtime.evaluate", params=dict(
                    expression=expr, awaitPromise=await_promise, returnByValue=True))))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == i:
                        r = m.get("result", {})
                        if "exceptionDetails" in r:
                            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description"))
                        return r.get("result", {}).get("value")

            for _ in range(60):                                   # wait for the editor and its players
                if await ev("!!document.querySelector('.monitor audio') && document.querySelector('.monitor audio').readyState >= 2"):
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(2)
            print("clips:", await ev(SAMPLER % dict(eid=a.episode)))
            print("plays:", await ev("document.querySelector('.monitor video').currentSrc.split('/').pop()"))
            # start where asked: click the ruler is not needed; set the audio clock, then press Play like the editor
            await ev(f"(() => {{ const A = document.querySelector('.monitor audio'); A.currentTime = {a.start}; }})()")
            if a.cpu_throttle > 1:
                n[0] += 1
                await ws.send(json.dumps(dict(id=n[0], method="Emulation.setCPUThrottlingRate", params=dict(rate=a.cpu_throttle))))
            if a.slow_raf:                                        # screen redraws only every N ms (window not in front)
                await ev("(() => { const raf = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = (f) => "
                         f"setTimeout(() => raf(f), {a.slow_raf}); }})()")
            await ev("document.querySelector('button[title=\"Play / pause (Space)\"]').click()")
            t_end = time.time() + (a.seconds + 30 if a.seconds else 3600)
            last = -1; paused_for = 0
            while time.time() < t_end:
                await asyncio.sleep(5)
                st = await ev("(() => { const A = document.querySelector('.monitor audio'); return [A.currentTime, A.paused, A.ended, A.duration, "
                              "!!document.querySelector('.mon-hold')]; })()")
                if int(st[0]) // 60 != last:
                    last = int(st[0]) // 60; print(f"  at {st[0]:.0f} s of {st[3]:.0f} s", flush=True)
                paused_for = paused_for + 5 if st[1] and not st[4] else 0      # a hold (voice waits for the picture) is not a stop
                if st[2] or paused_for >= 10 or (a.seconds and st[0] >= a.start + a.seconds):
                    break
            await ev("clearInterval(window.__pciv)")
            res = await ev("window.__pc")
    finally:
        chrome.terminate(); shutil.rmtree(prof, ignore_errors=True)
    s = res["samples"]; errs = sorted(abs(x[2]) for x in s)
    if not errs:
        print("no samples (the player did not run)"); return 1
    q = lambda p: errs[min(len(errs) - 1, int(len(errs) * p))]
    print(f"samples {len(s)} (+{res['skipped']} at cuts); |error| median {q(0.5)} ms, 95% {q(0.95)} ms, max {errs[-1]} ms; "
          f"within 1 frame (33 ms) {sum(e <= 34 for e in errs) / len(errs):.0%}, within 100 ms {sum(e <= 100 for e in errs) / len(errs):.0%}")
    bad = [x for x in s if abs(x[2]) > 100]
    if bad:
        spans = []
        for t, row, e in bad:
            if spans and t - spans[-1][1] <= 400:
                spans[-1][1] = t; spans[-1][3] = max(spans[-1][3], abs(e))
            else:
                spans.append([t, t, row, abs(e)])
        print("more than 100 ms off:")
        for t0, t1, row, e in spans[:40]:
            print(f"  {t0 / 1000:7.1f}-{t1 / 1000:7.1f} s  IT#{row}  up to {e} ms")
    sh = sorted(abs(x[2]) for x in res.get("shown") or [])
    if sh:
        qs = lambda p: sh[min(len(sh) - 1, int(len(sh) * p))]
        print(f"frames shown {len(sh)}: |error| median {qs(0.5)} ms, 95% {qs(0.95)} ms; within 1 frame (34 ms) "
              f"{sum(e <= 34 for e in sh) / len(sh):.0%}, within 100 ms {sum(e <= 100 for e in sh) / len(sh):.0%}")
    sb = res["subs"]; ck = res["clock"]
    if sb[1]:
        print(f"subtitle on screen right: {sb[0]} of {sb[1]} checks ({sb[0] / sb[1]:.0%})")
    if len(ck) > 2:
        wall = (ck[-1][0] - ck[0][0]) / 1000; played = (ck[-1][1] - ck[0][1]) / 1000
        print(f"voice played {played:.1f} s in {wall:.1f} s of wall time; held for the picture {res['held_ms'] / 1000:.1f} s")
    out = Path(a.out or ".") / "playback_check.json"
    out.write_text(json.dumps(res)); print("samples saved:", out)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", type=int)
    ap.add_argument("--from", dest="start", type=float, default=0)
    ap.add_argument("--seconds", type=float, default=0, help="0 = to the end")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default="")
    ap.add_argument("--cpu-throttle", type=float, default=1, help="simulate a busy Mac: Chrome runs N times slower")
    ap.add_argument("--slow-raf", type=int, default=0, help="screen redraws only every N ms (window not in front)")
    sys.exit(asyncio.run(main(ap.parse_args())))
