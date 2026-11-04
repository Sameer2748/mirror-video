"""Paths and settings. Order: environment variables > <project>/config.json > detected defaults."""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the dub-studio folder
HOME = Path.home()


def _load_file():
    p = ROOT / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _first_existing(cands):
    for c in cands:
        if c and Path(c).expanduser().exists():
            return Path(c).expanduser()
    return None


class Config:
    def __init__(self):
        f = _load_file()
        env = os.environ.get
        self.library = Path(env("DUBSTUDIO_LIBRARY") or f.get("library") or ROOT / "library").expanduser()
        self.tools_dir = Path(env("DUBSTUDIO_TOOLS") or f.get("tools_dir") or _first_existing([
            HOME / "Desktop/france-team/adaptation-tool", ROOT.parent / "adaptation-tool", HOME / "mnt/adaptation-tool"])
            or HOME / "Desktop/france-team/adaptation-tool").expanduser()
        # Render has no Mac Desktop folder. Scripts that must run on this box (groups, reading view, lip merge)
        # live in app/backend/pipeline_tools and ship in the API image.
        bundled = Path(__file__).resolve().parents[1] / "pipeline_tools"
        self._scripts = Path(env("DUBSTUDIO_SCRIPTS") or "").expanduser() if env("DUBSTUDIO_SCRIPTS") else _first_existing([
            self.tools_dir / "scripts" if (self.tools_dir / "scripts" / "dubstitch.py").is_file() else None,
            bundled if (bundled / "dubstitch.py").is_file() else None,
            self.tools_dir / "scripts",
        ]) or (self.tools_dir / "scripts")
        # the app's own pipeline environment (scripts/setup_pipeline.sh) first: the tools' .venv on the Mac is empty
        own_py, venv_py = ROOT / "pipeline_env/bin/python", self.tools_dir / ".venv/bin/python"
        self.pipeline_python = env("DUBSTUDIO_PIPELINE_PY") or f.get("pipeline_python") or (
            str(own_py) if own_py.exists() else str(venv_py) if venv_py.exists() and sys.platform == "darwin"
            else sys.executable)
        # the face map's own environment (insightface, onnxruntime, ultralytics): NEVER the pipeline / lip-scan env -- those
        # packages upgrade numpy and break mediapipe (scripts/setup_face_env.sh makes it)
        self.face_python = env("DUBSTUDIO_FACE_PY") or f.get("face_python") or str(
            self.tools_dir / "mac_lipscan/venv_face/bin/python")
        # the scene-edit scripts (library.py, sceneedit.py, scene_transitions.py, sceneedit_sheet.py): the app's own copies
        self.scene_dir = Path(env("DUBSTUDIO_SCENE_DIR") or f.get("scene_dir") or Path(__file__).resolve().parent / "pipeline")
        self.models_dir = Path(env("DUBSTUDIO_MODELS") or f.get("models_dir") or self.tools_dir / "data/models").expanduser()
        self.port = int(env("DUBSTUDIO_PORT") or f.get("port") or 8765)
        self.cpu_slots = int(env("DUBSTUDIO_CPU_SLOTS") or f.get("cpu_slots") or 2)
        self.gpu_slots = int(f.get("gpu_slots") or 1)
        self.step_max_seconds = float(env("DUBSTUDIO_STEP_MAX_SECONDS") or f.get("step_max_seconds") or 0)   # 0 = run to the end
        # subtitle font (Poppins Bold, the house style); the pipeline only looks in Linux font folders by itself
        self.font = env("DUBSTUDIO_FONT") or f.get("font") or next((str(x) for x in (
            ROOT / "assets/fonts/Poppins-Bold.ttf", HOME / "Library/Fonts/Poppins-Bold.ttf",
            Path("/Library/Fonts/Poppins-Bold.ttf")) if x.exists()), "")
        self.ffprobe = f.get("ffprobe") or "ffprobe"
        self.ffmpeg = f.get("ffmpeg") or "ffmpeg"

    @property
    def scripts(self):
        return self._scripts

    @property
    def db_path(self):
        return self.library / "dubstudio.db"

    def work_dir(self, show_code, number):
        return self.library / "work" / f"{show_code}_ep{number}"

    def as_dict(self):
        return {k: str(v) for k, v in self.__dict__.items()}


cfg = Config()


def reload():
    global cfg
    cfg = Config()
    return cfg
