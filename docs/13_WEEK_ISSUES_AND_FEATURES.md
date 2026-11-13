# 13 — Issues and features, 3–11 Nov 2026

A week log for **TAB PGC ITALY** (`TABPGCIT`) going live on Render, plus the editor and pipeline fixes that came with it. Dated detail still lives in `docs/03_PROGRESS.md`. This file is the **type index**: what broke, what we added, what is still open.

**Main stitch logic was not rewritten.** Matching, scene edit, lip windows, stretch/bleed, render concat, QC rules, and translate stay the same. Changes this week are durability around that path (disk, empty slices, lost `config.json` pointers, packing) plus editor/bulk features.

Live: https://dubstudio-yg5s.onrender.com (service `srv-dar686jncjis73c9rr90`, branch `handover-doc-review`). Heavy steps on RunPod; files in R2; this box is the website + queue.

Do not Process Again from scratch on a late episode (throws away matching / lip scan / scene edit). Retry the failed step.

---

## 1. Features added

| When | Feature | Why |
| --- | --- | --- |
| 4 Nov | **Bulk generation** | Upload and queue a range of episodes together. Names like `TAB FR eo 14 .mp4` / `S01E11` match the episode number; folders do not have to be renamed `14.mp4`. |
| 4 Nov | **Cheaper scene edit** | Claude medium thinking, 2+2 rounds, no retry of empty replies, speed clamp in code. Typical **~$2–4/ep** instead of **~$18**. |
| 9 Nov | **Render from the bulk list** | An episode waiting in Review can be rendered without opening the editor. First list render may go out with flags; only a **black gap** blocks it. |
| 9 Nov | **Inspector transitions while Automatic is on** | Any clip except the first shows Light leak / Blur dissolve / Zoom blur / Glow / Automatic / None. A pick is kept. Timeline ✦ is bright only for a pick you made. |
| 9 Nov | **Matching-clip fill after scene edit** | If Claude leaves a hole / placeholder and matching already had a clip, use that clip. Retry `use_scene` repairs an old episode with no new Claude spend. `docs/12_SCENE_EDIT_MATCHING_FALLBACK.md`. |
| 9 Nov | **8 CPU slots** (cap 16) | Several render tails can wait on idle RunPod CPUs at once (was 2). |
| 9 Nov | **Shot library on rented CPUs** | A 35-episode bulk was 502ing the website; library takes this box’s `ai.py` in the work tar. |
| 9 Nov | **Bulk row Delete** | A bulk row can be removed; screens rebuilt so live gets the button. |
| 10 Nov | **Premiere-style Motion** | Inspector zoom + pan X/Y on a selected clip. The program monitor frame stays fixed; zoom crops inside a black box. Output resolution and aspect ratio do **not** change (crop same AR, then scale back to source WxH). |
| 10 Nov | **Check-only retry** | After translate rewrites the English SRT, run check again without sending the render tail. Videos already made stay. |
| 10 Nov | **Undo Process again** | If Process again restarted from lip-scan after a late run already existed, restore the previous run and retry the failed step. |
| 10 Nov | **Keep the English SRT pointer** | If scene/cut unpacks an older `config.json` after translate, find `*english*.srt` next to the videos, write `translation.out` again, and still land `/out` when check fails so the masters are not thrown away (`232f32b`). Tail is **14 steps** (`point_translation` before `check`). |
| 12 Nov | **Upload type per slot** | Video / audio / script folders (or one mixed folder used three times) only take their own types: video `.mp4/.mov/.mkv`, audio `.mp3/.wav/.m4a` (not mp4), script `.docx/.doc/.txt`. The API rejects the wrong type too. |
| 12 Nov | **Daily disk cleanup** | Once a day the runner drops leftover pack tars, render `segs*`, and local copies of **done** episodes that are already in R2 (source video, published masters, intermediates). Processing / Review / running jobs are left alone. Thumbs / json / srt stay. If free space is under **40 GB**, it also strips done episodes from today; under **20 GB** it drops proxy / lips of done episodes too. |
| 12 Nov | **Editor preview from R2** | 540p + dub audio play from a signed cloud link once mirrored, even if this box still has the file. A render packing on the website no longer streams preview bytes through the API, so other editors can work. |
| 12 Nov | **Smoother editor preview** | A cut / slow clip used to pause the voice (“picture catching up”) because the player was seeking. Seeking at a cut is ignored; the voice only waits on a real 2 s hang. |
| 12 Nov | **Filmstrip cannot 502 the site** | Missing stills used to run ffmpeg on every tile (TAB EP22 404 storm). Thumbs only cut a local file, fail once and remember, timeout 8 s; 404s are cached. |
| 13 Nov | **Poppins Bold on the API box** | Dynamic subs (`dynsrt`) on Render failed: the ttf was in the screens folder but not on the font path. Copy it into `/usr/share/fonts` and `/app/assets`; config finds it. |
| 12 Nov | **Second-render missing video** | After Review, a second Render left `config.json` pointing at `/work/input/….mp4`. The tar skips `input/`, so the rented machine ffprobed an empty path (TAB EP21 / EP23). Fetch from R2 if the file is gone, rewrite leftover worker paths, and the chain driver copies `/files/F0/name` onto `/work/input/…` if it is still missing. |
| 12 Nov | **Download-all zip** | Zip is the three videos + English SRT only (pulled from R2 if the local copies were cleaned). Deliverables shows only the cloud list (download links + Download all), not the this-Mac Finder table. |

