"""CPU worker copies library.py and sceneedit.py to /opt/scene/pipeline.

parents[2] is then /opt, which has no dubstudio package. The package is at /opt/app.
The scripts also parse argv and read the work dir at import, so the test loads _app_dir
from each file instead of importing the module.
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = (
    BACKEND / "dubstudio" / "pipeline" / "library.py",
    BACKEND / "dubstudio" / "pipeline" / "sceneedit.py",
)


def _app_dir_from(path):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_app_dir")
    ns = {"Path": Path}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    return ns["_app_dir"]


def test_cpu_worker_script_uses_opt_app(tmp_path, monkeypatch):
    script = Path("/opt/scene/pipeline/library.py")
    parent = script.resolve().parents[2]
    assert parent == Path("/opt")

    # /opt/app is not created on the machine that runs the suite. The stub is the worker
    # layout: parents[2] (/opt) has no package, /opt/app has dubstudio/ai.py.
    real_is_file = Path.is_file
    opt_app_ai = Path("/opt/app/dubstudio/ai.py")
    opt_app_init = Path("/opt/app/dubstudio/__init__.py")
    missing = {parent / "dubstudio" / "ai.py", parent / "dubstudio" / "__init__.py"}

    def is_file(self):
        if self in missing:
            return False
        if self == opt_app_ai:
            return True
        if self == opt_app_init:
            return False
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    for src in SCRIPTS:
        assert _app_dir_from(src)(script) == Path("/opt/app")
        assert _app_dir_from(src)(Path("/opt/scene/pipeline/sceneedit.py")) == Path("/opt/app")

    # parents[2] is tried first: a checkout that already contains the package wins,
    # including when the only marker is dubstudio/__init__.py and /opt/app also has ai.py.
    root = tmp_path / "backend"
    (root / "dubstudio").mkdir(parents=True)
    (root / "dubstudio" / "__init__.py").write_text("")
    local = root / "dubstudio" / "pipeline" / "library.py"
    local.parent.mkdir(parents=True)
    for src in SCRIPTS:
        assert _app_dir_from(src)(local) == local.resolve().parents[2]
