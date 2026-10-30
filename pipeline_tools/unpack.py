# unpack.py -- rebuild every tool from the Appendix of SKILL.md (each file is checked against its md5)
import hashlib, os, re, sys
skill = sys.argv[1] if len(sys.argv) > 1 else "SKILL.md"
root = sys.argv[2] if len(sys.argv) > 2 else "adaptation-tool"
txt = open(skill, encoding="utf-8").read().replace("\r\n", "\n")
files = re.findall(r"<!-- FILE: (\S+) md5=([0-9a-f]{32}) -->\n~{6}[a-z]*\n(.*?)\n~{6}\n", txt, re.S)
bad = 0
for path, md5, body in files:
    data = (body + "\n").encode("utf-8"); out = os.path.join(root, path)
    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, "wb").write(data)
    ok = hashlib.md5(data).hexdigest() == md5; bad += not ok
    if out.endswith(".command"): os.chmod(out, 0o755)
    print(("ok        " if ok else "MD5 WRONG ") + out)
print(f"{len(files)} files written, {bad} with a wrong md5" + (" -- copy the skill file again, unchanged" if bad else ""))
