# 09 — Web platform: uploads, storage, queue, workers

Owner's ask (2026-10-30, with `Dub pipeline platform: deployment requirements` and the three sketches): stop asking
the editor for folders on a Mac. A show is made in the browser (details + the localisation sheet), an episode is made
by **uploading** the source video, the dub audio and the script; the files live in **R2**; pressing *Start processing*
puts the episode in a **queue**; the workers take the files they need out of R2, run our existing pipeline steps, put
the results back and clean up, then take the next episode. Everything must be deployable later (Render for web +
API, R2 for storage, RunPod for GPU) and scale to the numbers in the requirements doc.

This file is the plan and the state of that work. `docs/08` stays the reference for accounts and the R2 client.

---

## 1. Services (the shape we are building towards)

| Service | What it does | Deploy later as |
| --- | --- | --- |
| **web** | the React screens (shows, uploads, editor, queue) | Render static site / CDN |
| **api** | login, shows, episodes, uploads, status, editor saves | Render web service (FastAPI, 2 replicas) |
| **orchestrator + workers** | takes jobs off the queues in order, runs the pipeline steps, writes status back | Render worker (CPU/AI) + RunPod (GPU) |
| **database** | accounts, shows, episodes, steps with status / time / cost, editor saves | SQLite today → Postgres |
| **object storage** | every input, work file and output | Cloudflare R2 (done) |
| **queue** | one queue per machine kind: `cpu`, `gpu`, `ai` | database table today → Redis when more than one worker |

**Postgres and Redis are wired in and tested** (2026-10-31). `app/docker-compose.yml` brings both up locally:

```bash
cd app && docker-compose up -d          # postgres on 5433, redis on 6380
DATABASE_URL=postgresql://dubstudio:dubstudio@127.0.0.1:5433/dubstudio
REDIS_URL=redis://127.0.0.1:6380/0
```

- **Database**: with `DATABASE_URL` set the whole app runs on Postgres; without it, SQLite in the library folder,
  exactly as before. The rest of the code writes the same SQL for both (`db.py` translates: `?` → `%s`,
  `INTEGER PRIMARY KEY` → `BIGSERIAL`, `REAL` → `DOUBLE PRECISION`, `INSERT … RETURNING id`). The whole test suite
  runs on both.
- **Redis**: with `REDIS_URL` set every live event goes through a Redis channel, so a browser connected to any API
  replica sees a step running on any worker. Without it the events stay in the one process — which is correct for
  one machine. The queue itself still claims jobs in the database; it moves to Redis the day we run more than one
  worker process, and that is then a small change.

## 2. Where files live (R2 keys)

```
<R2_PREFIX>/shows/<CODE>/localisation/<file>              the localisation sheet (one per show)
<R2_PREFIX>/shows/<CODE>/ep<N>/input/<kind>/<file>        source video, dub audio, scripts, SRTs
<R2_PREFIX>/shows/<CODE>/ep<N>/work/<relative path>       the work folder worth keeping (Claude caches, edit lists)
<R2_PREFIX>/shows/<CODE>/ep<N>/out/<file>                 the delivered videos, SRT and sheets
<R2_PREFIX>/db/dubstudio.db                               the database copy (see docs/08)
```

`<kind>` is the same name the app already uses: `src_video`, `dub_audio`, `dub_script`, `src_script`, `dub_srt`,
`src_srt`, `names_csv`.

Each asset row keeps **both** the R2 key and the local path it is cached at while a step runs
(`<work>/input/<kind>/<file>`), so the pipeline scripts keep taking plain file paths and **nothing in the pipeline
changes** (CLAUDE.md rule: the pipeline is the source of truth).

## 3. The flow

```
New show      details + localisation sheet ──upload──> R2 ──> shows row (no Mac folder needed)
New episode   source video + dub audio + script (+ SRTs) ──upload──> R2 ──> assets rows (r2_key, size, probe)
Start         episode goes in the queue → "Queued · position 2"
Worker        fetch  : the episode's inputs come down from R2 into the work folder
              …      : every existing step runs unchanged (Whisper, lip scan, matching, scene edit, render…)
              publish: outputs + the small work files go back up to R2
              cleanup: the big local files are deleted; the next episode in the queue starts
```

Uploads happen in one of two ways (`UPLOAD_MODE` in `app/.env`; **direct** since 2026-10-31):

- **direct** (what runs now): the API signs a link per part and the browser sends the bytes **straight to R2**, so
  the API never carries a 3 GB video. It needs the bucket's CORS rule, which the owner added on 2026-10-31
  (`AllowedOrigins` must list the web app's address; `ExposeHeaders: ETag` is what lets the browser read each part's
  ETag). Checked from a real headless Chrome: a 120 MB file went up in two parts in 10.5 s, and `fetch` brought it
  back down in 4.1 s.
