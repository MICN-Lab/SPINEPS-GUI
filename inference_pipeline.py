import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import gzip
from datetime import datetime
from pathlib import Path

from dicom2nifti import convert_directory
import pydicom

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

TASK_NAME = "Task005_SpineSubregions"
TASK_ID = "5"
DERIVATIVE_NAME = "derivatives_seg"

def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_root = Path(sys.executable).resolve().parent
        roots.extend([exe_root, exe_root / "_internal"])
        roots.extend(exe_root.parents)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
    roots.extend([Path(__file__).resolve().parent, Path.cwd()])

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _resolve_trainer_dir() -> Path:
    for base in _candidate_roots():
        candidate = base / "Spine" / "code" / "nnUNetTrainerV2__nnUNetPlansv2.1"
        if candidate.is_dir():
            return candidate
    # keep previous default for clearer error messages downstream
    return ROOT / "Spine" / "code" / "nnUNetTrainerV2__nnUNetPlansv2.1"


TRAINER_DIR = _resolve_trainer_dir()

DEFAULT_COMPUTE_THREADS = "1"
DEFAULT_NNUNET_PREPROCESS_THREADS = "1"
DEFAULT_NNUNET_SAVE_THREADS = "1"
DEFAULT_NNUNET_STEP_SIZE = "1.0"

_THREAD_LIMIT_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

_MACOS_ALLOCATOR_DEBUG_VARS = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
    "MallocScribble",
    "MallocPreScribble",
    "MallocGuardEdges",
    "MallocCheckHeapStart",
    "MallocCheckHeapEach",
    "MallocCheckHeapAbort",
    "MallocCheckHeapSleep",
)

_IGNORED_LOG_SUBSTRINGS = (
    "MallocStackLogging: can't turn off malloc stack logging because it was not enabled.",
)

_HEARTBEAT_INTERVAL_S = 5.0


class _ImmediateAsyncResult:
    def __init__(self, value):
        self._value = value

    def get(self, timeout=None):
        return self._value


class _SerialPool:
    """Minimal Pool-compatible shim for Windows-locked environments."""

    def __init__(self, processes=None, initializer=None, initargs=()):
        if initializer is not None:
            initializer(*initargs)

    def starmap_async(self, func, iterable):
        return _ImmediateAsyncResult([func(*args) for args in iterable])

    def close(self):
        return None

    def join(self):
        return None


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _apply_compute_limits(env: dict[str, str], log_fn=print) -> dict[str, str]:
    env = env.copy()
    applied = []
    for var in _THREAD_LIMIT_VARS:
        if not env.get(var):
            env[var] = DEFAULT_COMPUTE_THREADS
        applied.append(f"{var}={env[var]}")

    try:
        import torch

        threads = max(1, int(env["OMP_NUM_THREADS"]))
        torch.set_num_threads(threads)
        if hasattr(torch, "set_num_interop_threads"):
            torch.set_num_interop_threads(max(1, min(threads, 2)))
    except Exception:
        pass

    # Drop macOS malloc-debug flags from child processes. When inherited into
    # multiprocessing workers they can emit repetitive warnings.
    for var in _MACOS_ALLOCATOR_DEBUG_VARS:
        env.pop(var, None)

    log_fn("Compute limits: " + ", ".join(applied))
    return env


def _ensure_dir_link(link_path: Path, target_path: Path) -> None:
    # On Windows, broken junctions/reparse points can report Path.exists() as
    # false even though the filesystem entry is still there. lexists catches
    # those so repeated runs do not fail with "file already exists" at mklink.
    if os.path.lexists(link_path) or link_path.exists() or link_path.is_symlink():
        try:
            if link_path.resolve() == target_path.resolve():
                return
        except Exception:
            pass

        # A previous fallback copy is also a valid model layout. Keep it if it
        # already contains the expected trainer files.
        if link_path.is_dir() and (link_path / "plans.pkl").exists():
            return

        if link_path.is_symlink() or link_path.is_file():
            link_path.unlink()
        else:
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(link_path)],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except Exception:
                    shutil.rmtree(link_path)
            else:
                shutil.rmtree(link_path)

    link_path.parent.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            return
        except Exception:
            pass

    try:
        link_path.symlink_to(target_path, target_is_directory=True)
        return
    except Exception:
        pass

    shutil.copytree(target_path, link_path, dirs_exist_ok=True)


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _collect_process_stats(pid: int) -> str:
    try:
        import psutil

        proc = psutil.Process(pid)
        rss_gb = proc.memory_info().rss / (1024 ** 3)
        cpu_pct = proc.cpu_percent(interval=None)
        children = proc.children(recursive=True)
        child_count = len(children)
        child_rss_gb = sum(child.memory_info().rss for child in children) / (1024 ** 3)
        total_rss_gb = rss_gb + child_rss_gb
        return (
            f"pid={pid}, cpu={cpu_pct:.1f}%, rss={rss_gb:.2f} GB, "
            f"tree_rss={total_rss_gb:.2f} GB, child_procs={child_count}"
        )
    except Exception:
        return f"pid={pid}"