Automatic transitions on a finished cut still rotate styles at source jumps (into B-roll stays a straight cut). EP52 example: 104 transitions (blur dissolve 39, zoom blur 34, light leak 31), not one style on every clip.

---

## 2. Issues by type

### A. Disk / Render box (`OSError: [Errno 28] No space left on device`)

The website disk is **not** the rented machine. Whisper / render can finish on RunPod and still fail here while packing or unpacking a tar.

| Episode / when | What we saw | Cause | What we did |
| --- | --- | --- | --- |
| TAB EP53, 10 Nov | CHAIN OK, then fail unpacking `segs_groups` | Worker sent throwaway segment caches onto a **50 GB** disk | Skip `segs*` on unpack; drop them on the worker; unpack `/out` first; queue R2 immediately (`761cb07`) |
| TAB EP52 editor | Timeline clip images blank; `/thumb` 500 | Same full disk; ffmpeg could not write a jpg | Keep `thumbs/`; free segs before cutting a frame; fill filmstrip (`9c15c5e`) |
| TAB EP31 Whisper | Worker finished (1570 words, 286 SRT rows, 63 s); then download ENOSPC | Cleanup only looked for `segs` and found **none**. 50 GB was leftover **published mp4s, tars, other episodes’ lips/proxies** | `free_disk`: drop leftover tars, published videos already in R2, neighbour caches (`2311a93`) |
| TAB EP53 / EP54 | Stuck **68%**, “sending the render tail”, 55–61 min | Not waiting on RunPod. This box could not pack the work tar / copy `dynsubs.py` | Same disk. After space returned, retry **render** only |
| 10 Nov afternoon | Jobs stayed **running** with no new log after a deploy | Runner requeues `running` on start, then hung in `free_disk` while the volume was still 0 GB. After that, `running` + message `sending the render tail` has no watchdog | Restarted the service once 53 GB (later **157 GB**) was free so they packed and sent |
| 12 Nov | Site 502 ~5 min, then 200; **35 GB free of 200** | Deploy warmup of upload-type filter, not a full disk. TAB bulk leftovers (tars, segs, published mp4s, source files of 100+ done eps) still eat the volume | Daily sweeper on the runner: leftover tars/segs always; done-episode R2 copies after 1 day, or immediately when free < 40 GB |
| 10 Nov | Disk still tight for bulk | 50 GB too small for 8 active episodes + leftovers | Live disk **50 → 200 GB** at `/app/output` (~**$50/mo** at $0.25/GB). Extra space does **not** replace cleanup |

**Now (11 Nov):** disk is **200 GB** at `/app/output`. EP53 / EP54 were resent as the full tail. EP31 check-fail (below) is patched; retry was **Render clips** only, not Process Again. Tail is **14 steps** (`point_translation` before `check`).

### B. Lip scan / merge

| Episode | What we saw | Cause | Fix |
| --- | --- | --- | --- |
| Several | Merge fail 375 vs 376 frames | 29.97 → 25 fps rounding | Trim 1–2 extra frames on the API box (`bef292b`) |
| EP15 | Lipsync died on last sentence | Sentence past last scanned frames | Treat as **no lips**, not a hard stop (`77ff045`) |
| Last piece 376 vs 258 | Merge fail | Last file window shorter than 15 s | Keep the **shorter** half (`b54bd4a`) |
| EP20, EP26–30 | `lipmesh` / merge fail, empty slice | ffmpeg fast seek returned no frames; empty GPU piece | Ship this box’s `lipmesh.py`; accurate seek; rescan empty GPU piece; fill empty last piece (`0654118`, `39b823f`) |
| EP26–30 | Four minutes “no lips” | Missing pictures treated as a silent scan | Stop that; drop a truncated tail instead of failing the whole episode (`7f4ade0`, `ca784fd`) |

Merge always runs from the **API image**, not the older rented `lipmerge.py`.

