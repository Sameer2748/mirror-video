"""Name a face group from the source story, then keep that name on every shot of that face.

The face map already separates people. The shot library's names can be swapped (episode 69: the navy-suit
man was called Jake and the brown-jacket man Frankie, while the story says the opposite). A subtitle line
renames a face only when all of these hold:

  - it names a cast member (not "he" / "she", and not a name spoken to someone);
  - it describes something visible (frowned, eyebrows knitted, nodded, looked away, clenched fists, face darkened);
  - the shot at that time shows one face;
  - a "his / her" line counts only when the previous sentence names the subject and both fall in that same shot;
  - every such line on the same face group agrees, and there are at least two.

A group with no agreed name stays as the library named it. Looks follow the face: the description written
for the old name moves to the new name.
"""
import json, re
from collections import defaultdict
from pathlib import Path

# A visible action. "looked" alone is not one: it is usually the listener, not the person named.
_ACTION = re.compile(
    r"frown(?:ed|s|ing)?|"
    r"eyebrows knitted|knitted (?:his |her |their )?eyebrows|"
    r"\bnodded\b|"
    r"averted (?:his |her )?gaze|"
    r"clenched (?:his |her )?fists|"
    r"face (?:had |has )?(?:already )?become",
    re.I)
_PRON = re.compile(r"^(?:his|her|he|she)\b", re.I)


def _aliases(cast):
    """(alias lowercase, canonical cast name). 'Allen' alone is not an alias: 'Mr. Allen' is spoken to him."""
    out = []
    for c in cast:
        name = c.get("name") or ""
        if not name:
            continue
        out.append((name.lower(), name))
        first = name.split()[0]
        if first.lower() not in ("mr", "mrs", "ms", "officer") and first.lower() != name.lower():
            out.append((first.lower(), name))
        m = re.search(r"\(([^)]+)\)", name)
        if m:
            out.append((m.group(1).lower(), name))
    out.sort(key=lambda x: -len(x[0]))
    return out


def _subject(text, aliases):
    """The cast name who is the subject, when the name starts the sentence. None for 'Mr. Allen, ...'."""
    s = text.strip().lstrip("\"'“")
    if re.match(r"^Mr\.?\s|^Mrs\.?\s|^Ms\.?\s", s):
        return None
    low = s.lower()
    for alias, canon in aliases:
        if low.startswith(alias):
            rest = s[len(alias):]
            if rest.startswith("'s") or rest[:1] in (" ", ",", "") or rest.lower().startswith(" had"):
                if rest.startswith(","):
                    return None
                return canon
    return None


def anchors(sentences, cast):
    """Lines that may name the face on screen. Each is (start_s, end_s, canonical name, text)."""
    aliases = _aliases(cast)
    named = []
    for s in sentences:
        who = _subject(s["text"], aliases)
        named.append((s["start_ms"] / 1000, s["end_ms"] / 1000, who, s["text"], _ACTION.search(s["text"]) is not None))
    out = []
    for i, (a, b, who, text, act) in enumerate(named):
        if act and who:
            out.append((a, b, who, text))
            continue
        if not act or not _PRON.match(text.strip()):
            continue
        if i == 0 or not named[i - 1][2]:
            continue
        pa, pb, pwho, _, _ = named[i - 1]
        # the pronoun keeps the previous subject only inside the same shot (the caller checks the shot)
        out.append((a, b, pwho, text, pa, pb))
    return out


def _one_face(persons, cast_names):
    """The single face/profile cast member in a shot, or None when the shot shows none or several."""
    seen = []
    for p in persons or []:
        if p.get("view") not in ("face", "profile"):
            continue
        n = p.get("name")
        if n in cast_names and n not in seen:
            seen.append(n)
    return seen[0] if len(seen) == 1 else None


