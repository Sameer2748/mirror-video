# 18 — Problems now, problems we can hit, what to fix

**25 Nov 2026.** Live website: `https://mirror-video.onrender.com` (`srv-dc1vo52fngtc738q87eg`). Factory: RunPod CPU + GPU. Files: R2.

This is the **problem list** for the tool as it runs today, plus what we already decided to change. Night file-move checks: `docs/17_NIGHT_IO_QUEUE_AND_CHECKS.md`. Locked stitch (matching, groups, lip play, unused-edge, scene-edit, 0.8–1.25×, step order): `docs/15_QC_LOCKED_FLOW.md` — **do not change** unless Sameer says so.

We are **not** building “3 giant processes.” Lip scan stays **8 pieces**.

---

## How to read this

| Column | Meaning |
| --- | --- |
| **Now** | Seen on live today (or this week) |
| **Can** | Will happen again at this scale if we do not fix it |
| **Fix** | What we do |
| **Helps** | Which pain goes away |
| **Does not** | What still breaks until another row |

---

## 1. Happening now (live)

### P1 — Website holds the master files (disk death)

**Now.** Render `fetch` downloads the episode video/audio onto the API disk. After a factory step it **downloads the result tar** again. Review never used to run cleanup; **54 TGS** in Review plus leftovers hit **0 GB**. TGS EP6–34 skipped with `Errno 28` copying `manifest_groups.json`. Health later recovered (~77–88 GB) after parked-media cleanup.

**Can.** Any 10–15 episodes in flight + Review leftovers fill 200 GB again.

**Fix (coded).** Fetch already skips masters when a CPU factory is on. The worker GETs those signed links and **PUTs videos** to `outputs.loose` (proxy, dub preview, deliverables). Render only unpacks JSON/SRT; a leftover giant tar from an old worker image is streamed and the mp4s are parked in R2, never written under `library/`. Small JSON only when Check matching / Review opens. Worker image rebuild still needed for the direct PUT path; website is safe either way.

**Helps.** 0 GB, `Errno 28`, slow fetch, copy-twice per episode.

**Does not.** RunPod throttle, 8 lip jobs × 15 episodes, 502 from a long queue click.

---

### P2 — New sheet can silently keep last week’s file

**Now / can.** If R2 already has `src_video`, ingest may skip. Sheet 153 with 155’s video would keep the old object if we “skip when already uploaded.”

**Fix.** New sheet row **always** ingest (Drive/HTTP → R2 overwrite or `v2`). Do not skip. Do not overwrite while that episode is **processing**.

**Helps.** Wrong picture for the row. “I changed the sheet and it still used the old mp4.”

**Does not.** Factory speed.

---

### P3 — 15 episodes × 8 lip pieces flood 13 CPUs

**Now.** IMTS 153–179: many at “0 of 8 pieces: waiting for a free machine.” RunPod CPU: ~8 running, **13 throttled**, **100+ jobs in queue**. You asked for **26** CPUs; the **account cap** will not start them.

**Can.** Every time `MAX_ACTIVE` is high and everyone is on lip/Whisper at once.

**Fix.** (a) RunPod **unthrottle** so 26 actually run — **owner**. (b) Coded: one rented CPU send per episode; at most `cpu_slots / 8` episodes in lip at once (30/8 = 3). Translate stays on the website (needs `config.json` from R2), not a factory box. (c) Later: pack Whisper/words/sharpness into **one CPU visit**.

**Helps.** Wall clock per episode, idle “waiting for a machine.”

**Does not.** Website disk. Matching gate. Face-map GPU lock.

---

### P4 — Cap parks a mid-run episode at the **back** of the whole line

**Now.** IMTS **156, 158–170** were processing, then `queued` at 16:45–16:46. Cap keeps jobs that are `running` (even “waiting for a machine”). Overflow is parked with **`queued = now`**. Line is global `ORDER BY queued` → they sat at position **~150**, behind WBT/TGS. We put them back after 171–179 (`resume=1`).

