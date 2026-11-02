"""Compare two face_map.json files (Mac vs cloud). Names and talking windows, not bytes.
Usage: python compare_face_map.py <mac.json> <other.json>
"""
import json, sys
from collections import Counter

A = json.load(open(sys.argv[1]))
B = json.load(open(sys.argv[2]))


def names(shot):
    return sorted({(p.get("name") or "").strip() for p in shot.get("persons") or [] if p.get("name")})


def talk(shot):
    out = []
    for t in shot.get("talking") or []:
        out.append((round(float(t.get("from_s", 0)), 1), round(float(t.get("to_s", 0)), 1),
                    (t.get("name") or "").strip()))
    return out


sa, sb = A.get("shots") or {}, B.get("shots") or {}
keys = sorted(set(sa) | set(sb), key=lambda x: int(x) if str(x).isdigit() else str(x))
same_people = miss = extra = 0
talk_same = talk_diff = 0
examples = []
for k in keys:
    if k not in sa or k not in sb:
        examples.append(f"#{k}: only in {'mac' if k in sa else 'other'}")
        continue
    na, nb = set(names(sa[k])), set(names(sb[k]))
    if na == nb:
        same_people += 1
    else:
        miss += len(na - nb)
        extra += len(nb - na)
        if len(examples) < 12:
            examples.append(f"#{k}: mac {sorted(na)} other {sorted(nb)}")
    if talk(sa[k]) == talk(sb[k]):
        talk_same += 1
    else:
        talk_diff += 1

print(f"shots mac {len(sa)} other {len(sb)} compared {len(keys)}")
print(f"people exact {same_people}/{len(keys)}  mac-only names {miss}  other-only names {extra}")
print(f"talking exact {talk_same}/{len(keys)}  different {talk_diff}")
print("groups mac", {g: v.get("name") for g, v in (A.get("groups") or {}).items()})
print("groups other", {g: v.get("name") for g, v in (B.get("groups") or {}).items()})
print("timing mac", A.get("timing"), A.get("seconds"), "other", B.get("timing"), B.get("seconds"))
if examples:
    print("examples:")
    for e in examples:
        print(" ", e)
