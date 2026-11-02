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

## 8. Any step on a rented machine (2026-11-02)

A deployed API box can run **none** of the 37 steps: no Whisper, no models, no pipeline scripts. So an image per
step was the wrong shape — there is **one image with the whole pipeline in it**
(`sameer27488/dubstudio-worker:pipeline`), and the app sends it *the command it was going to run anyway*, with this
machine's paths swapped for placeholders the worker fills in with its own:

```
{PY} {FPY}   the pipeline's python        {SCRIPTS} adaptation-tool/scripts    {TOOLS} adaptation-tool
{SCENE}      the scene-edit scripts       {MODELS}  the Whisper / sentence models
{WORK}       the episode's work folder    {OUT}     the show's output folder   {APP} the app's backend
{F0} {F1} …  one big input file each (source video, dub audio, the font): a signed link that expires
```

The pipeline scripts are **not touched** (CLAUDE.md's rule). Checked against all 24 real commands of ep34: every
one comes out with no leftover path from this Mac.

Only the episode's **small** work files travel up (one tar), the big inputs go as signed links, and only what the
step **changed** comes back — a step that writes one JSON brings back one JSON. Measured: in a finished episode the
only work files over 1 MB are videos; the lip scan's 42 range files, the meshes and the manifests are all a few kB.

**`config.json` is translated both ways.** It records absolute paths on this Mac (`src_video`, `dub_audio`,
`models`, `video_out`, `clean_final`…) and pipeline stages read file paths out of it, not just from argv. It goes up
pointing at the worker's files and comes back pointing at ours — without that, the step either fails or poisons the
work folder with `/work/...` paths that the next local step cannot find.

### What can and cannot go, and why

| | steps | why |
| --- | --- | --- |
| **Can go now (13)** | `mksrt_dub` `mksrt_src` `words_src` `words_dub` `analyze` `sheet` `manifest` `lip_mesh` `lip_merge` `lipsync` `groups` `trans_plan` `translate` — plus the lip scan, which has its own split-into-pieces path | their inputs are the episode's own files plus small work files |
| **One machine only (7)** | `render` `verify` `bookends` `transitions` `subs_dub` `subs_en` `check` | each reads the **previous step's multi-GB video**, and the work folder records where it is. The machine that runs `verify` is not the one that ran `render`, so the file would have to travel up and down — gigabytes each way, per step. That chain belongs on one machine. |
| **Needs an AI key there (3)** | `library` `facemap` `qc` | they ask the app's own AI client for a vote. Sending an OpenRouter key to a rented machine is the owner's call, so they stay here unless `CLOUD_AI=true`. Note *why* this matters: without a key `facemap` does not fail, it gives a **worse** answer ("groups without a clear vote stay unknown") — and a quietly worse answer is the thing to avoid. |

`CLOUD_STEPS=all` therefore means *every step that can actually work there* — turning it on cannot break a run at
step 25. Asking for `render` by name is refused too.

### The image

Two pythons, for the same reason the Mac has two: the RunPod SDK pulls opentelemetry (protobuf ≥ 5) and
mediapipe 0.10.14 pins protobuf < 5 — one environment cannot hold both (`pip` says ResolutionImpossible). The
handler runs on the system python; the pipeline runs in `/opt/pipeline_env`, which is what `{PY}` means.

Pinned to **the versions the Mac actually runs** (numpy 1.26.4, mediapipe 0.10.14, faster-whisper 1.2.1,
ctranslate2 4.8.3, onnxruntime 1.31.0, sentence-transformers 6.1.0, scenedetect 0.7.2 …), because the point of a
worker is that it gives the *same* answer; `setup_pipeline.sh` installs them unpinned, which is fine on one machine
and not here. torch is the exception — 2.14 has no CPU wheel for this base, and the lip scan already came back
byte-identical on 2.5.1. Only the two models the pipeline really uses are baked in (faster-whisper-small and
paraphrase-multilingual-MiniLM-L12-v2, ~920 MB); `LaBSE`, `mpnet` and the cross-encoder appear only in the
`sanity_*` experiment scripts, so 3.3 GB of the models folder is not shipped.

### Worth fixing: we are paying GPU prices for CPU work

The endpoint is called `dubstudio-cpu`, but it is configured `gpuCount: 1` with a list of NVIDIA cards — so every
job so far has rented a **GPU** machine (about $0.58/h each) to do work that was measured yesterday at **1% GPU
utilisation**. A CPU-only serverless endpoint would cost a fraction of that. Idle workers really are free
(`currentSpendPerHr: 0` with 4 idle), so the waste is only while jobs run — but it is the single easiest saving
available. Also found: RunPod **retried 3 jobs by itself** during the full-episode lip scan, which explains part of
the gap between 1,736 s of wall clock and 1,085 s of reported execution.

## 9. Deployed on Render (2026-11-02)

**Live: https://dubstudio-yg5s.onrender.com** — the owner's existing `DUBSTUDIO` service
(`srv-dar686jncjis73c9rr90`), repointed at our repo as he asked ("we dont need this app now can we replace its
github"). It was already a Docker web service on 2 CPU / 8 GB, so only the repo, the build paths and the
environment changed.

| | |
| --- | --- |
| repo / branch | `Sameer2748/mirror-video`, `handover-doc-review`, auto-deploy on push |
| built from | `app/backend/Dockerfile`, context `app/backend` (API + the built screens; no pipeline, no models) |
| database | `dubstudio-db`, Postgres 16, **singapore** — the same region, so the internal connection string works |
| disk | the service's existing 20 GB at `/app/output`; `DUBSTUDIO_LIBRARY=/app/output/library` reuses it rather than destroying it to mount a new one |
| health check | `/api/info` |
| secrets | copied from `app/.env` by `app/scripts/deploy_render.py` — never printed, never committed |

The old app's 27 environment variables (its ElevenLabs key, its own R2 and RunPod settings) were replaced by our
22. Nothing else of it was touched; its disk is reused, not deleted.

### The first deploy crashed, and it was my bug

`update_failed`, "Application exited early". The build was fine. Render sets `WEB_CONCURRENCY` from the CPU count,
so uvicorn started **two** processes, and both built the schema at the same moment —
`CREATE TABLE IF NOT EXISTS` is **not atomic** against another session doing the same. Both look, both see nothing,
both create, and the loser dies with `duplicate key value violates unique constraint "pg_type_typname_nsp_index"`.
My local smoke test ran one process, so it never showed.

Fixed properly rather than by turning concurrency off: the schema, the migration and the first account are made
under a Postgres **advisory lock**, so any number of replicas can start together. Proved on the real thing — the
next deploy ran with `WEB_CONCURRENCY=2`, the exact condition that crashed, and came up **live**. It is now set to
**1**, for a different reason: a second process in the same container means a second job runner polling for the same
steps. More than one needs `REDIS_URL` for shared events first.

### Checked on the live site, not assumed

- the API answers, the screens load, an API call with no session is 401, a wrong password is 401;
- signing in as the owner works, and the deployed database is its own (0 shows — the Mac's library is not shared);
- **R2 is reachable from Singapore** (`ok=true`, bucket `dubstudioprod`) and **OpenRouter is configured**;
- a show made with no Mac folder gets `uploads: true` and its own staging folder on the disk;
- **the real web upload works end to end**: the API signs a link, the bytes go **straight to R2**, `confirm`
  registers the file, and the episode then has its `src_video`. (That test was server-side, which needs no CORS.)

### The one thing left for the owner

**Add `https://dubstudio-yg5s.onrender.com` to the R2 bucket's CORS `AllowedOrigins`** (Cloudflare dashboard → R2 →
`dubstudioprod` → Settings → CORS). Our R2 token is object-scoped, so it cannot read or change bucket CORS itself
(`403 Access Denied` on `GET /?cors`) — this one needs the dashboard. Without it, uploading from a real **browser**
on the deployed site will fail even though everything else works.

```json
[{"AllowedOrigins": ["http://localhost:8766", "https://dubstudio-yg5s.onrender.com"],
  "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedHeaders": ["*"],
  "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]
```

### What the deployed site can and cannot do

**Can**: accounts and sign-in, shows and episodes, uploads straight to R2, the queue, the editor (it plays and
saves — §7), review, the deliverables.
**Cannot yet**: process an episode. Only the lip scan has a worker image live; the pipeline worker image that would
run the other 13 cloud-capable steps (§8) is built but **not pushed** — the Docker VM on the Mac ran out of disk
mid-export (`failed to create temp dir: input/output error`) and corrupted its own content store. Processing stays
on the Mac until that image is up.

### Proved on a real step, and what that found (2026-11-02)

The image is built, the endpoint points at it, and `manifest` ran on a rented machine: **`manifest.json` came back
byte-identical to the Mac's**. Getting there found two things that no amount of reading the code would have shown.

**1. A step's files may be named only inside `config.json`.** `dubstitch.py manifest --work W` is the entire
command — the video is never mentioned — and then `cmd_manifest` does `probe_duration_ms(cfg["src_video"])`. The
first real run died on `ValueError: could not convert string to float: ''`, which is ffprobe being handed a path to
a file that was never sent. `cloud._files_named_in_config` now walks the config for absolute paths to real files
outside the work and output folders and ships those too. A test pins it.

**2. The worker's ffmpeg disagreed with the Mac's, and it reached the output.** The first comparison was
1,242 of 1,244 files identical, with `manifest.json`'s `total_ms` at **664686** against the Mac's **664635**:

| | ffprobe | ep34's dub MP3 | `total_ms` |
| --- | --- | --- | --- |
| the Mac | 8.1.2 | 664.634535 s | 664635 |
| Debian slim's apt ffmpeg | much older | — | 664686 |

Nothing was wrong with the pipeline: two ffmpeg versions estimate a **VBR MP3's** duration differently. But the
pipeline turns that straight into `total_ms`, which is where the last clip ends, and **51 ms is more than a frame at
25 fps**. The image now installs a pinned static **ffmpeg 8.1** (n8.1.3) rather than whatever apt ships, and the
manifest then matches exactly.

The lesson generalises: the lip scan was byte-identical across machines because torch and TalkNet are
deterministic, but **anything that probes or decodes media is only as identical as its ffmpeg**. Pinning the
worker's versions to the Mac's matters more than it first looked — which is why the pip list is pinned to
`pipeline_env`'s versions too.

(One more difference in that comparison — `config.json`'s `manifest` key flipping from `manifest_groups.json` back
to `manifest.json` — is an artefact of the test re-running an early step on an episode that had already finished,
not a fault.)

### The path translation was corrupting the config, and how that was found (2026-11-02)

Comparing a real run's `config.json` with the Mac's showed paths like

```
/Users/manish/conductor/Users/manish/conductor/workspaces/…/TGS_ep34spaces/…
/Users/manish/Downloads/TGS US_DE/Users/manish/Downloads/TGS US_DE/output/ep34_openrouter_fullput/…
```

The translation was a **text replace**, and `/work` is a substring of `/work`**spaces** while `/out` is a substring
of `/out`**put** — so every path in the file that merely contained those letters was mangled. Processing an episode
in the cloud would have quietly wrecked its work folder. It now walks the **parsed JSON** and matches whole path
segments, so a sibling episode's folder (`…/work/TGS_ep35/…`) and an unrelated `workspaces` path are left alone; a
test covers exactly that corruption in both directions. ep34 was never damaged — the test harness puts the Mac's
files back after every run.

### Careful with "identical": a step that skips proves nothing

Two comparisons looked perfect and meant nothing, because the step **skipped**:

- `words_src` finished in 2.8 s — it reuses what is already there, so "identical" only meant the file was never
  rewritten. (It also writes `sentences_src.json`, not `TGS_EP34_EN_words.json`; that one is `mksrt`'s.)
- `mksrt_src` finished in 0.7 s even with its outputs deleted, because `mksrt` caches the raw transcription in
  `<work>/asr/` and rebuilds the SRT from it. Byte-identical, but it proves only that the **rebuild** matches.

To make Whisper really listen, the `asr/` cache has to go aside as well. Worth remembering for any future
comparison: **check the step's own runtime against the Mac's before believing a match.** A step that finished in
one second did not do the work.

### A CPU endpoint needs the dashboard

The endpoint is `gpuCount: 1` with a list of NVIDIA cards, and that is set by the **template's** `category`. A
template can be made with `category: CPU` through the REST API, but the endpoint still comes back with
`gpuTypeIds`, and `instanceIds` is rejected (`key provided in request body which is not in input schema`). So the
REST API only makes GPU endpoints — a CPU one needs the RunPod dashboard (Serverless → New Endpoint → CPU) or the
GraphQL `saveEndpoint`. Two endpoints created while finding this out were deleted again.

It is worth doing: this is renting GPU machines at about $0.58/h for work measured at **1% GPU utilisation**, and
GPU scarcity now actively gets in the way — three or four of the four workers sat **throttled** during today's
runs, with jobs queued behind capacity that CPU machines would not have been short of.

### Whisper on a rented machine: 5.5× faster, 99.3% the same words (2026-11-02)

The real test, with the `asr/` cache moved aside so Whisper had to listen to all 11 minutes of ep34:

| | the Mac | the worker |
| --- | --- | --- |
| `mksrt_src` | **682 s** | **124 s** (wall 175 s, including sending the video up) |

**5.5× faster** — and the reason is the same one that makes the Mac slow: Whisper never used the Apple GPU
(docs/03, `device="cpu", compute_type="int8"` is hard-coded), so this is x86 int8 against Apple-Silicon int8, and
the x86 box wins.

What it transcribed, measured on the **word sequence** rather than line by line (a different row split makes a
line diff look catastrophic when the words agree):

| file | words (Mac / cloud) | words in common | stretches differing |
| --- | --- | --- | --- |
| `TGS_EP34_EN.srt` (the cleaned one the pipeline uses) | 1,622 / 1,633 | **99.3%** | **1** |
| `TGS_EP34_EN_raw.srt` | 1,622 / 1,636 | 98.3% | 19 |
| `TGS_EP34_EN_words.json` | 1,621 / 1,632 | 99.3% | — |

- The **single** difference in the cleaned SRT is eleven words the cloud *heard and the Mac missed*:
  `"know that you might want to work on that aiden blinked"`. Not worse — if anything slightly better.
- The raw SRT's 19 differences are the usual small-model wobbles: `family has` / `family's`,
  `mother has` / `mother's`, **`Ava` / `Eva`**, a few stray apostrophes. Worth noting that the character-name one
  is in the **raw** SRT only — cleaning each side with its script repairs it, which is exactly why the cleaned SRT
  comes out at 99.3% with one differing run.
- Word timings, over the words that match: **median 0 ms, 95th percentile 20 ms** (one 4.65 s outlier, next to the
  inserted stretch).

**So Whisper is not reproducible across machines, and cannot be.** The lip scan was byte-identical because
TalkNet's ops are deterministic; Whisper is int8-quantised inference, so a different CPU architecture gives
slightly different logits and the decoder occasionally picks a different word. Nothing is broken.

**This one is the owner's call**, because the SRTs feed Step 2 matching: run Whisper in the cloud and get an
equivalent-but-not-identical transcript 5.5× faster, or keep it on the Mac for exact reproducibility. Our reading is
that the cloud transcript is as good or marginally better, and 682 s → 124 s on the single most expensive step is
worth having — but it is a change in output, so it is not ours to decide.
(The cloud's own transcript is kept at `app/library/work/_cloud_whisper/` for comparison.)
