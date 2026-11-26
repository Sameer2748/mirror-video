"""Deliverables must list worker-PUT videos that live only in R2 (IMTS IT-DE EP17)."""
from dubstudio import deliver, presets


def test_videos_in_r2_count_as_existing(tmp_path, monkeypatch):
    show = {"code": "IMTSITDE", "profile": presets.PRESETS["WBM"]}
    ep = {"number": 17, "output_dir": str(tmp_path / "out"), "work_dir": str(tmp_path / "work")}
    (tmp_path / "out").mkdir(); (tmp_path / "work").mkdir()
    sizes = {
        "IMTSITDE_EP17_IT_final_clean.mp4": 277,
        "IMTSITDE_EP17_IT_final_it_subs.mp4": 214,
        "IMTSITDE_EP17_IT_final_en_subs.mp4": 213,
    }

    def fake_head(key):
        name = key.rsplit("/", 1)[-1]
        if name in sizes:
            return {"size": sizes[name]}
        return None

    monkeypatch.setattr("dubstudio.storage.configured", lambda: True)
    monkeypatch.setattr("dubstudio.storage.head", fake_head)
    monkeypatch.setattr("dubstudio.storage.out_key", lambda s, e, n: f"shows/{s['code']}/ep{e['number']}/out/{n}")
    files = {f["kind"]: f for f in deliver.deliverables(show, ep, None)}
    assert files["clean"]["exists"] and files["clean"]["in_cloud"]
    assert files["dub_subs"]["exists"] and files["dub_subs"]["size"] == 214
    assert files["en_subs"]["exists"]