**Can.** Any time more than 15 are `processing` and some have no `running` job (between visits, or Review freed slots and later eps grabbed them).

**Fix (coded).** Overflow parks **in front of** the existing line (`queued` before the current MIN), `resume=1`. Do not Process again.

**Helps.** First show does not wait for WBT/KOTU after a cap blip.

**Does not.** Why 15 is full (that is P3).

---

### P5 — Website 502 (health check 5 seconds)

**Now.** 17:28 IST 502 after **queue reorder** of 135 rows on the **same** process as `/`. Render: `HTTP health check failed (timed out after 5 seconds)`. **77 GB free** after restart — **not disk**. Same pattern after the first queue-order ~15:45. Restart brought the site back; running website jobs went to `waiting` (same run).

**Can.** Any long sync API (bulk queue, bulk sheet, packing many tars on the web worker).

**Fix (coded).** `POST /api/queue/order` returns at once and reorders on a **background thread**. Health is not blocked by 135 updates.

**Helps.** Page dead while someone reorders the line.

**Does not.** RunPod queue length.

---

### P6 — Restart / deploy bounces in-flight website jobs

**Now.** Restart to clear 502. Jobs `running` on Render become `waiting` (“resumed after a restart”). RunPod jobs already submitted may still finish; the site may **send the step again** if it was watching.

**Can.** Every deploy (`autoDeploy` on). We already said: do not deploy while 15 are packing.

**Fix.** Fewer big files packed **on Render** (P1) so restarts are cheaper. Optional: do not auto-deploy. Do not treat restart as Process again.

**Helps.** Less lost packing; less 502 from disk.

**Does not.** A restart still interrupts the **API** thread that was waiting on a cloud job.

---

### P11 — Cap-full Process wipes a mid-run episode

**Now.** Process (and bulk Process) while 15 are busy queued with **`resume=None`**. `start_next` then **`create_run`**. IMTS IT-DE **23 / 35** were at verify ~95% and started again at fetch/proxy (run 2) after the GPU/cloud-AI deploys. Lip + GPU + render paid twice.

**Can.** Any bulk Process, or Process on a stuck ep, while the cap is full. Same after a restart if an open run sits in the line with no resume flag.

**Fix.** If the latest run still has waiting/running jobs: queue with **`resume=1`**. A free slot **continues**. `create_run` only for a first Process (no open run) or explicit `from_scratch` after cancel. Night check **D** in `docs/17`. Tests in `test_episode_cap.py`.

**Helps.** Bulk line cannot re-rent machines from step 1 by accident.

**Does not.** P4 park-at-back. Explicit Process again **with a free slot** still starts a new run.

---

### P12 — Cleanup fails on stale failed uploads (do not Process again)

**Now.** IMTS IT-DE **EP7**: cleanup failed `3 file(s) could not be sent to the cloud: nothing was deleted`. The 12 current deliverables are **done** in R2. Cleanup counts every `uploads` row with `status=failed`, including older tries of the same files.

**Can.** Any episode that retried an upload, then succeeded: leftover failed rows block cleanup. Disk is not freed. Same pattern for other steps that treat “an R2 put failed once” as episode death.

**Fix (coded).** Cleanup uses **latest upload per kind** (`blocking_failed_uploads`). It retries those latest failures **once**, waits for the queue, then fails with the **kind names**. Older failed rows of a kind that later went `done` do not block. Retry **cleanup** (or the upload), not Process again. Night check **E**.

**Helps.** Machine disk frees; no second lip/GPU bill.

**Does not.** Why the first put failed (network, missing local file, expired URL).

---

### P13 — RunPod CPU: stuck `IN_PROGRESS` + idle workers ignore the queue

