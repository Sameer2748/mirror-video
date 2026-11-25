# 17 — How files should move (night test)

Sameer: at night, say which checks below to run (A / B / C, or a named row). This doc is the plan. **Not live until those checks pass.**

Locked stitch logic does **not** change: matching, groups, how a lip group is played, unused-edge fill, scene-edit scope, 0.8–1.25× stretch, step order. Canonical: `docs/15_QC_LOCKED_FLOW.md`.

We are **not** building the “3 giant processes” idea. Lip scan stays **8 pieces** on factory CPUs.

---

## What is wrong today

The website (Render) is a small office. It still **downloads master videos**, holds them on disk, then factory workers **download the same files from R2**. After a step, Render **pulls the result tar back**. That extra copy filled the disk (0 GB) and is not needed for the factory.

---

## How it should work

Three lines. Bytes of big files never sit on the website.

### 1. Website (Render) — office only

- Sign-in, show list, episode queue, Check matching, Review.
- Queue payload is **locations**, not files: episode id, R2 keys, duration from ingest.
- May hold **small** work files when a person opens the editor (`prep.json`, decisions, timeline, SRTs under 64 MB). That already exists as `fetch_work_files`.
- May 307 the player to R2 for video. May pull the **540px proxy** once for waveform/thumbs — never the master.

### 2. File line — new sheet always

**2–4 copier machines.** Not 1. Not the 26 factory CPUs. Only these machines talk to Drive/HTTP.

When a **new or changed sheet row** arrives:

1. If that episode is **processing**, do not overwrite its inputs. Wait until queued / failed / cancelled / review, **or** write a new R2 version (`…/epN/input/v2/…`).
2. Download **this row’s** video, audio, script (do **not** skip because last week’s file is already in R2 — it can be the wrong file).
3. Probe here: readable, size, **duration**. Save duration on the episode (lip scan needs it).
4. Upload to that episode’s R2 inputs. Confirm with HEAD (size matches).
5. Network / 5xx: **wait, retry once**, then fail **ingest** only.
6. Delete the copier’s local copies.
7. Put the episode on the **process** line with R2 keys + duration.

### 3. Process line — factory (26 CPU + GPU)

Job is sent with **R2 keys**. Website **presigns at send time** (do not sign hours before or the link expires).

Worker:

1. GET inputs and **previous work JSON** from R2 (not from Render disk).
2. Run today’s steps in today’s order (lip = 8 pieces, then Whisper, match, cut, face map on GPU, scene, render tail, …).
3. PUT work + outputs to R2.
4. Wipe local disk.

Eight lip pieces read the **same** R2 video. They must **not** hit Drive.

---

## Step by step — who holds what

| Stage | Big video/audio | Small JSON | Duration |
| --- | --- | --- | --- |
| New sheet | Copier → R2 | — | Copier writes DB |
| Lip / Whisper / cut / face / scene / render | Worker GET/PUT R2 | Previous `work_out` from R2 | DB |
| Check matching / Review | Not on website | Small copy on website or from R2 | — |
| After visit | Not unpacked onto Render | Optional small fetch for editor | — |
| Cleanup | Worker local + Render local only. **R2 inputs stay** until Process again | Keep what the editor needs | — |

---

## What we must handle (or it breaks)

| Skip this | What breaks | Handle |
| --- | --- | --- |
| Probe on copier | Lip scan (no length) | Ingest on file line, store `probe.duration` |
| Next job without last work from R2 | Cut / scene / render empty | Worker loads last `work_out` |
| Unpack **masters** on Render after every visit | Disk fills again | Unpack JSON/SRT only, or nothing until editor opens |
| Overwrite R2 while ep is processing | Half-written file on the worker | Versioned keys or wait |
| Factory hitting Drive | 8× download, quota, wrong file | Copier only |
| Sign URL at enqueue, job waits hours | 403 mid-step | Presign when the job is **sent** |
| Cleanup deleting R2 inputs | Retry / editor dead | Cleanup = local disks only |
| No small JSON for the editor | Check matching / Review blank | `fetch_work_files` when the screen opens |

---

## What we will check at night

Say **A**, **B**, **C**, **D**, **E**, **F**, or a show+episode. Do **not** run this on the live IMTS 153–179 batch unless Sameer names those eps.

### A — One episode, new sheet ingest, then process

1. Sheet row with video / audio / script links.
2. File line runs (even if R2 already had a file).
3. Render free disk does **not** drop by the size of the master video.
4. Process starts; RunPod GET from R2; PUT work; worker disk empty after.
5. Open **Check matching**: JSON is there, not blank.
6. Times: ingest wall, lip wall (~7 min with 8 free CPUs is the old baseline), Whisper wall.

### B — Same episode, sheet points at a **different** video

1. File line must **replace** R2.
2. Process must use the **new** file, not last week’s.
3. If the episode is still processing, ingest must wait or write `v2` — not clobber the running job.

### C — Three episodes on the process line after ingest

1. Copiers 2–4; factory does the work.
2. Render GB stays high (no three masters on the API box).
3. RunPod queue is work jobs, not Drive downloads.
4. No lip piece error from Google Drive.

### D — Cap full must not Process again (wipe)