def apply_story_names(library, face, sentences):
    """Rename face groups from agreed story lines. Mutates `library` and `face`. Returns a report."""
    cast = (library.get("cast") or {}).get("cast") or []
    cast_names = {c["name"] for c in cast}
    shots = {r["n"]: r for r in library.get("shots") or []}
    groups = face.get("groups") or {}
    by_name = defaultdict(list)
    for gid, g in groups.items():
        if g.get("name") in cast_names:
            by_name[g["name"]].append(str(gid))
    votes = defaultdict(lambda: defaultdict(int))
    used = defaultdict(list)
    for item in anchors(sentences, cast):
        a, b, who, text = item[:4]
        same = None
        if len(item) == 6:
            pa, pb = item[4], item[5]
            holders = [r for r in shots.values() if r["end"] > pa and r["start"] < pb]
            if len(holders) != 1:
                continue
            same = holders[0]["n"]
        hit, best = None, 0.0
        for r in shots.values():
            ov = min(b, r["end"]) - max(a, r["start"])
            if ov <= 0.1 or ov <= best:
                continue
            if same is not None and r["n"] != same:
                continue
            face_who = _one_face((face.get("shots") or {}).get(str(r["n"]), {}).get("persons"), cast_names)
            if not face_who:
                continue
            gids = by_name.get(face_who) or []
            if len(gids) != 1:
                continue
            best = ov
            hit = (gids[0], face_who, r["n"])
        if not hit:
            continue
        gid, old, n = hit
        votes[gid][who] += 1
        used[gid].append((n, old, who, text[:80]))
    mapping = {}
    report = []
    for gid, bag in votes.items():
        old = groups[gid]["name"]
        if len(bag) != 1:
            report.append(dict(group=gid, old=old, new=None, why=f"lines disagree: {dict(bag)}", lines=used[gid]))
            continue
        new, n = next(iter(bag.items()))
        if n < 2:
            report.append(dict(group=gid, old=old, new=None, why=f"only {n} line, need 2", lines=used[gid]))
            continue
        if new == old:
            report.append(dict(group=gid, old=old, new=old, why=f"{n} lines agree", lines=used[gid]))
            continue
        mapping[old] = new
        groups[gid]["name"] = new
        groups[gid]["how"] = f"story {dict(bag)} (was {old}; {groups[gid].get('how', '')})"
        report.append(dict(group=gid, old=old, new=new, why=f"{n} lines agree", lines=used[gid]))
    new_names = list(mapping.values())
    if len(new_names) != len(set(new_names)):
        raise RuntimeError(f"two faces would get the same name: {mapping}")
    if mapping:
        looks = {c["name"]: c.get("look", "") for c in cast}
        for c in cast:
            if c["name"] in mapping.values():
                src = next(old for old, new in mapping.items() if new == c["name"])
                c["look"] = looks.get(src, c.get("look", ""))
        def ren(n):
            if n in mapping:
                return mapping[n]
            for old, new in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
                if n.startswith(old + " ") or n.startswith(old + "("):
                    return new + n[len(old):]
            return n
        for r in library.get("shots") or []:
            r["people"] = [ren(p) for p in (r.get("people") or [])]
        for shot in (face.get("shots") or {}).values():
            for p in shot.get("persons") or []:
                if p.get("name") in mapping:
                    p["name"] = mapping[p["name"]]
            for t in shot.get("talking") or []:
                if t.get("name") in mapping:
                    t["name"] = mapping[t["name"]]
    return report


def sentences_beside(library_path):
    """<work>/sentences_src.json when the library is <work>/broll_library/broll_library.json."""
    p = Path(library_path).resolve().parents[1] / "sentences_src.json"
    return p if p.is_file() else None


def apply_files(library_path, face_path, sentences_path=None):
    """Load the three files, rename, and write the library and the face map back."""
    lib = json.load(open(library_path))
    face = json.load(open(face_path))
    sent_path = Path(sentences_path) if sentences_path else sentences_beside(library_path)
    if not sent_path or not Path(sent_path).is_file():
        print("story names: no sentences_src.json, names stay as the vote")
        return []
    sentences = json.load(open(sent_path))["sentences"]
    report = apply_story_names(lib, face, sentences)
    for row in report:
        print(f"story names group {row['group']}: {row['old']} -> {row['new']} ({row['why']})")
        for n, old, who, text in row["lines"]:
            print(f"    shot {n}: face was {old}, line says {who}: {text}")
    if any(r["new"] not in (None, r["old"]) for r in report):
        json.dump(lib, open(library_path, "w"), indent=1, ensure_ascii=False)
        json.dump(face, open(face_path, "w"), indent=1, ensure_ascii=False)
        print("story names: wrote", library_path, "and", face_path)
    else:
        print("story names: no face renamed")
    return report
