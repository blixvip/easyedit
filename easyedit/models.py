"""The model files the optional tools use, fetched once into .cache/.

Every model here is under a permissive licence (MIT, Apache or BSD), like easyedit itself.
"""
from __future__ import annotations

import os
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

from .util import CACHE, log

# name -> (file, url, rough size in MB)
MODELS = {
    "vocals": ("Kim_Vocal_2.onnx",
               "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx", 67),
    "matte": ("isnet-general-use.onnx",
              "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx", 176),
    "matte-anime": ("isnet-anime.onnx",
                    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-anime.onnx", 176),
    "upscale": ("realesr-general-x4v3.pth",
                "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth", 5),
    "upscale-anime": ("realesr-animevideov3.pth",
                      "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth", 3),
    "frames": ("rife49.onnx",
               "https://huggingface.co/yuvraj108c/rife-onnx/resolve/main/rife49_ensemble_True_scale_1_sim.onnx", 22),
}
GPU_RUNTIME = CACHE / "ort-dml"


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def get(name: str, tool: str = "") -> Path:
    """The model's file, downloaded the first time it is asked for."""
    file, url, mb = MODELS[name]
    path = CACHE / file
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log(f"{tool or name}: downloading the model (one time, about {mb} MB)")
        tmp = path.with_suffix(path.suffix + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": "easyedit"})
        with urllib.request.urlopen(req, context=_context(), timeout=60) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.replace(path)
    return path


def gpu_runtime(tool: str = "gpu") -> Path | None:
    """A DirectML build of onnxruntime in .cache/ (Windows). About five times faster than CPU here."""
    if os.name != "nt":
        return None
    if not (GPU_RUNTIME / "onnxruntime").exists():
        log(f"{tool}: getting the GPU runtime (one time, about 70 MB)")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--target",
                            str(GPU_RUNTIME), "onnxruntime-directml"], check=True, timeout=900)
        except (subprocess.SubprocessError, OSError) as e:
            log(f"{tool}: GPU runtime not installed ({e}); using the CPU, which is slow")
            return None
    return GPU_RUNTIME


def gpu_env(tool: str = "gpu") -> dict:
    """The environment for a child process that should find the GPU build of onnxruntime first."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    runtime = gpu_runtime(tool)
    if runtime:
        env["PYTHONPATH"] = str(runtime) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def session(path: Path):
    """An onnxruntime session on the best provider this machine has."""
    import onnxruntime as ort
    have = ort.get_available_providers()
    providers = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in have] + ["CPUExecutionProvider"]
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=providers)