### C. Shot library / scene edit / Claude

| Episode | What we saw | Cause | Fix |
| --- | --- | --- | --- |
| EP13 | Face map `json.load` fail | Control character in a Claude shot label | Strip control chars (`d11c006`) |
| EP14 | `dictionary changed size during iteration` | Six label threads writing `_claude_cache.json` | Lock the dump (`e033ea6`) |
| Library | Step fail mid-locations | Claude hit max_tokens (`"Night forest, then Albe`) | Close finished pairs; leftover places in batches of 30 (`f33a958`) |
| Library | PIL crash / fail after frames survived | Empty last-shot grab, or last shot past the pictures | Skip empty last-shot clip; survive empty grabs (`564eb5a`, `b7ee4df`) |
| Library | Scene split crash after a full label run | Claude people as dicts | Accept dicts (`080ddad`) |
| Scene edit Retry | `json.load` of half `par_cache` | Kill / disk full mid-dump | Atomic write; load closes a truncated file (`e35c99a`) |
| EP11 / EP12 | 26 s / 32 s black hole in Review | Claude `placeholder` with no footage; matching had clips | Matching-clip fill (`a404920`) |
| Cost | ~$18/ep scene edit | High thinking + empty retries | Medium thinking, 2+2, no empty retry (`a8c361a`, `ac1a220`) |
| TAB EP48 | Face map `KeyError: 83` after ~5 min (InsightFace `estimate` warnings are noise) | Claude named a group id that is not a ≥4-face identity cluster; centroids then did `X[big[g]]` | Ignore unknown group ids; this box’s `facemap.py` travels with the GPU job. Retry **facemap** only, not Process Again. |

### D. Render tail / worker packing

| Episode | What we saw | Cause | Fix |
| --- | --- | --- | --- |
| EP20 | Subtitle burn `import pngsubs` fail | Staged only `dubstitch.py` | Stage `pngsubs.py` + `dynsubs.py` too (`e0a8784`) |
| Concat | Leftover `.ok` files treated as done, no mp4 | Old worker concatenates `.ok` without the video | This box’s `dubstitch.py` travels with the job (`54316a6`) |
| Bulk render | PUT `Too much/little data` | Eleven tails wrote the same `_render_tail_work_in.tar.gz` | Scratch tar name includes episode id (`2607db2`) |
| Bookends | `render_tail` die on source ending | Truncated H264 ending | Skip truncated ending (`883df92`) |
| Translate then check | English SRT missing after videos exist | Retry sent the whole tail again | Check-only retry (`4044cb3`) |
| TAB EP31, 10 Nov | Videos OK (3× 673.960s), then `FINAL CHECK FAILED: translated SRT missing` / `BAD translated SRT: None`. QC story/black FAILs did **not** fail this check. `/out` never came home. | Translate wrote the SRT + `translation.out`. Scene/cut then unpacked an **older `config.json`** and wiped the pointer. Check only looked at the pointer. Worker skipped PUT `/out` on rc≠0; Render raised before unpack. | Find the SRT next to the videos; keep `translation` keys on unpack; land `/out` even when check fails; `point_translation` before `check` (`232f32b`). Retry **render**, not Process Again. Worker image still needs a rebuild for the handler PUT; staged `dubstitch.py` already travels with the job. |
| IMTS EP69/70 | Worker `FINAL CHECK PASSED` (all 3 videos). Then local check: `missing /out/…_final_en_subs.mp4`. Retrying check fails the same way. Videos **are** in the output folder. | `subs_outputs` is keyed by the video path. Unpacking rewrote values, not dict keys, so check still looked at the worker’s `/out/…`. | Rewrite those keys on unpack; local check also finds the file next to the clean master. Retry **check** only, not Process Again. |
| TAB EP79 | English translation `HTTP 403 Key limit exceeded $500.62 of $500`. Later episodes translate fine. | Job hit the old OpenRouter cap and stayed **failed**. Raising the cap does not retry a finished fail. Not a Claude empty-reply. | One automatic job retry on that 403 (not 3 Claude calls). Retry **translate** only. |
| TAB EP15 | Render retry: `probe_video` `expected 3, got 1`; transitions `float ''`. Videos and FINAL CHECK already existed. | A later visit left `src_video` as a rented-machine path. ffprobe of that empty file is blank. Retry also rebuilt `_before.mp4` even though it was already there. Not a new 25 fps cap (that is older). | Keep/heal the local source path; skip `before_render` when that file exists; check-only retry. |
| Status table | Episode **Failed** while a new tail was on a machine | Leftover failed `transitions` row | Show **Processing** while a retried render is running (`673e1d7`) |
| Worker vs this box | “Stuck at 68% / waiting for worker” | UI is packing/sending **on Render**; RunPod has not started yet | Read the log: `No space left` / `sending the render tail` = this box |

