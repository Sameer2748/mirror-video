# AI gateway — calling Claude for Step 2 (matching by meaning)

The owner's AI gateway (the one used for the Gemini lip tests) also serves **Claude**. Dub Studio uses it for
Step 2: after `analyze`, Claude reads the flagged rows by meaning and proposes the decisions a person used to
write in `spec.py`. It is built and tested (see "Test results").

## The key and settings

They are in **`app/.env`**, which is in `.gitignore`. Never print the key, put it in code, docs, logs or tests,
or commit it. A blank template is in `app/.env.example`.

```
AI_BASE=https://madeye.internal.pocketfm.org     # LiteLLM gateway, OpenAI-compatible
AI_KEY=sk-...                                    # Bearer token (owner's key, budget $100)
AI_EMAIL=ext-sameer.s@pocketfm.com               # required: sent as metadata.user_email on every call
AI_MODEL=claude-sonnet-5-5                       # Step 2 default
AI_MODEL_FAST=claude-haiku-5-5                   # cheap checks
```

Environment variables with the same names override the file. Later, the Settings page will store them in the
local DB (milestone 7).

## How to call it

The API is the OpenAI **chat completions** format, not the Anthropic Messages format.

```bash
curl -s "$AI_BASE/v1/chat/completions" \
  -H "Authorization: Bearer $AI_KEY" -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-5-5","max_tokens":500,
       "messages":[{"role":"user","content":"..."}],
       "metadata":{"user_email":"'"$AI_EMAIL"'"}}'
# reply: .choices[0].message.content , .usage.prompt_tokens / completion_tokens
```

Other endpoints:

- `GET /v1/models`: the models this key can use. Currently `claude-sonnet-5-5`, `claude-haiku-5-5`,
  `claude-sonnet-4-6` and `gemini-3.5-flash`.
- `GET /key/info`: `info.spend` and `info.max_budget`. Spend was $8.86 of $100 on Oct 15, 2026.

Things to watch:

- **Do not send `temperature`** to the Claude 5.5 models. The gateway routes them through Vertex and answers
  HTTP 400 "`temperature` is deprecated for this model".
- Sonnet sometimes writes a sentence before the JSON, and that sentence can contain things like `[13]`. Parse with
  `ai.extract_json(text, "decisions")`, which takes the first object that has the key.
- Retry on 429 and 5xx with backoff. `ai.chat` already does this.
- Images and video go in a content list (`{"type":"image_url",...}` / `{"type":"file","file":{"file_data":"data:video/mp4;base64,..."}}`),
  the way the Gemini tests sent them (`pipeline_tools/lipgemini.py`).

## Code in the app

| File | What |
| --- | --- |
| `app/backend/dubstudio/ai.py` | `settings()`, `available()`, `chat(prompt, model, max_tokens)` → `(text, usage)`, `extract_json`, `key_info()`, `models()` |
| `app/backend/dubstudio/pipeline/ai_match.py` | Step 2 by Claude: `propose(work_dir)` → proposals + `W/spec_ai.py` + `W/ai_proposals.json`; CLI `python -m dubstudio.pipeline.ai_match <W> [--model M] [--apply]` |
| `main.py` | `GET /api/ai/status` (configured, models, spend); `POST /api/episodes/{id}/decisions/ai` body `{"apply": false, "model": null}` |
| `tests/test_ai.py` | offline test with a fake gateway; live check with `DUBSTUDIO_LIVE_AI=1 pytest tests/test_ai.py -k live` |
| `tools/eval_ai_match.py` | compares the AI proposals with a hand-made `decisions.json` |

**How `propose` works**

1. It takes the flagged rows from `prep.json`: unmatched rows plus "Case A reuse" rows, the same rows `sheet`
   refuses without a decision.
2. It clusters rows that are no more than 5 apart and makes one call per cluster, 4 to 6 calls in parallel.
3. Each call gets the skill's Step-2 rules, the dub rows ±4 around the cluster with their current automatic
   source, the source window those neighbours point to (±4), and the far-away high-similarity candidates.
4. Claude answers in JSON. Each answer is `{row, action: assign|group|confirm|gap, src: S | [a,b], members, reason, confidence}`.
5. A group answer also decides its member rows. Rows the reply skips are asked again, alone.
6. Answers become spec lines (`A`, `G`, `C`, `GAP`). The keywords come from the real texts, so `from_spec`
   always passes its check. The reason is tagged `AI <model> (<confidence>)`.
7. **Nothing is applied by default.** The Check-matching screen shows the proposals, with low confidence
   (< 0.6) highlighted. The editor accepts them all or changes some, then imports. `{"apply": true}` imports
   straight away.

## Test results (Oct 15, 2026, claude-sonnet-5-5)

