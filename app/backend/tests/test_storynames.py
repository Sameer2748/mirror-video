"""Story names: a source line renames the face group seen alone in its shot (episode 69, Jake / Frankie)."""
import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "dubstudio" / "pipeline" / "storynames.py"
_spec = importlib.util.spec_from_file_location("storynames", _p)
sn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sn)


def _world(group_names):
    cast = [{"name": "Jake", "look": "brown jacket"}, {"name": "Frankie Allen", "look": "navy suit"}]
    shots, fshots = [], {}
    # group 1 is the navy-suit man, the library calls him Jake; group 2 is the brown-jacket man, called Frankie
    for i, (lab, g) in enumerate([("Jake", 1)] * 4 + [("Frankie Allen", 2)] * 4, start=1):
        shots.append({"n": i, "start": i * 10.0, "end": i * 10.0 + 9, "people": [lab], "broll": "yes"})
        fshots[str(i)] = {"persons": [{"name": group_names[g], "view": "face", "g": g}], "talking": []}
    lib = {"cast": {"cast": cast}, "shots": shots}
    face = {"groups": {str(g): {"name": n, "faces": 500, "how": "Claude (reference faces)"} for g, n in group_names.items()},
            "shots": fshots}
    return lib, face


LINES = [{"start_ms": 11000, "end_ms": 15000, "text": "Frankie frowned."},
         {"start_ms": 21000, "end_ms": 25000, "text": "Frankie nodded."}]


def test_group_is_found_by_id_even_when_a_name_sits_on_several_groups():
    lib, face = _world({1: "Frankie Allen", 2: "Frankie Allen"})       # Claude gave both faces one name
    sn.apply_story_names(lib, face, LINES)
    assert face["groups"]["1"]["name"] == "Frankie Allen"
    assert face["groups"]["2"]["name"] == "Jake"                      # the other side of the swap


def test_library_names_swap_with_the_faces():
    lib, face = _world({1: "Officer Cohen", 2: "Isla"})
    sn.apply_story_names(lib, face, LINES)
    assert [r["people"][0] for r in lib["shots"]] == ["Frankie Allen"] * 4 + ["Jake"] * 4
    assert face["shots"]["1"]["persons"][0]["name"] == "Frankie Allen"
    assert face["shots"]["5"]["persons"][0]["name"] == "Jake"


def test_old_face_map_without_group_ids_changes_nothing():
    lib, face = _world({1: "Jake", 2: "Frankie Allen"})
    for s in face["shots"].values():
        for p in s["persons"]:
            p.pop("g")
    before = [list(r["people"]) for r in lib["shots"]]
    sn.apply_story_names(lib, face, LINES)
    assert [r["people"] for r in lib["shots"]] == before


def test_one_line_is_not_enough():
    lib, face = _world({1: "Officer Cohen", 2: "Isla"})
    sn.apply_story_names(lib, face, LINES[:1])
    assert face["groups"]["1"]["name"] == "Jake"               # no story change: the library's vote, not Claude's guess
    assert [r["people"][0] for r in lib["shots"]][:4] == ["Jake"] * 4