Bulk Process while 15 are already working used to queue with **no resume**. The next free slot then **`create_run`** (new job list, drop work). That re-ran IMTS IT-DE **23 / 35** from fetch after they were at **verify ~95%** — lip + GPU + render paid twice.

**Must hold on the night line (and any bulk):**

1. Process (or bulk Process) while the cap is full, on an episode that still has waiting/running jobs → it **waits with `resume=1`**. Same run when a slot opens. **No new run.**
2. First Process of a **new** episode (no open run) may still start a run when the slot opens.
3. `from_scratch` on the queue-order API (WBT-style, after cancel) is the only bulk path that wipes. Do not use it on IMTS IT-DE / DE-US mid-run.
4. After a deploy/restart: jobs go `waiting` (“resumed after a restart”). Cap overflow **parks** (`resume=1`). It must **not** look like Process again.

**Pass:** an episode at verify (or render) that is Process-clicked or overflowed while 15 are busy is still on **verify/render**, `run` unchanged, when a slot opens.

**Fail — stop:** `run` bumped, step back at fetch/proxy/lip, work tar gone. That is money.

### E — Cloud put/get fail must not look like “start the episode again”

**Seen live:** IMTS IT-DE **EP7**, cleanup *Free the machine for the next episode*: `RuntimeError: 3 file(s) could not be sent to the cloud: nothing was deleted`. Publish had already put 12 files in R2. The 12 latest uploads are **done**. Cleanup counts **any** old `uploads.status=failed` row, so 3 leftover failed sends (earlier try) block disk cleanup. Local files stay. Do **not** Process again.

**Rule for any step (night line and later):** if the step failed because R2/upload/download/5xx/file-not-sent:

1. Retry **that transfer** (or ignore superseded failed rows when a later send of the same kind is `done`).
2. Retry **that step only**.
3. Do **not** `create_run` / Process again. That re-rents GPU/CPU from fetch.

Same class as: cleanup blocked by stale failed uploads; cloud step 500; signed URL expired; worker disk wipe then next job.

**Not coded yet.** Night: if this happens, retry the upload then retry the step; record it. Fix in a later pass: cleanup (and similar gates) should use **latest upload per kind**, retry failed latest once, then fail with the kind names.

### F — RunPod idle workers must take our queued tails

**Seen live:** 8 CPU workers `IN_PROGRESS` with **no `executionTime`** (stuck leftovers). ~16 workers **idle**. Our 10 render tails `IN_QUEUE` for **40–90 min**. Cancelling the 8 zombies did not make idle boxes pick up the live jobs.

**Must hold on the night line:**

1. `/health` + `/requests`: if `idle > 0` and our live job ids stay `IN_QUEUE` > **5 min**, that is a fail — not “waiting for throttle.”
2. `IN_PROGRESS` with empty `executionTime` after a few minutes = zombie. Cancel those **leftover** ids. Do not cancel the live site’s current job id.
3. Then **Retry the step** (render/verify tail) so a **new** RunPod id is sent. Do **not** Process again.
4. Pass only when live tails go `IN_PROGRESS` with a `workerId` and `executionTime` climbing (or `COMPLETED`).

**Fail — stop:** idle workers + our tails still `IN_QUEUE`; or Process again from fetch to “unstick” a tail.

### G — Worker pulls R2; dynsrt does not need the master on Render

**Seen live:** IT-DE 23/35 dynsrt ffprobe on Render (`exists=False`). Fetching the master back to the API box is the old disk death.

**Must hold:**

1. A cloud send does **not** `fetch_inputs` of src/audio onto Render.
2. Job `files` include src_video from the asset **`r2_key`** when the local file is gone. Worker GET.
3. When `CLOUD_STEPS` has `tail`/`all`, **dynsrt** is `enabled_for` (own CPU job), not a local probe.
4. Retry **dynsrt** then the tail from **subs** — not Process again.

**Pass:** Render GB does not jump by the master size when dynsrt/render run. `dynsubs_plan.json` comes back from the worker. subs_dub can burn.

**Fail — stop:** Render download of the episode mp4 to “fix” dynsrt.

### Pass

- Render GB does not fall by video size per episode.
- Factory jobs finish.
- Check matching opens.
- New sheet file is the one that ran (B).
- Ingest network blip: one retry, then ok **or** ingest failed — never a silent old file.
- Cap full + Process (D): same `run`, same step (not fetch).
- RunPod (F): live tails get a worker while idle > 0; zombies cancelled, step retried, not Process again.
- Worker R2 (G): dynsrt does not pull the master onto Render.

### Fail — stop

- Lip/render “file not found” or empty ffprobe.
- Check matching empty.
- Render disk falling by GBs per episode.
- Drive errors on lip pieces.
- A mid-run episode gets a new `run` because the cap was full (D).
- Cleanup/publish fail → Process again (E). Retry the put, then that step.
- Idle CPUs + tails stuck `IN_QUEUE` (F). Process again to unstick.
- Fetch master to Render so dynsrt can ffprobe (G).

---

## Not tonight

- Three mega-processes (all-before-face-map as one job).
- `MAX_ACTIVE=26`.
- Factory CPUs used as copiers.
- Deploy to live until A–D pass.
- Changing matching / groups / scene-edit rules.

---

## After a green night test

Then we can talk about making it live. Until then this stays a plan: file line first, queue = locations, factory = R2 only, small JSON for the editor, locked pipeline unchanged.