def _collect_segmentation_stats(seg_dir: Path) -> str:
    nii_files = sorted(seg_dir.glob("*.nii.gz"))
    if nii_files:
        newest = max(nii_files, key=lambda path: path.stat().st_mtime)
        size_mb = newest.stat().st_size / (1024 ** 2)
        return f"segmentations={len(nii_files)}, latest={newest.name} ({size_mb:.1f} MB)"
    plans = sorted(seg_dir.glob("*"))
    if plans:
        newest = max(plans, key=lambda path: path.stat().st_mtime)
        return f"seg_dir_items={len(plans)}, latest={newest.name}"
    return "segmentation output pending"


def _is_nifti_file(path: Path) -> bool:
    return path.is_file() and (path.name.endswith(".nii.gz") or path.suffix == ".nii")


def _discover_nifti_files(path: Path) -> list[Path]:
    if _is_nifti_file(path):
        return [path]
    if not path.is_dir():
        return []

    ignored_dir_names = {
        "nifti_tmp",
        "nnunet_input",
        "nnunet_preprocessed",
        "nnunet_raw",
        "nnunet_results",
        "segmentation",
        "output",
        "dist",
        "build",
    }

    def _valid_file(p: Path) -> bool:
        if not p.is_file() or p.name.startswith("._"):
            return False
        parts_lower = {part.lower() for part in p.parts}
        return not any(name in parts_lower for name in ignored_dir_names)

    nii_gz = sorted(p for p in path.rglob("*.nii.gz") if _valid_file(p))
    nii = sorted(
        p for p in path.rglob("*.nii")
        if _valid_file(p) and not p.name.endswith(".nii.gz")
    )
    return nii_gz + nii


def _is_ignored_input_path(path: Path) -> bool:
    ignored_dir_names = {
        "nifti_tmp",
        "nnunet_input",
        "nnunet_preprocessed",
        "nnunet_raw",
        "nnunet_results",
        "segmentation",
        "output",
        "dist",
        "build",
        "dicom_series_tmp",
    }
    parts_lower = {part.lower() for part in path.parts}
    return any(name in parts_lower for name in ignored_dir_names)


def _safe_stage_name(text: str, index: int) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip()
    cleaned = "_".join(cleaned.split())
    if not cleaned:
        cleaned = "series"
    return f"{index:03d}_{cleaned[:80]}"


def _read_dicom_series(input_path: Path) -> list[dict[str, object]]:
    if input_path.is_file():
        candidates = [input_path]
    else:
        candidates = sorted(p for p in input_path.rglob("*") if p.is_file())

    series_by_key: dict[str, dict[str, object]] = {}
    for path in candidates:
        if path.name.startswith("._") or _is_ignored_input_path(path):
            continue
        try:
            ds = pydicom.dcmread(
                str(path),
                stop_before_pixels=True,
                force=True,
                specific_tags=[
                    "SeriesInstanceUID",
                    "StudyInstanceUID",
                    "SeriesNumber",
                    "SeriesDescription",
                    "ProtocolName",
                    "Modality",
                    "ImageType",
                    "Rows",
                    "Columns",
                    "InstanceNumber",
                    "SOPInstanceUID",
                ],
            )
        except Exception:
            continue

        rows = int(getattr(ds, "Rows", 0) or 0)
        cols = int(getattr(ds, "Columns", 0) or 0)
        if rows <= 0 or cols <= 0:
            continue

        series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "").strip()
        if not series_uid:
            fallback = [
                str(getattr(ds, "StudyInstanceUID", "") or "").strip(),
                str(getattr(ds, "SeriesNumber", "") or "").strip(),
                str(getattr(ds, "SeriesDescription", "") or "").strip(),
                str(path.parent),
            ]
            series_uid = "|".join(bit for bit in fallback if bit)

        entry = series_by_key.setdefault(
            series_uid,
            {
                "key": series_uid,
                "files": [],
                "description": str(getattr(ds, "SeriesDescription", "") or "").strip(),
                "protocol": str(getattr(ds, "ProtocolName", "") or "").strip(),
                "modality": str(getattr(ds, "Modality", "") or "").strip(),
                "image_type": "\\".join(str(bit) for bit in getattr(ds, "ImageType", [])).upper(),
                "rows": rows,
                "cols": cols,
            },
        )
        entry["files"].append(path)

    series = []
    for entry in series_by_key.values():
        files = sorted(entry["files"], key=lambda p: str(p).lower())
        if files:
            entry["files"] = files
            series.append(entry)

    return sorted(
        series,
        key=lambda item: (
            str(item.get("modality", "")),
            str(item.get("description", "")),
            str(item.get("protocol", "")),
            str(item.get("key", "")),
        ),
    )