**Now.** 25 Nov evening. Render tails (IMTS DE-US **175–179**, IT-DE **17 / 23 / 35**, TGS **50 / 53**) sat **40–90 min** on “waiting for a free machine.” Site jobs were `processing` / `running`. RunPod: **8 `IN_PROGRESS`**, **~16 idle**, **~10–16 `IN_QUEUE`**. The 8 “running” jobs had **no `executionTime`** (box never started the command). They were leftover sends, not these 10 tails. The 10 live tails were `IN_QUEUE` with no `workerId`. GPU pool idle (render tail is CPU). `cpu_slots=15` and `workersMax=30` were not the gate. Cancelling the 8 zombies freed the boxes (`running` 0, `idle` 22) but the 10 jobs **stayed `IN_QUEUE`** — idle workers still did not pick them up.

**Can.** Every busy render-tail wave: an abandoned send holds a worker as `IN_PROGRESS` forever; new tails wait; idle count looks “free.”

**Fix (tonight / ops, then code).** (1) List `/requests`: cancel `IN_PROGRESS` with empty `executionTime` that are **not** the live site job ids. (2) Re-send the live tails (cancel the dead RunPod id, then **Retry that step only** — not Process again). (3) Night check **F** in `docs/17`. Later: cancel orphan RunPod jobs when the site step is cancelled; do not leave `IN_PROGRESS` with no execution; alert if idle > 0 and our jobs stay `IN_QUEUE` > N minutes.

**Helps.** Tails actually get a box. Stop paying for zombie workers.

**Does not.** Account **throttle** (P3). Signed URLs waiting >3 h (C1).

---

### P14 — dynsrt (and other “local probe”) still want the master on Render

**Now.** IMTS IT-DE **23 / 35**: picture rendered on the worker; **`subs_dub` died** (`dynsubs_plan.json` missing). `dynsrt` ran **on the website** and ffprobed `work/input/src_video/…mp4` which is **gone** (`exists=False`). Same class as C6 / TAB EP21: tar skips `input/`; cleanup drops the local master; the next local step still opens that path. **17** was a sibling hole: worker `/work/config.json` missing because small work was not fetched before pack.

**Wrong fix.** Fetch the master back to Render, then retry dynsrt here.

**Right fix.** Worker already GET/PUTs R2. `dynsrt` is **its own CPU job** when the render tail is on: signed URL for src_video (even if this box has no file), captions JSON in the work tar, plan + SRT come back. Translate stays here. Tail then burns. Do **not** `fetch_inputs` on Render for a cloud send.

**17-class (coded).** `fetch_work_files` unpacks `config.json` / `manifest_groups.json` from the newest work tar when loose objects are missing, and copies them to loose R2 keys. Render/dynsrt **refuses to send** if those files are still missing — Retry that step, not Process again. Night: docs/17 **C6** + **G**.

**Helps.** 23/35 subtitle burn without filling Render. 17-class pack after cleanup.

---

### P15 — Match visit opens `*_dynamic.srt` (IMTS IT-DE 36–43)

**Now.** IMTSITDE **36–41, 43** (and any later dynamic-strip batch): Whisper→match died on the rented CPU with `FileNotFoundError: /work/{SHOW}_EPn_IT_dynamic.srt`. The match visit packed **translate** (`SCRIPT_CHAIN` ends with translate). For HIH / `sub_style=dynamic`, that command’s `--srt` is the **post-review** cards file. Dynsrt has not run yet. Lip scan waiting on 38 is a different job; the failed row is still match.

**Wrong fix.** Drop a blank `*_dynamic.srt` so parse_srt succeeds. Process from scratch.

**Right fix (coded).** Do not put any command that names `*_dynamic.srt` on the match visit. Translate for those shows rides the **render tail after dynsrt**. Retry the failed match step only — not Process. Same for every future dynamic-strip show, not only IMTSITDE.

**Helps.** New IT-DE / HIH batches get through Whisper→match. English cards still come from the editor’s final cut.

**Does not.** An episode already **on a machine** under the old pack (e.g. 42) — wait until it fails, then Retry match.

---

### P16 — Lip scan / render GET the Word script

**Now.** The `.docx` is only `mksrt --script` (Whisper SRT cleaned against the script). Every cloud visit still attached `dub_script` / `src_script` as a signed GET, and leftover `/files/F1/….docx` was rewritten onto **any** command. Lip opening and render then died on a missing package they never open.