### E. Editor / UI

| What we saw | Cause | Fix |
| --- | --- | --- |
| Banner “IT#-1 runs outside the episode” blocks Render | Last B-roll a few ms past `total_ms` (Claude hundredths) | Stretch a short last clip, trim a long one (`fa6169a`) |
| Groups overlap fail | Lip windows share >1 ms of source | Trim shared footage in our `dubstitch.py` (API box) |
| Whisper fail `IT` / `FR` | faster-whisper wants `it` / `fr` | `presets.asr_lang` (`08c07de`) |
| Groups / merged `can't open file` on live | Commands pointed at `/root/Desktop/france-team/...` | Scripts in `app/backend/pipeline_tools/` (`b54bd4a`) |
| EP13 Deliverables “final check did not pass” blank box | Check rode the render-tail log; job log empty | Read the render log too (`7403f10`) |
| Timeline clips empty | `/thumb` 500, disk full | See §2A (`9c15c5e`) |
| Zoom “changes resolution” | Expected crop; output still source WxH | Not a bug; monitor is a fixed frame (`e48affe`) |
| Automatic = one transition on all clips | Misread of mixed styles | Plan still rotates per jump except into B-roll |

### F. Queue / bulk ops

| What we saw | Cause | Fix |
| --- | --- | --- |
| Website 502 on 35-episode bulk | Shot library on the API box | Library on rented CPUs (`9f048fa`) |
| Bulk page hammering | One query per episode | Two queries; refresh at most every 2 s |
| Process again after a late fail | New run from fetch/lip-scan | Restore previous run (`3934897`) |

---

## 3. Infra this week (not a code feature)

| Change | Detail |
| --- | --- |
| Live disk | **50 GB → 200 GB** persistent disk at `/app/output`. Render SSD **$0.25/GB/month** → **$50/mo** (was $12.50). |
| `MAX_ACTIVE_EPISODES` | 8 |
| `CLOUD_STEPS` (live) | lip scan, Whisper both sides, face map, proxy, sharpness, words, lip mesh, translate, match, cut, scene, **tail** |
| Worker images | `sameer27488/dubstudio-worker:pipeline` (CPU) and `:face-gpu`. Tag push does **not** restart warm RunPod workers. |
| Auto-deploy | API image rebuilds on push to `handover-doc-review`. |

Bigger disk **buys time**. Leftover masters, tars, and other episodes’ caches will fill 200 GB too if cleanup never runs.

---

## 4. How to read a “stuck” episode

1. Open the step log, not only the % bar. 68% with `sending the render tail` is still **this box**.
2. `OSError: [Errno 28]` = disk. Check Settings health **Free disk space**. Worker may already have finished.
3. `render tail on a rented machine (N working)` = RunPod has the job. **N** is workers on the endpoint, not clip progress. The visit often takes **30–60+ min** with no extra log lines until it returns.
4. Retry the **failed / current** step. Do not Process Again. Do not start EP11/12 (known black-gap history). Do not overwrite live TGS EP32/33 cuts.

---

## 5. Still open / next

- Cleanup after Review (park work in R2, download only on Render) was asked then **paused** — process unchanged; disk was increased instead. A **daily leftover sweeper** now covers tars / segs / R2 copies of done episodes (12 Nov).
- After a deploy, a job can sit `running` with a dead thread if the message is not `starting` (120 s watchdog only covers `starting`). Restarting the service requeues them.
- `CLEAN_WORK` still defaults **false** (proxy / lips stay after publish). The daily sweeper removes them of **done** episodes only when free space is under 20 GB.
- Scene/groups still `force_local` until the CPU image is confirmed to have the empty-reply cap and medium thinking.
- R2 CORS `ExposeHeaders: ["ETag"]` still needed for browser multipart of multi-GB videos.
- TAB EP12 matching-fill hole: Retry `use_scene` only if asked.
- Do not fill minutes of missing pictures as “no lips.”

---

## 6. Commit index (3–11 Nov)

Commits on `handover-doc-review`. Head after EP48 facemap skip: see git log. Prior: `232f32b` (English SRT pointer).

| Date | Count | Range |
| --- | --- | --- |
| 4 Nov | 12 | bulk + scene-edit cost + Whisper lang + lip merge + library JSON |
| 9 Nov | 10 | matching fill, cpu slots, bulk render, library on workers, transitions inspector |
| 10 Nov | 18 | lip-scan empty slices, Motion zoom, segs skip, thumbs, `free_disk`, EP31 SRT pointer |