def _is_convertible_dicom_series(item: dict[str, object], min_slices: int = 3) -> tuple[bool, str]:
    files = item.get("files") or []
    if len(files) < min_slices:
        return False, f"fewer than {min_slices} slices"

    text = " ".join(
        str(item.get(key, "") or "").upper()
        for key in ("description", "protocol", "image_type")
    )
    localizer_markers = (
        "LOCALIZER",
        "TOPGRAM",
        "TOPOGRAM",
        "SCOUT",
        "PATIENT PROTOCOL",
        "DOSE",
        "REPORT",
    )
    for marker in localizer_markers:
        if marker in text:
            return False, f"localizer/non-volume marker: {marker}"

    return True, ""


def _stage_dicom_series(series: list[dict[str, object]], stage_root: Path, emit) -> list[Path]:
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    staged_dirs: list[Path] = []
    max_slices = max((len(item.get("files") or []) for item in series), default=0)
    min_slices = 3
    if max_slices >= 20:
        min_slices = max(3, int(max_slices * 0.5))

    for index, item in enumerate(series, start=1):
        files = item.get("files") or []
        should_convert, reason = _is_convertible_dicom_series(item, min_slices=min_slices)
        if not should_convert:
            label = (
                str(item.get("description", "") or "").strip()
                or str(item.get("protocol", "") or "").strip()
                or str(item.get("key", "series"))
            )
            emit(f"    skipped DICOM series: {label} ({len(files)} file(s), {reason})")
            continue
        title_bits = [
            str(item.get("modality", "") or "").strip(),
            str(item.get("description", "") or "").strip(),
            str(item.get("protocol", "") or "").strip(),
        ]
        title = "_".join(bit for bit in title_bits if bit) or str(item.get("key", "series"))
        stage_dir = stage_root / _safe_stage_name(title, index)
        stage_dir.mkdir(parents=True, exist_ok=True)
        for file_index, src in enumerate(files, start=1):
            suffix = src.suffix if src.suffix else ".dcm"
            dst = stage_dir / f"{file_index:06d}{suffix}"
            try:
                os.link(src, dst)
            except Exception:
                shutil.copy2(src, dst)
        emit(
            "    staged series: "
            f"{stage_dir.name} ({len(files)} file(s), {item.get('rows')}x{item.get('cols')})"
        )
        staged_dirs.append(stage_dir)
    return staged_dirs


def _convert_dicom_series(input_path: Path, output_dir: Path, emit) -> list[Path]:
    series = _read_dicom_series(input_path)
    if not series:
        raise RuntimeError(f"No readable DICOM image series found in: {input_path}")

    emit(f"  Readable DICOM image series: {len(series)}")
    stage_dirs = _stage_dicom_series(series, output_dir / "dicom_series_tmp", emit)
    if not stage_dirs:
        raise RuntimeError(f"No non-empty DICOM image series found in: {input_path}")

    before = {p.resolve() for p in output_dir.glob("*.nii.gz") if p.is_file()}
    failures: list[str] = []
    for stage_dir in stage_dirs:
        try:
            emit(f"  DICOM conversion: {stage_dir.name}")
            convert_directory(str(stage_dir), str(output_dir), compression=True, reorient=True)
        except Exception as exc:
            failures.append(f"{stage_dir.name}: {exc}")
            emit(f"    skipped series after conversion error: {exc}")

    nii_files = sorted(
        p for p in output_dir.glob("*.nii.gz")
        if p.is_file() and not p.name.startswith("._")
    )
    new_files = [p for p in nii_files if p.resolve() not in before]
    if not new_files and failures:
        joined = "; ".join(failures[:5])
        if len(failures) > 5:
            joined += f"; ... {len(failures) - 5} more"
        raise RuntimeError(f"DICOM conversion failed for all readable series: {joined}")
    return nii_files