**Wrong fix.** Fetch the script onto those machines so python-docx can open it.

**Right fix (coded).** Send the script only when the visit’s command has `--script`. Lip, render, captions, dynsrt keep video/audio only.

**Helps.** Lip / render no longer fail because a script GET 404s.

**Does not.** Locked stitch. Process again.

---

### P7 — Face map: one GPU slot, one show at a time

**Now.** `gpu_slots` default **1**. Software lock: two face maps of the **same show** must not run together. **6** GPU workers often **idle** while CPU is slammed.

**Can.** IMTS 156 on facemap while 171–179 still on lip — GPU farm unused.

**Fix.** Only if Sameer asks: raise `gpu_slots` (Settings cap 16) and/or drop show lock after a worker wipe. **Not** the night file-line. GPU pooling was built once and **reverted** (do not ship without an explicit ask).

**Helps.** Face map throughput when several shows hit that step.

**Does not.** Lip/Whisper (those are CPU). Mixing two IMTS face maps if lock stays.

---

### P8 — Settings will not let `cpu_slots` go above 16

**Now.** Settings allow **1–64** (`DUBSTUDIO_CPU_SLOTS=30` live). The remaining bug: a website job **waiting on RunPod** still filled a cpu_slot, and `MAX_ACTIVE=10` left boxes idle while 115–120 sat in line.

**Fix (coded).** Waiting-on-machine does **not** consume cpu_slots. New sends fill up to **30** rented CPUs (lip scan counts as 8). At most **4 packs** on Render at once (health). `DUBSTUDIO_FILL_CPUS=true` raises the episode cap to `cpu_slots` so queued work starts. Lip stays 8 pieces.

**Helps.** All 30 boxes can run when the line has work.

**Does not.** Throttle / Active workers (owner). Disk (P1).

---

### P9 — One episode, several Render “running” jobs at once

**Now.** IMTS 153–157 showed **4 running jobs** each during Whisper, all “waiting for a free machine.” Each holds a **cpu_slot**. WBT 101–104 sat at 5/39 for 13+ min with RunPod idle: every send unpacked **every** old work tar looking for `manifest_groups.json` (not needed until render), packed ~86 files on Render, then a restart cancelled the RunPod job (`status` 404). After that, proxy failed by `stat()` of dub audio on Render (masters stay in R2) and 105’s pack tar was deleted by another episode’s `free_disk`. Live cap was **4**, so 26 of 30 CPUs stayed unused.

**Coded.** Unpack tars only when `config.json` is missing. Do not fetch-from-every-tar on proxy/Whisper/words. Job message updates while waiting on the machine. Visit packing (stitch order unchanged): Whisper→matching one CPU; sheet→library one CPU; scene→fast trim one CPU after the face map; render→check (including dynsrt, trans_plan, sheet, qc) one CPU. Videos stay on the worker/R2.

**Can.** Slot starvation if an old deploy still sends Whisper/words/match as separate jobs.

**Fix.** Live this packing. Lip scan stays 8 pieces, parallel from ingest.

**Helps.** Slot waste, R2 pack/unpack count, P3 queue shape.

**Does not.** Human Check matching / Review gates.

---

### P10 — Translate / tail can fail while the rest is fine

**Now.** IMTS **165** had `translate` **failed**, rest queued `resume=1`. Scene-edit **HTTP 500** used to fail with no retry — live now waits **60s and retries once** (`b903bee`). Render-tail `.part` stolen by `free_disk` — live skip `.part` + unique suffix (`2df8d1e`).

**Can.** Worker internet 5xx, missing English SRT if unpack never landed.

**Fix.** Keep one retry on 5xx/timeout/reset. File-line: next visit loads **work from R2**, not a half-unpack on Render. Retry **translate** on 165 when Sameer asks (do not Process again).

**Helps.** One blip ≠ whole episode red. Tail not losing `.part`.

**Does not.** A real logic error in translate.

---

