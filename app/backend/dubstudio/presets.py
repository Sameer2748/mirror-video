"""Show presets = the show profile of the skill (v18), one per known show plus New."""
import copy

# The same values for EVERY show (owner, 2026-10-20); only TDMB's subtitle style differs (white strip over its
# burned-in US captions). Measured on TOLR ep24 vs the hand-made episode: whole sentences 97.9% vs 94.5% with the
# 20-word clause rule; max drift 0.5 s (WBM QC); subtitle rows of at most 7 words / 4 s.
CLAUSE_WORDS = 0          # whole sentences: no clause splitting
CLAUSE_LONG = 0
GROUP_SIZE = 7            # the lip scan runs for every show -> groups mode 1, size 7
MAX_DRIFT_MS = 500        # a picture may run at most 0.5 s from its voice inside a group
SUB_WORDS = 7             # burned-in subtitle rows: at most 7 words ...
SUB_SECONDS = 4           # ... and 4 s
FAST_THRESHOLD = 1.25     # no clip plays faster than this (owner, 2026-10-22) ...
FAST_TARGET = 1.2         # ... a faster clip is trimmed to about this speed
# Step 4C, the scene edit (shot library, face map, Claude's scene edit with B-roll for slow lines, transition plan,
# scene-edit sheet; skill v21, docs/06): mandatory for every show (owner, 2026-10-30). It trims fast lines itself, so
# the older `fastfix` step does not run with it.
SCENE_EDIT = True

BASE = {
    "dub_lang": "it", "src_lang": "en", "labels": "IT,US", "dub_label": "IT",
    "episode_offset": 0,             # source episode = dub episode + offset (MVS: German N = US N+2)
    "lip": True, "clauses": CLAUSE_WORDS, "group_size": GROUP_SIZE, "max_drift_ms": MAX_DRIFT_MS,
    "transitions": False, "sub_style": "box", "render_subs": "keep", "ending": "auto",
    "translate_to": "en", "output_dir": "output/ep{N}",
    "file_prefix": "{SHOW}_EP{N}_{D}", "mapping_name": "{show}_ep{N}_{d}_{s}_mapping.xlsx",
    "flags": {"slow": 0.67, "slow_red": 0.5, "fast": 1.5, "fast_red": 2.0, "drift_ms": 500, "short_ms": 500, "blur": 0.25},
}

PRESETS = {
    "TDMB": dict(BASE, name="TDMB (Italian dub vs US)", labels="IT,US", src_lang="en",
                 sub_style="strip", mapping_name="ep{N}_it_us_mapping.xlsx"),
    "TOLR": dict(BASE, name="TOLR (Italian dub vs German)", labels="IT,DE", src_lang="de", transitions=True),
    "WBM": dict(BASE, name="WBM (Italian dub vs US)", labels="IT,US", src_lang="en", output_dir="outputs/ep{N}"),
    # HIH: the US video has burned-in captions (2 lines, sometimes 1 or 3): "dynamic" = a white strip 2 caption lines tall,
    # 3 where a 3-line US caption is on screen, with Italian cards that fill it (owner, 2026-10-22). Other shows get it
    # only when the owner says so.
    "HIH": dict(BASE, name="HIH (Italian dub vs US)", labels="IT,US", src_lang="en", sub_style="dynamic"),
    # TGS: German dub of the US show; the US video has burned-in captions -> the white strip (as the owner's ep34 tests)
    "TGS": dict(BASE, name="TGS (German dub vs US)", labels="DE,US", dub_label="DE", dub_lang="de", src_lang="en",
                sub_style="strip"),
    "NEW": dict(BASE, name="New show"),
}


def clause_mode(prof):
    """Every show: whole sentences (owner, 2026-10-20; was clauses only for sentences of 20+ words).
    -> (clause words, long-sentence words); (0, 0) = no splitting."""
    return CLAUSE_WORDS, CLAUSE_LONG


def preset(name):
    p = PRESETS.get(name.upper(), PRESETS["NEW"])
    return copy.deepcopy(p)


def names(show_code, number, profile):
    """Output file names of an episode, the skill's naming pattern."""
    d_label = profile.get("dub_label") or profile["labels"].split(",")[0]
    dl, sl = (x.lower() for x in profile["labels"].split(","))
    pre = profile["file_prefix"].format(SHOW=show_code.upper(), N=number, D=d_label)
    return {
        "clean": f"{pre}_final_clean.mp4",
        "dub_subs": f"{pre}_final_{dl}_subs.mp4",
        "en_subs": f"{pre}_final_en_subs.mp4",
        "en_srt": f"{pre}_english_google.srt",
        "mapping": profile["mapping_name"].format(show=show_code.lower(), N=number, d=dl, s=sl),
    }