def _copy_or_compress_nifti(src: Path, dst_dir: Path) -> Path:
    if src.name.endswith(".nii.gz"):
        dst = dst_dir / src.name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            return dst
        shutil.copy2(src, dst)
        return dst

    dst = dst_dir / f"{src.stem}.nii.gz"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    with src.open("rb") as fsrc, gzip.open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst)
    return dst


def _prepare_nifti_inputs(input_path: Path, output_dir: Path, emit) -> list[Path]:
    nifti_tmp = output_dir / "nifti_tmp"
    nifti_tmp.mkdir(parents=True, exist_ok=True)

    direct_nifti = _discover_nifti_files(input_path)
    if direct_nifti:
        emit("Step 1: Preparing NIfTI input...")
        emit(f"  NIfTI temp dir: {nifti_tmp}")
        emit(f"  Direct NIfTI input(s) discovered: {len(direct_nifti)}")
        nii_files = [_copy_or_compress_nifti(src, nifti_tmp) for src in direct_nifti]
    else:
        emit("Step 1: Converting DICOM to NIfTI...")
        emit(f"  NIfTI temp dir: {nifti_tmp}")
        nii_files = _convert_dicom_series(input_path, nifti_tmp, emit)

    emit(f"  {len(nii_files)} NIfTI file(s) ready.")
    for path in nii_files[:5]:
        emit(f"    nifti: {path.name}")
    if len(nii_files) > 5:
        emit(f"    ... {len(nii_files) - 5} more NIfTI file(s)")
    return nii_files


_SPINEPS_MODEL_CACHE = None


def _model_key(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default).strip().lower()


def _ensure_spineps_import_path() -> None:
    try:
        import spineps  # noqa: F401
        return
    except ImportError:
        pass

    for base in _candidate_roots():
        for candidate in (base / "spineps", base.parent / "spineps"):
            if (candidate / "spineps" / "__init__.py").is_file():
                sys.path.insert(0, str(candidate))
                return


def _load_spineps_models(use_cpu: bool, emit):
    global _SPINEPS_MODEL_CACHE
    cache_key = (
        _model_key("SPINEPS_GUI_SEMANTIC_MODEL", "ct"),
        _model_key("SPINEPS_GUI_INSTANCE_MODEL", "ct_instance"),
        _model_key("SPINEPS_GUI_LABELING_MODEL", "ct_labeling"),
        use_cpu,
    )
    if _SPINEPS_MODEL_CACHE is not None and _SPINEPS_MODEL_CACHE[0] == cache_key:
        emit("Step 3: Reusing loaded spineps models.")
        return _SPINEPS_MODEL_CACHE[1]

    emit("Step 3: Loading spineps models...")
    emit(f"  Semantic model: {cache_key[0]}")
    emit(f"  Instance model: {cache_key[1]}")
    emit(f"  Labeling model: {cache_key[2]}")

    _ensure_spineps_import_path()
    from spineps import get_instance_model, get_labeling_model, get_semantic_model

    semantic = get_semantic_model(cache_key[0], use_cpu=use_cpu).load()
    instance = get_instance_model(cache_key[1], use_cpu=use_cpu).load()
    labeling = None
    if cache_key[2] not in {"", "none", "off", "false"}:
        labeling = get_labeling_model(cache_key[2], use_cpu=use_cpu).load()

    models = (semantic, instance, labeling)
    _SPINEPS_MODEL_CACHE = (cache_key, models)
    return models


def _strip_nifti_suffix(path: Path) -> str:
    if path.name.endswith(".nii.gz"):
        return path.name[:-7]
    return path.stem