The proposals were compared with the decisions made by careful hand reading (`examples/*/decisions.json`).
A row counts as "the same" when it gets the same source sentence or an overlapping range.

| Episode | Flagged rows | Same as the hand read | Cost / time |
| --- | --- | --- | --- |
| WBM ep17 (clause mode) | 38 | 36–37 over 4 runs | ~21k in + 4k out tokens, ~$0.12, ~40 s |
| TOLR ep24 | 19 | 18–19 over 4 runs | ~18k in + 3k out tokens, ~$0.10, ~30 s |

The few rows that differed were all ones Claude gave low confidence (0.3–0.6), for example ep17 row 148
"Dove la stava portando?". Claude called it a gap, while the hand read used S150. This is why low confidence is
shown to the editor.

Not covered: Step 2 only decides the **flagged** rows. The hand read of ep17 also regrouped many rows that were
not flagged (clause mode, QC notes about the picture). That stays with the editor and the timeline flags, and
there is no AI for it yet.

## Other uses (not built yet)

- **Translation fallback** (Step 9), when Google answers 429. Send the Italian SRT cues in batches of about 40 to
  `AI_MODEL` and ask for English, one line per cue, keeping the numbering. Name the result `_english.srt`.
- Lip scan: **do not** use AI. Our local scan won the comparison (see `01_PROJECT_CONTEXT.md`).

## Video generation (Veo) through the gateway (2026-10-26)

Models on the key: `veo-3.1-fast-generate-001` (first + last frame), `veo-3.0-generate-001` (first frame). LiteLLM's
OpenAI-style video API:

- `POST /v1/videos` `{"model", "prompt", "seconds": 4|6|8, "size": "1280x720"|"720x1280", "metadata": {"user_email"},
  "extra_body": {"instances": {"image": {"bytesBase64Encoded", "mimeType"}, "lastFrame": {...}}, "generateAudio": false,
  "negativePrompt": "..."}}` + header `user_email` → `{"id": "video_…", "status": "processing"}`.
  The frames MUST be inside `extra_body`; a top-level `instances` is dropped without an error (you get a text-only video).
- `GET /v1/videos/{id}` → `status` processing / completed / failed; `GET /v1/videos/{id}/content` → the MP4 bytes.
- 4 s at 720p took 35–97 s. Output is 16:9 / 9:16 only, 24 fps. Code: `app/backend/dubstudio/gen.py` (`madeye_body`,
  `_madeye`, `run_job`).
- Reference images (Veo 3.1): `extra_body.instances.referenceImages = [{"image": {...}, "referenceType": "asset"}, …]`
  (up to 3). Only 8 s, and not together with `image` / `lastFrame` (Vertex: "Image and reference images cannot be both
  set"). Tested 2026-10-26.


## Two providers, and prompt caching (2026-10-31)

**Provider** (`dubstudio/ai.py`, `AI_PROVIDER` in app/.env):

| | gateway | openrouter |
| --- | --- | --- |
| who | the owner's Pocket FM gateway | OpenRouter, public internet |
| keys | `AI_BASE`, `AI_KEY`, `AI_EMAIL` | `OPENROUTER_API_KEY` |
| models | `claude-sonnet-5-5`, `claude-haiku-5-5`, … | `anthropic/claude-sonnet-5.5`, `anthropic/claude-haiku-5.5`, … |
| budget | `GET /key/info` | `GET /key` (+ `/credits`) |
| reachable from a deployed worker | **no, VPN only** | **yes** |

With no `AI_PROVIDER` the choice is automatic: OpenRouter when its key is set, else the gateway. Our own model
names are mapped per provider, so no step had to change. In use since 2026-10-31: **openrouter /
anthropic/claude-sonnet-5.5** ($2 per M in, $10 per M out).

**Prompt caching** (`AI_CACHE=true`, on by default). Every round of a scene-edit stretch re-sends the same rules,
shot list and ~95 frame strips — tens of thousands of tokens. `sceneedit.cacheable()` marks the last block of that
part with `cache_control: ephemeral`, so the provider keeps it: the first call writes the cache, the later ones
read it at a tenth of the price. **The prompt does not change** (same blocks, same order, same bytes), so the
answers do not either; `AI_CACHE=false` sends exactly what it used to.

- Measured on two calls sharing a 7.3 k-token image block: $0.0184 → **$0.0009** (7 303 of 7 334 tokens cached).
- Measured on the ep34 5-minute scene edit: **$4.99 → $2.29**, 0 problems left either way. Caching paid for about
  24% of that run (roughly half the input bill); the rest was needing 19 calls instead of 36.
- `ai.cache_stats()` reports calls, prompt tokens, cached tokens and cost; the run-times sheet shows the real
  dollars per step (it read the old gateway's endpoint before and printed $0.00).
- Caching discounts **input only**. Output (43% of the bill) falls when the re-cut rounds do — the next job.
