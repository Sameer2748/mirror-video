# Prompt for the new chat (paste everything below the line)

---

You are continuing work on **Dub Studio**, a local video-dubbing platform on my Mac. A previous Claude Code session
built it; you pick up exactly where it stopped. The project folder is `/Users/manish/Downloads/dub-studioo`.

Before you do anything, read these in order:
1. `CLAUDE.md` (project rules: they override your defaults)
2. `docs/07_HANDOVER.md` (where everything is, the full step list, final decisions, what I asked so far, open items)
3. `docs/03_PROGRESS.md` (what is built, with dates; read at least the last 10 sections)
4. Then 01, 02, 04, 05 and 06 in `docs/` as you need them, and `skill/SKILL_dub-clip-video-stitcher_v21_full.md`
   (Step 4C).

Then:
- Check that the app runs: `app/scripts/start.sh` (http://127.0.0.1:8765, Settings shows health) and that the tests
  pass (`cd app/backend && .venv/bin/python -m pytest -q tests`, expect 72 passed, 1 skipped).
- Tell me in plain words, in a few lines, what you understood: what the platform does, what steps run for every show,
  and what is open. Don't change anything yet.

What happens next: I'm running one episode of another show end to end myself (new show → process → review in the
editor → render). Help me with anything that breaks. Rules that matter most:
- the decisions in the handover §4 are final, so don't reopen them;
- never touch the show folders' inputs, and never print or copy the AI key in `app/.env`;
- don't change the pipeline logic without asking me;
- if a step can't run (VPN off, app down), tell me which step and why, and never skip it silently;
- after every piece of work, update `docs/03_PROGRESS.md` and report to me in plain words.
