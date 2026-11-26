"""Unused-edge pieces can have shot=None. The scene-edit xlsx dump must not KeyError SH[None]."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "dubstudio/pipeline/slowfix_edit.py"


def test_sheet_dump_does_not_index_library_with_a_none_shot():
    text = SRC.read_text(encoding="utf-8")
    dump = text.split("ws2 = wb.create_sheet", 1)[-1]
    assert 'SH[p["shot"]]' not in dump
    assert "if n is None:" in text
    assert "if n is not None and (STRIPS" in dump
