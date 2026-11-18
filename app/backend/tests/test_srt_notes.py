"""The script/SRT comparison ignores a Word Count header and a stage note.

IMTS episode 69: "Word Count: 1369" is three tokens, so the small-leftover pass
pasted it onto the first line. "[ambience voice]" is not all capitals, and the
keyword list spells AMBIENT, so that note was compared as if it were dialogue.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipeline_tools"))

import mksrt
import srtclean as P


def _srt(lines):
    blocks = []
    t = 1000
    for i, line in enumerate(lines, 1):
        a, b = t, t + 400
        blocks.append(f"{i}\n00:00:{a // 1000:02d},{a % 1000:03d} --> 00:00:{b // 1000:02d},{b % 1000:03d}\n{line}\n")
        t = b
    return "\n".join(blocks)


def test_extract_drops_the_header_and_the_ambience_note_and_keeps_the_line(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text(
        "Word Count: 1369\n"
        "[ambience voice] Als Keira hörte das.\n"
        "[Ambience: rooms| bedroom] Er trat näher.\n"
        "(ambience voice) Hallo.\n"
        "[Nascosto] bleibt [Razza: Umano].\n"
        "[ambient noise] Ende.\n"
        "[AMBIENCE VOICE] Schluss.\n",
        encoding="utf-8")
    words = P.extract_script_words(script, list(P.STAGE_TAG_RE_DEFAULT))
    text = " ".join(words)
    assert "1369" not in words and not any(w.lower().startswith("count") for w in words)
    assert "ambience" not in text.lower() and "ambient" not in text.lower() and "bedroom" not in text.lower()
    assert words[:4] == ["Als", "Keira", "hörte", "das."]
    assert "Nascosto" not in text and "Razza" not in text and "Umano" not in text
    assert "Hallo." in words and "Ende." in words and "Schluss." in words


def test_word_count_goes_even_when_stage_tags_are_turned_off(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("Word Count: 1,369 Als Keira.\n[ambience voice] Hallo.", encoding="utf-8")
    words = P.extract_script_words(script, [])
    assert words[0] == "Als"
    assert any("ambience" in w.lower() for w in words)


def test_script_wins_does_not_paste_the_header_and_still_takes_the_script_spelling(tmp_path):
    srt = tmp_path / "heard.srt"
    srt.write_text(_srt(["Als", "Kira", "hörte das."]), encoding="utf-8")
    done = mksrt.script_wins(srt, ["Word", "Count:", "1369", "Als", "Keira", "hörte", "das."])
    text = srt.read_text(encoding="utf-8")
    assert "Word" not in text and "1369" not in text
    assert "Keira" in text and "Kira" not in text
    assert any("Kira" in change and "Keira" in change for change in done)


def test_script_wins_does_not_paste_an_ambience_note_between_two_words(tmp_path):
    srt = tmp_path / "heard.srt"
    srt.write_text(_srt(["Als", "Keira", "hörte"]), encoding="utf-8")
    mksrt.script_wins(srt, ["Als", "[ambience", "voice]", "Keira", "hörte"])
    text = srt.read_text(encoding="utf-8")
    assert "ambience" not in text.lower()
    assert "Als" in text and "Keira" in text and "hörte" in text


def test_a_real_short_difference_longer_than_three_words_stays_as_heard(tmp_path):
    srt = tmp_path / "heard.srt"
    srt.write_text(_srt(["eins zwei drei vier"]), encoding="utf-8")
    mksrt.script_wins(srt, ["alpha", "beta", "gamma", "delta", "epsilon"])
    text = srt.read_text(encoding="utf-8")
    assert "eins zwei drei vier" in text
    assert "alpha" not in text


def test_the_cleaner_follows_the_tool_and_does_not_paste_script_only_words(tmp_path):
    """The comparison is the SRT cleaning tool: a missing space is split, a close
    spelling takes the script, and words the audio never said are not added."""
    script = tmp_path / "script.txt"
    script.write_text("Word Count: 1369\n[ambience voice] scampo.Viola restava.\n", encoding="utf-8")
    heard = tmp_path / "heard.srt"
    heard.write_text(_srt(["scampo.", "Viola", "restava."]), encoding="utf-8")
    res = P.clean_episode(1, str(script), str(heard), tmp_path, tmp_path, sensitivity="aggressive")
    text = Path(res["out_path"]).read_text(encoding="utf-8")
    assert "Word Count" not in text and "1369" not in text and "ambience" not in text.lower()
    assert "scampo." in text and "Viola" in text and "restava." in text

    script2 = tmp_path / "only.txt"
    script2.write_text("alpha beta gamma delta epsilon\n", encoding="utf-8")
    heard2 = tmp_path / "heard2.srt"
    heard2.write_text(_srt(["eins"]), encoding="utf-8")
    res2 = P.clean_episode(2, str(script2), str(heard2), tmp_path / "b", tmp_path / "b")
    text2 = Path(res2["out_path"]).read_text(encoding="utf-8")
    assert "eins" in text2 and "alpha" not in text2


def test_clean_then_script_wins_leaves_the_header_out_of_the_line(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("Word Count: 1369\n[ambience voice] Als Keira hörte das.\n", encoding="utf-8")
    heard = tmp_path / "heard.srt"
    heard.write_text(_srt(["Als", "Kira", "hörte", "das."]), encoding="utf-8")
    res = P.clean_episode(69, str(script), str(heard), tmp_path, tmp_path, sensitivity="aggressive")
    sw = P.extract_script_words(script, list(P.STAGE_TAG_RE_DEFAULT))
    mksrt.script_wins(res["out_path"], sw)
    text = Path(res["out_path"]).read_text(encoding="utf-8")
    assert "Word Count" not in text and "1369" not in text and "ambience" not in text.lower()
    assert "Keira" in text and "hörte" in text