### P11 — Review / Render is a person gate; bulk Render on a full disk skips

**Now.** IMTS **153, 154, 155, 157** at Review (25/39). We **did not** press Render. TGS Review + disk full → skip.

**Can.** 50+ Review eps holding leftover files until P1 cleanup.

**Fix.** P1 cleanup of parked/review **inputs already in R2** (started live). Review auto-renders a first draft only when there is **no black gap**; a gap still waits for the editor.

**Helps.** Disk. Not “auto first draft.”

---

## 2. Can happen (not firing this hour, still real)

| ID | Risk | What happens | Fix |
| --- | --- | --- | --- |
| C1 | Signed URL signed at enqueue, job waits >3 h | Factory 403 mid-step | Presign **when the job is sent** |
| C2 | Factory hits Drive | 8 lip pieces × Google quota, wrong file | Copiers only (P1/P2) |
| C3 | Cleanup deletes R2 inputs | Retry/editor dead | Cleanup = **local** disks only |
| C4 | Overwrite R2 while ep processing | Worker reads a half file | Wait or `input/v2` |
| C5 | Check matching with **zero** files on website | Screen blank | Small JSON fetch on open (P1 exception) |
| C6 | `ensure_episode_inputs` on Render before pack | Masters back on the API disk | Stop fetch of src/audio on Render; worker already has R2 links |
| C7 | OpenRouter cap | Matching/scene/library stall | One retry on 403; raise cap (owner) |
| C8 | GPU pooling live without ask | Shows mix face libraries | Keep show lock until Sameer asks |
| C9 | Deploy while 15 packing | 502 + bounce | No live deploy until asked; P1 reduces pack size |

---

## 3. Fix map — what we will do, what it fixes

| When | Work | Fixes | Not in this work |
| --- | --- | --- | --- |
| **Night test first** (`docs/17`) | File line (2–4 copiers), always ingest new sheet, queue = R2 keys, factory R2 only, small JSON for editor, **D / F / G** (no wipe, idle CPUs take tails, dynsrt on worker) | **P1, P2, P11, P13, P14, C2, C3, C4, C5, C6**; disk 502 | P3 throttle, P4 park-at-back, P5 queue-order 502, P7 GPU |
| **After A–C green** | Make that live | Same | — |
| **Coded** | Park overflow in front of the line | **P4** | — |
| **Coded** | Queue order off the health thread | **P5** | — |
| **Coded** | Fill `cpu_slots` (30); waiters do not hold slots | **P8** | — |
| **Owner / RunPod** | Keep Active workers ~8 while a batch runs; 0 when idle | **P3** | Throttle |
| **Later** | One CPU visit for Whisper→match (keep lip pieces) | **P9, P3** shape | 3 mega-processes |
| **Ask Sameer** | Raise `gpu_slots` / drop show lock | **P7** | Lip speed |
| **Already live** | Scene 500 retry once; tail `.part` + English SRT; parked cleanup when disk tight | **P10** partial, **P1** partial | File line still needed |

---

## 4. What we will **not** call a “tool problem”

- Locked matching / groups / scene-edit looking “wrong” on QC — that is stitch rules (`docs/15`), not scale.
- Check matching “waiting for the matching files” is **not** a person. Fetch `prep.json` from R2 and auto-accept leftover rows. Review waits for a person only when there is a **black gap**.
- IMTS 69/70, leftover 197–258, IMTSITDE — do not Process unless Sameer asks.
- yg5s old host — **suspended**; do not send people there.

---

## 5. Tonight vs later (one page)

**Tonight (checks in doc 17):** prove masters never need to live on Render; new sheet always wins; matching screen still opens; Render GB does not fall by video size.

**After that, in this order:** (1) live file line, (2) park-in-place so P4 cannot hide IMTS behind WBT, (3) queue-order off the web thread so P5 502 stops, (4) unthrottle 26 CPUs, (5) optional visit packing for Whisper/match.

Until (2) and (3), reordering the line by API can **502** the site and **mis-order** parked eps even if disk is empty.
