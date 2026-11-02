"""qc_final's exit code: a missing --videos file fails the job; a QC finding does not.

The script runs at import, so this drives it as a subprocess on a tiny fixture instead of importing it.
"""
import json, subprocess, sys
from pathlib import Path

QC = Path(__file__).resolve().parents[1] / "dubstudio" / "pipeline" / "qc_final.py"


def _run(tmp_path, videos, *, short_broll=False):
    work, lib = tmp_path / "work", tmp_path / "library"
    work.mkdir(); lib.mkdir()
    segs = [{"it_start_ms": 0, "it_end_ms": 400, "us_start_ms": 0, "us_end_ms": 400, "broll": True}] if short_broll else []
    (work / "manifest_groups.json").write_text(json.dumps({"segments": segs}))
    (work / "sentences_dub.json").write_text(json.dumps({"sentences": []}))
    (lib / "broll_library.json").write_text(json.dumps({"shots": [{"n": 1, "start": 0.0, "end": 10.0, "action": "walks"}]}))
    out = tmp_path / "QC.xlsx"
    proc = subprocess.run(
        [sys.executable, str(QC), "--work", str(work), "--library", str(lib), "--videos", *videos,
         "--out", str(out), "--no-ai"],
        capture_output=True, text=True, timeout=60)
    return proc, out


def _summary(path):
    from openpyxl import load_workbook
    ws = load_workbook(path)["Summary"]
    return {row[0]: row[3] for row in ws.iter_rows(min_row=2, max_col=5, values_only=True) if isinstance(row[0], int)}


def test_missing_video_exits_1_after_the_sheet_is_saved(tmp_path):
    proc, out = _run(tmp_path, [str(tmp_path / "gone.mp4")])
    assert proc.returncode == 1, proc.stderr
    assert "saved" in proc.stdout and out.exists()
    from openpyxl import load_workbook
    black = load_workbook(out)["4 Black - blank screens"]
    assert [c.value for c in black[2]][:3] == ["gone.mp4", "-", "missing"]


def test_existing_video_with_a_short_shot_still_exits_0(tmp_path):
    video = tmp_path / "ep.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x160:rate=25:duration=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True, timeout=30)
    proc, out = _run(tmp_path, [str(video)], short_broll=True)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert _summary(out)[2] == "FAIL"   # short B-roll is a finding, not a failed job
    assert _summary(out)[3] == "NOT RUN"
