"""Google first; Claude fills leftover sentences. Runs on the matching CPU so HTTP 429 is not a visit fail."""
import subprocess, sys
from pathlib import Path


def _arg(cmd, key):
    return cmd[cmd.index(key) + 1] if key in cmd else None


def main(argv=None):
    cmd = list(argv if argv is not None else sys.argv[1:])
    if not cmd:
        print("translate_visit: missing dubstitch command", flush=True)
        return 2
    p = subprocess.run(cmd)
    if p.returncode == 0:
        return 0
    print("Google translate failed; filling leftover sentences with Claude", flush=True)
    root = Path(__file__).resolve().parents[2]          # …/_app
    sys.path.insert(0, str(root))
    from dubstudio.pipeline import translate_ai
    work = Path(_arg(cmd, "--work") or "/work")
    todo, filled = work / "translate_todo.tsv", work / "translate_ai.tsv"
    r1 = subprocess.run(cmd + ["--export-todo", str(todo)])
    if r1.returncode != 0:
        print("export-todo failed", flush=True)
        return r1.returncode
    n = translate_ai.fill_todo(todo, filled, _arg(cmd, "--sl") or "de", _arg(cmd, "--tl") or "en", log=print)
    r2 = subprocess.run(cmd + ["--engine", "file", "--import", str(filled)])
    if r2.returncode != 0:
        print("import of Claude translations failed", flush=True)
        return r2.returncode
    print(f"translated {n} sentences with Claude after Google failed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
