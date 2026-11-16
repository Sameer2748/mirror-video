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


def _one_face(persons, cast_names=None):
    """The single face/profile person of a shot, or None when the shot shows none or several.

    -> (name, group id or None). A shot with two faces says nothing about which face the line is about."""
    seen = []
    for p in persons or []:
        if p.get("view") not in ("face", "profile"):
            continue
        key = (p.get("name"), p.get("g"))
        if key not in seen:
            seen.append(key)
    return seen[0] if len(seen) == 1 else None


def _library_votes(library, face, groups, cast_names):
    """Which library name each face group carries: the shots the library names with ONE person, in which the
    group is the only face. The same vote facemap.py takes; repeated here so a group that Claude named by
    its reference pictures still has the name the library gave its shots."""
    votes = defaultdict(lambda: defaultdict(int))
    for r in library.get("shots") or []:
        people = r.get("people") or []
        if len(people) != 1 or people[0] not in cast_names:
            continue
        one = _one_face((face.get("shots") or {}).get(str(r["n"]), {}).get("persons"))
        if not one or one[1] is None or str(one[1]) not in groups:
            continue
        votes[str(one[1])][people[0]] += 1
    out = {}
    for gid, bag in votes.items():
        tot = sum(bag.values())
        top = max(bag, key=bag.get)
        if tot >= 3 and bag[top] >= 0.6 * tot:
            out[gid] = top
    return out


def _name_keys(mapping):
    """Words to swap in a description: the full name, and the first name when the library used the first name alone."""
    keys = {}
    for old, new in mapping.items():
        if len(old.split()) > 1:
            keys[old] = new
            if old.split()[0] not in ("Mr", "Mrs", "Ms", "Officer"):
                keys[old.split()[0]] = new.split()[0]
        else:
            keys[old] = new.split()[0] if len(new.split()) > 1 else new
    return keys


def _swap_text(text, keys):
    """Replace every listed name in one pass, so Jake -> Frankie and Frankie -> Jake do not undo each other."""
    if not keys:
        return text
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True)) + r")\b")
    return pat.sub(lambda m: keys[m.group(1)], text)


def apply_story_names(library, face, sentences):
    """Rename face groups from agreed story lines. Mutates `library` and `face`. Returns a report.

    A line votes for the face GROUP seen alone in its shot (the group id is written on each person by
    facemap.py). Names are never used to find the group: one name can sit on several groups."""
    cast = (library.get("cast") or {}).get("cast") or []
    cast_names = {c["name"] for c in cast}
    shots = {r["n"]: r for r in library.get("shots") or []}
    groups = face.get("groups") or {}
    has_g = any(p.get("g") is not None for s in (face.get("shots") or {}).values() for p in s.get("persons") or [])
    if not has_g:
        return [dict(group=None, old=None, new=None, why="face map has no group ids on its people (older facemap.py)", lines=[])]
    lib_name = _library_votes(library, face, groups, cast_names)
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
            one = _one_face((face.get("shots") or {}).get(str(r["n"]), {}).get("persons"))
            if not one or one[1] is None or str(one[1]) not in groups:
                continue
            best = ov
            hit = (str(one[1]), one[0], r["n"])
        if not hit:
            continue
        gid, seen_as, n = hit
        votes[gid][who] += 1
        used[gid].append((n, seen_as, who, text[:80]))
    story = {}
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
        story[gid] = new
        report.append(dict(group=gid, old=old, new=new, why=f"{n} lines agree", lines=used[gid]))
    # A swap has two sides. The story names one side (Jake -> Frankie); the other face then holds the name
    # the first one gave up, so its library name moves the other way. Without this two people share a name.
    for gid, new in list(story.items()):
        lib_old = lib_name.get(gid) or groups[gid]["name"]
        if lib_old == new or new in {lib_name.get(g) or groups[g]["name"] for g in story}:
            continue
        for other in groups:
            if other in story or lib_name.get(other) != new:
                continue
            story[other] = lib_old
            report.append(dict(group=other, old=groups[other]["name"], new=lib_old,
                               why=f"swap: the library calls this face {new}, and {new} is the face the story calls {lib_old}",
                               lines=[]))
            used[other] = []
    # two groups must not get one story name
    taken = defaultdict(list)
    for gid, new in story.items():
        taken[new].append(gid)
    for new, gids in taken.items():
        if len(gids) > 1:
            raise RuntimeError(f"two faces would get the same name {new}: groups {gids}")
    # the name each group carries now: the library's vote, else what Claude said
    for gid in groups:
        if gid in lib_name and gid not in story and groups[gid].get("how", "").startswith("Claude"):
            groups[gid]["how"] = f"library vote {lib_name[gid]} (was {groups[gid]['name']}; {groups[gid]['how']})"
            groups[gid]["name"] = lib_name[gid]
    # library names: the name the library gave the group -> the story name
    mapping = {}
    for gid, new in story.items():
        lib_old = lib_name.get(gid) or groups[gid]["name"]
        if lib_old != new:
            mapping[lib_old] = new
    for gid, new in story.items():
        if groups[gid]["name"] != new:
            groups[gid]["how"] = f"story {{'{new}': {len(used[gid])}}} (was {groups[gid]['name']}; {groups[gid].get('how', '')})"
            groups[gid]["name"] = new
    if len(set(mapping.values())) != len(mapping.values()):
        raise RuntimeError(f"two library names would become one: {mapping}")
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
        keys = _name_keys(mapping)
        for r in library.get("shots") or []:
            before = list(r.get("people") or [])
            r["people"] = [ren(p) for p in before]
            if r["people"] != before:
                # the description was written with the old name ("Jake looks down" on the navy-suit man): swap it too,
                # in one pass so a swapped pair does not change twice
                for field in ("action", "why"):
                    if isinstance(r.get(field), str):
                        r[field] = _swap_text(r[field], keys)
    # the face map: every person of a renamed group takes the story name, on every shot
    if story:
        for shot in (face.get("shots") or {}).values():
            moved = {}
            for p in shot.get("persons") or []:
                gid = str(p.get("g")) if p.get("g") is not None else None
                if gid in story:
                    moved[p.get("name")] = story[gid]
                    p["name"] = story[gid]
            for t in shot.get("talking") or []:
                if t.get("name") in moved:
                    t["name"] = moved[t["name"]]
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
