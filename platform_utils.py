from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _hidden_subprocess_kwargs() -> dict:
    if not IS_WINDOWS:
        return {}
    kwargs: dict = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    kwargs["startupinfo"] = startupinfo
    return kwargs


def ui_font_family() -> str:
    if IS_WINDOWS:
        return "Segoe UI"
    return "Sans Serif"


def gpu_busy_percent() -> float:
    if IS_WINDOWS or IS_LINUX:
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                **_hidden_subprocess_kwargs(),
            ).stdout
            values = [
                float(line.strip())
                for line in out.splitlines()
                if line.strip()
            ]
            if values:
                return max(0.0, min(100.0, values[0]))
        except Exception:
            pass
        return 0.0

    if not IS_MACOS:
        return 0.0

    try:
        out = subprocess.run(
            ["ioreg", "-r", "-c", "IOAccelerator"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            **_hidden_subprocess_kwargs(),
        ).stdout
        import re

        matches = re.findall(r'"IOGPUBusyPercent"\s*=\s*(\d+)', out)
        if matches:
            return float(matches[-1])
        matches = re.findall(r'"GPU Activity\(%\)"\s*=\s*(\d+)', out)
        if matches:
            return float(matches[-1])
    except Exception:
        pass
    return 0.0


def open_path_in_file_manager(path: str) -> bool:
    if not path:
        return False
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if IS_MACOS:
            subprocess.Popen(["open", path], **_hidden_subprocess_kwargs())
            return True
        if IS_LINUX:
            subprocess.Popen(["xdg-open", path], **_hidden_subprocess_kwargs())
            return True
    except Exception:
        return False
    return False


def active_runtime_env() -> str | None:
    """Best-effort name for the currently active Python environment."""
    for value in (
        os.environ.get("CONDA_DEFAULT_ENV"),
        os.environ.get("MAMBA_DEFAULT_ENV"),
        os.environ.get("VIRTUAL_ENV"),
    ):
        if value:
            return Path(value).name

    try:
        env_dir = Path(sys.executable).resolve().parent
        if os.name != "nt" and env_dir.name == "bin":
            env_dir = env_dir.parent
        return env_dir.name
    except Exception:
        return None


def require_runtime_env(expected: str, label: str | None = None) -> None:
    """Abort early when a task is launched from the wrong Python environment."""
    if getattr(sys, "frozen", False):
        return

    active = active_runtime_env()
    if active and active.lower() == expected.lower():
        return

    where = label or "This app"
    active_text = active or "unknown"
    raise SystemExit(
        f"{where} must run in the '{expected}' environment. "
        f"Current environment: '{active_text}'. "
        f"Interpreter: {sys.executable}"
    )
