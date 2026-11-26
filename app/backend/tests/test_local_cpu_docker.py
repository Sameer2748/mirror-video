"""A local Docker CPU stands in for a rented worker: unpack the work tar, run the chain driver.

No RunPod. Colima/Rancher Docker only. Skips when the engine is not up.
"""
import json, subprocess, sys, tarfile
from pathlib import Path

import pytest

from dubstudio import cloud


def _docker_ok():
    try:
        p = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


need_docker = pytest.mark.skipif(not _docker_ok(), reason="local Docker CPU (colima) is not running")


def _tiny_chain_tar(work, python="python3"):
    """Same driver the rented CPU runs. Steps only prove scripts from the tar are openable."""
    cloud.stage_sharpness_tool(work)
    cloud.stage_translate_fallback(work)
    (work / "_cloud_chain.json").write_text(json.dumps({
        "steps": [
            {"name": "sharpness", "cmd": [
                python, "/work/_app/dubstudio/sharpness_tool.py", "--help",
            ]},
            {"name": "translate_wrapper", "cmd": [
                python, "-c",
                "import pathlib; p=pathlib.Path('/work/_app/dubstudio/pipeline/translate_visit.py'); "
                "assert p.is_file(), p",
            ]},
        ],
        "place": [],
    }), encoding="utf-8")
    (work / "_cloud_chain.py").write_text(cloud.CHAIN_DRIVER, encoding="utf-8")
    tar = work.parent / "work_in.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        for p in work.rglob("*"):
            if p.is_file() and p.stat().st_size <= cloud.SMALL:
                t.add(p, arcname=str(p.relative_to(work)))
    return tar


def test_isolated_cpu_opens_staged_python_from_the_work_tar(tmp_path):
    """Unpack the work tar and run the same chain driver a rented CPU runs."""
    src = tmp_path / "pack"
    src.mkdir()
    tar = _tiny_chain_tar(src, python=sys.executable)
    cpu = tmp_path / "cpu"
    cpu.mkdir()
    with tarfile.open(tar) as t:
        t.extractall(cpu)
    tool = cpu / "_app" / "dubstudio" / "sharpness_tool.py"
    wrapper = cpu / "_app" / "dubstudio" / "pipeline" / "translate_visit.py"
    assert tool.is_file() and wrapper.is_file()
    (cpu / "_cloud_chain.json").write_text(json.dumps({
        "steps": [
            {"name": "sharpness", "cmd": [sys.executable, str(tool), "--help"]},
            {"name": "translate_wrapper", "cmd": [sys.executable, "-c",
                                                  f"import pathlib; assert pathlib.Path({str(wrapper)!r}).is_file()"]},
        ],
        "place": [],
    }), encoding="utf-8")
    (cpu / "_cloud_chain.py").write_text(cloud.CHAIN_DRIVER, encoding="utf-8")
    p = subprocess.run([sys.executable, str(cpu / "_cloud_chain.py")], cwd=str(cpu),
                       capture_output=True, text=True, timeout=30)
    log = p.stdout + p.stderr
    assert p.returncode == 0, log
    assert "CHAIN OK" in log
    assert "No such file" not in log


@need_docker
def test_docker_cpu_opens_staged_python_from_the_work_tar(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    tar = _tiny_chain_tar(work)
    out = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{tar}:/in.tar.gz:ro",
        "python:3.12-slim",
        "bash", "-lc",
        "mkdir -p /work && tar -xzf /in.tar.gz -C /work && python3 /work/_cloud_chain.py",
    ], capture_output=True, text=True, timeout=180)
    log = out.stdout + out.stderr
    assert out.returncode == 0, log
    assert "=== sharpness ===" in log
    assert "CHAIN OK" in log
    assert "No such file" not in log
    assert str(tmp_path) not in log