- **proxy** (`UPLOAD_MODE=proxy`): the browser sends the file to the API, which streams it on to R2. Works without
  any CORS rule — the fallback if a new origin is not yet allowed.
- Small files (under 96 MB) go in one request through the API in either mode; bigger ones always go in 64 MB parts,
  each part retried on its own.
- When a new origin is added (the Render URL), put it in the bucket's CORS `AllowedOrigins`:
  ```json
  [{"AllowedOrigins": ["http://localhost:8766", "https://<the web app>"],
    "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]
  ```

## 4. Queue and status

- One row per step (`jobs`) as today, now with a clear state the screens show:
  **queued** (waiting for its turn) → **picked** (a worker claimed it) → **running** (with progress) → **done**,
  plus *needs you* (matching / review), *failed*, *cancelled*.
- Episodes queue too: `MAX_ACTIVE_EPISODES` (default **1**, "one by one") — the rest show their place in the line and
  start by themselves when the one before finishes.
- Pools: `cpu`, `gpu`, `ai` each have their own number of slots, so later a GPU job of one episode and an AI job of
  another run at the same time (the requirements doc's three queues).
- Resume, never restart: a failed step is retried alone, Claude's cached answers stay in R2, a rerun costs $0.

## 5. What is built now, and what is next

- [x] Accounts, R2 client, deliverables + database to R2 (`docs/08`).
- [x] **Uploads**: a show is made with its details + the localisation sheet, an episode with its source video, dub
      audio, scripts and SRTs — all straight into R2 (`POST /api/shows/{id}/sheet`,
      `POST /api/episodes/{id}/files/{kind}`, with `upload-url` + `confirm` for the direct-to-R2 version).
      A show with no Mac folder gets its own staging folder in the library and `profile.uploads = true`; the screens
      then ask for uploads instead of folders.
- [x] **`fetch` / `publish` / `cleanup`** steps around the existing pipeline: the files come down to the work folder
      before `ingest`, the deliverables and the small work files go back up after `notes`, and the fetched files are
      deleted so the machine is free for the next episode. The pipeline scripts themselves are untouched.
- [x] **Queue**: `POST /api/episodes/{id}/process` either starts the episode or puts it in the line
      (`MAX_ACTIVE_EPISODES`, default 1). `GET /api/queue` returns what is working and who is waiting, with the step
      and its progress; the Render-queue page and the episode page show it live. An episode parked at Check matching
      or Ready for review releases the machine, so the next one starts by itself.
- [x] **Big files**: anything over 96 MB goes up in 64 MB parts (S3 multipart), from the browser as well, so a
      multi-GB source video works and a dropped line costs one part instead of the whole file.
- [x] **Postgres and Redis** (local Docker, `app/docker-compose.yml`): the whole suite passes on SQLite *and* on
      Postgres; with `REDIS_URL` the live events go through Redis so several replicas share them.
- [x] **Rented workers** (`dubstudio/cloud.py`, RunPod serverless): the image `sameer27488/dubstudio-worker:cpu`
      carries the pipeline scripts **and the model weights**, so a job downloads nothing while it runs (five bare-pod
      failures taught us that). `CLOUD_STEPS=lip_gpu` sends the lip scan there: the episode is cut into the
      pipeline's own 15 s pieces, shared round-robin between `CLOUD_PIECES` machines, and the range files come back
      **byte-identical to a scan done here**. The endpoint keeps 0 workers when the queue is empty, so idle time is
      free. A share that fails (a slow or reclaimed machine) is sent again on its own — the shares that finished are
      kept, so one bad machine costs one share, not the episode.
- [x] **The editor's media from R2** (see §7): a machine that never had the big files can still play the episode.
- [ ] Worker images for the other steps (render, transitions, subtitles) and a CUDA image for the face map;
      per-**step** storage sync instead of per-episode (the code is ready for it: `storage.fetch_inputs(kinds=…)`).
- [x] **CORS on the bucket** (owner, 2026-10-31) → `UPLOAD_MODE=direct` is on: a 120 MB file from a real
      browser went to R2 in two parts without touching the API.

The Mac keeps working exactly as before: a show that has a folder still reads it from disk, and `fetch` / `publish` /
`cleanup` do nothing when there is nothing in the cloud.

## 6. Settings and keys: what works, what is still needed

Checked 2026-10-31 on the running app (every health check green). **Nothing is missing for the process as it runs
today**; the list below is what each next step needs.

| Setting | State | Needed for |
| --- | --- | --- |
| Claude: `OPENROUTER_API_KEY` | **working, and now the one in use** (`AI_PROVIDER=openrouter`): the project's own key, **$250 a month**, $0.00 spent | every Claude step. Public internet, so a deployed worker reaches it without the VPN |
| Claude: `AI_BASE` / `AI_KEY` / `AI_EMAIL` (gateway) | still configured, still works; $107.06 of $200 spent | the fallback: `AI_PROVIDER=gateway`. VPN only, so it cannot be used from Render |
| `R2_*` | working (bucket `dubstudioprod`, folder `dub-studio-mac/`) | uploads, deliverables, the database copy |
| `ADMIN_*`, `AUTH_*` | working | sign-in |
| Pipeline Python, face Python, models, ffmpeg, font | working (pinned in `app/config.json`) | the pipeline steps |
| CORS rule on the bucket | **done** (owner, 2026-10-31) — direct uploads verified from a real browser | `UPLOAD_MODE=direct`: the browser uploads straight to R2. Add the Render URL to `AllowedOrigins` at deploy time |
| `FAL_KEY` | not set | fal.ai video generation — on hold, not needed |
| `DATABASE_URL` | works (local Docker Postgres on 5433; the whole suite passes on it) | the deployed database — point it at a Render Postgres when we deploy |
| `REDIS_URL` | works (local Docker Redis on 6380) | live events shared between API replicas and workers |
| RunPod endpoint for **our** GPU image | not set (`RUNPOD_*` in the owner's file belongs to his other app) | the GPU worker phase (lip scan, Whisper, face map) |
| `PUBLIC_WEB_ORIGIN`, `AUTH_COOKIE_SECURE=true` | not set | when the app is served over https on Render |

## 7. Playing an episode on a machine that has none of the files (2026-11-01)

The editor needs four things to play an episode: the **540-px preview copy**, the **dub preview** (the exact AAC copy
— browsers run ahead on some MP3s, see `timeline.dub_preview`), the **waveform** and the **thumbnails**. All four are
made by ffmpeg from the originals, which a deployed API box does not have. So:

- When they are made here, a copy goes to R2 under `…/ep<N>/work/` (`storage.mirror`, also on first serve, so
  episodes made before today are covered the first time anyone opens them).
- **Playback** (`GET /api/episodes/{id}/media/{src_proxy,dub_preview,src_video,dub_audio}`): if the file is on this
  machine it is served from disk; if not, the browser is sent a **307 to a signed R2 link** that lasts 3 hours. R2
  answers the range requests, so scrubbing works and the API box carries no video bytes.
- **Thumbnails and the waveform** need a real file, so those pull the small preview copy down to the work folder once
  (`storage.ensure_local`) and work from it. The waveform is a few kB of JSON and travels with the episode.
- Nothing anywhere? Still a plain 404, as before.

This is also what makes `cleanup` safe: after the big local files are deleted, the episode still plays.

### What else the editor needed (2026-11-02)

Serving the media was not enough: the editor is **built out of the small work files** — the edit list
(`manifest_groups.json`), the rows, the lip-sync, the sentences, the SRTs. On a box that never processed the episode
it answered *"the edit list is not made yet"* for a finished episode. So `storage.fetch_work_files` brings them down
once per episode, the first time anyone opens it: the same patterns `publish_outputs` sends up (`WORK_KEEP`), small
files only, and they are skipped if they are already here.

Also fixed while checking this on a real bare box:
- the **project bin** listed nothing, because it filtered on "is the file on disk". It now lists a file that is only
  in R2 (`in_cloud: true`) and downloading one goes straight to R2.
- an input file with **no `r2_key` on its row** (every episode made from a Mac folder) was a 404. The key its name
  would have is tried, and failing that the single object in that kind's folder — only when there is exactly one,
  so it never guesses between two.
- `final_check.jpg` / `qc_sheet.jpg` were not in `WORK_KEEP`, so the QC images never left the Mac. `*.jpg` is now
  kept — top level only, **not** the thousands of `thumbs/*.jpg`. (That trap is real: `Path.glob("*.jpg")` is the
  top level, but `PurePosixPath("thumbs/x.jpg").match("*.jpg")` is `True`, so the obvious way to match coming back
  down would have pulled every thumbnail. A test pins it.)

**How it was checked** — not by reading: the API image was run with an empty library and the real database, which is
exactly a deployed box, and every endpoint the editor calls was walked. Before: 4 of 15 worked. After: **15 of 15**
— the timeline (105 clips), the video and dub as 307s to R2, the waveform, a thumbnail cut from a preview copy
pulled out of R2, the bin, QC markers, the deliverables and the Premiere XML. Then a real edit: trim a clip, `check`
(which correctly **refused** a careless trim that left a 200 ms black gap), autosave the draft, save **version 2**,
read it back, add a QC note. All of it on a machine holding none of the episode's files.