def _copy_for_gui_overlay(src: Path, input_nii: Path, seg_dir: Path, emit) -> Path:
    seg_dir.mkdir(parents=True, exist_ok=True)
    dst = seg_dir / f"{_strip_nifti_suffix(input_nii)}.nii.gz"
    if src.exists():
        shutil.copy2(src, dst)
        emit(f"  GUI overlay: {dst.name}")
        return dst
    raise RuntimeError(f"spineps did not create expected segmentation: {src}")


def _run_spineps_sample(
    nii_file: Path,
    output_dir: Path,
    models,
    emit,
) -> Path:
    _ensure_spineps_import_path()
    from TPTBox import BIDS_FILE
    try:
        from spineps import process_img_nii
    except ImportError:  # SPINEPS >= 2.0 renamed it
        from spineps import segment_image as process_img_nii

    semantic, instance, labeling = models
    emit(f"Step 4: Running spineps on {nii_file.name}...")
    bids_sample = BIDS_FILE(str(nii_file), dataset=str(output_dir), verbose=False)
    output_paths, errcode = process_img_nii(
        img_ref=bids_sample,
        model_semantic=semantic,
        model_instance=instance,
        model_labeling=labeling,
        derivative_name=DERIVATIVE_NAME,
        override_semantic=False,
        override_instance=False,
        override_postpair=False,
        override_ctd=False,
        proc_sem_n4_bias_correction=False,
        ignore_bids_filter=True,
        ignore_compatibility_issues=True,
        verbose=False,
    )
    emit(f"  spineps result: {errcode}")
    return _copy_for_gui_overlay(Path(output_paths["out_spine"]), nii_file, output_dir / "segmentation", emit)


def run_pipeline(input_path: str, output_dir: str, log_fn=print) -> None:
    def emit(message: str) -> None:
        log_fn(f"[{_timestamp()}] {message}")

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    emit("Pipeline start")
    emit(f"  Input path: {input_path}")
    emit(f"  Output dir: {output_dir}")

    if input_path.is_dir():
        nifti_files = _discover_nifti_files(input_path)
        if nifti_files:
            emit(f"  Input mode: NIfTI directory ({len(nifti_files)} file(s))")
        else:
            dicom_files = sorted(p for p in input_path.rglob("*") if p.is_file())
            leaf_dirs = sorted({p.parent for p in dicom_files})
            emit(f"  Input mode: DICOM directory")
            emit(f"  DICOM files discovered: {len(dicom_files)}")
            emit(f"  Candidate series folders: {len(leaf_dirs)}")
            for folder in leaf_dirs[:5]:
                emit(f"    series: {folder}")
            if len(leaf_dirs) > 5:
                emit(f"    ... {len(leaf_dirs) - 5} more series folder(s)")
    elif _is_nifti_file(input_path):
        emit("  Input mode: NIfTI file")
    else:
        raise RuntimeError(f"Unsupported input path: {input_path}")

    base_env = _apply_compute_limits(os.environ, emit)

    nii_files = _prepare_nifti_inputs(input_path, output_dir, emit)
    if not nii_files:
        raise RuntimeError("No NIfTI files were found or produced from the selected input")

    seg_dir = output_dir / "segmentation"
    seg_dir.mkdir(parents=True, exist_ok=True)
    emit(f"  Segmentation dir: {seg_dir}")

    expected_outputs = [seg_dir / f"{_strip_nifti_suffix(path)}.nii.gz" for path in nii_files]
    if expected_outputs and all(path.exists() for path in expected_outputs):
        emit(f"Step 2: Using cached segmentation ({len(expected_outputs)} file(s)).")
        return

    use_cpu = os.environ.get("SPINEPS_GUI_CPU", "").strip().lower() in {"1", "true", "yes", "on"}
    models = _load_spineps_models(use_cpu=use_cpu, emit=emit)

    completed = []
    for idx, nii_file in enumerate(nii_files, start=1):
        expected = seg_dir / f"{_strip_nifti_suffix(nii_file)}.nii.gz"
        if expected.exists():
            emit(f"Step 4: Using cached segmentation ({idx}/{len(nii_files)}): {expected.name}")
            completed.append(expected)
            continue
        completed.append(_run_spineps_sample(nii_file, output_dir, models, emit))

    emit(f"Done. {len(completed)} segmentation(s) saved to: {seg_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    run_pipeline(args.input_path, args.output_dir)


if __name__ == "__main__":
    main()
