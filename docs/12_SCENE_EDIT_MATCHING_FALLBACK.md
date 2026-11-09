# Scene edit: use the matching clip if Claude leaves a hole

Copy this into the Stargate app’s logic. Do not change matching, lip scan, groups, or the editor. This is only what happens **after** Claude’s scene edit.

## Rule

1. Matching already picked source footage for each dub line (the automatic cut).
2. Scene edit (Claude) may replace that cut with a better one (B-roll on slow lines, etc.).
3. If Claude **does not cover** some dub time — a hole, or a “placeholder” piece with **no source footage** (`from == to`) — **and matching already had a clip there, use that matching clip**. Do not leave black screen.
4. If matching also had **no** clip there (a real not-found line), leave the hole. The editor banner is then correct: the human fills it.

Claude is allowed to fail a stretch. Matching is the fallback, not “not found”.

## When it runs

- Right after scene edit writes its edit list.
- Again when `use_scene` copies that list into the editor (so an episode that already ran scene edit is repaired without calling Claude again).

Tiny leftover at the end (1–10 ms because Claude writes hundredths of a second) is **not** a hole: stretch or trim the last piece as before.

## What not to change

- Step order.
- Matching / lip / groups / render algorithms.
- Claude prompts, images, or JSON shape.
- Do not invent footage when matching had none.

## CPU slots (renders in parallel)

`cpu_slots` is how many CPU jobs the **API** may have in flight, including a render tail that is only *waiting* on a rented machine. Default **8** (was 2). One episode’s render tail still uses **one** rented CPU box (the video is too big to split). Several episodes can render at once, up to `cpu_slots` and the idle RunPod CPU workers.

Deployed 2026-11-09 (owner): live Render `handover-doc-review`, CPU worker image rebuild, Stargate dest. TAB EP11 Retry of `use_scene` applies the fill with no new Claude spend.
