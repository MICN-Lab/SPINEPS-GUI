import sys
import glob
import os
import math
import re
import time
from pathlib import Path
import numpy as np
from spine_labels import label_map_for_values

# PyQt wheels may not ship Qt font directories in some envs; on Windows use
# system fonts to avoid repetitive QFontDatabase warnings.
if sys.platform.startswith("win") and not os.environ.get("QT_QPA_FONTDIR"):
    _win_font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if _win_font_dir.is_dir():
        os.environ["QT_QPA_FONTDIR"] = str(_win_font_dir)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextEdit, QLabel, QFileDialog,
    QSplitter, QSlider, QCheckBox, QSizePolicy, QComboBox,
    QFrame, QGridLayout, QGraphicsDropShadowEffect, QStackedLayout,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QUrl, QTimer, QRect, QPointF, QRectF
from PyQt5.QtGui import (QFont, QImage, QPixmap, QPainter, QColor,
                         QDesktopServices, QLinearGradient, QRadialGradient,
                         QBrush, QPen)

try:
    from platform_utils import (
        gpu_busy_percent,
        open_path_in_file_manager,
        require_runtime_env,
        ui_font_family,
    )
except Exception:
    import os as _os
    import subprocess as _subprocess
    import sys as _sys

    IS_WINDOWS = _sys.platform.startswith("win")
    IS_MACOS = _sys.platform == "darwin"
    IS_LINUX = _sys.platform.startswith("linux")

    def ui_font_family() -> str:
        return "Segoe UI" if IS_WINDOWS else "Sans Serif"

    def gpu_busy_percent() -> float:
        if not IS_MACOS:
            return 0.0
        try:
            out = _subprocess.run(
                ["ioreg", "-r", "-c", "IOAccelerator"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout
            import re as _re
            matches = _re.findall(r'"IOGPUBusyPercent"\s*=\s*(\d+)', out)
            if matches:
                return float(matches[-1])
            matches = _re.findall(r'"GPU Activity\(%\)"\s*=\s*(\d+)', out)
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
                _os.startfile(path)  # type: ignore[attr-defined]
                return True
            if IS_MACOS:
                _subprocess.Popen(["open", path])
                return True
            if IS_LINUX:
                _subprocess.Popen(["xdg-open", path])
                return True
        except Exception:
            return False
        return False

require_runtime_env("gui" if os.name == "nt" else "spine", "Spine GUI")

DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #11100e;
    color: #f2e8d9;
}
QLineEdit {
    background-color: #1a1712;
    color: #f6ead8;
    border: 1px solid rgba(212, 163, 93, 125);
    border-radius: 4px;
    padding: 4px;
}
QPushButton {
    background-color: #1c1812;
    color: #f4e8d7;
    border: 1px solid #D4A35D;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover {
    background-color: #2a2318;
    color: #fff7ea;
}
QPushButton:disabled {
    background-color: #15120e;
    color: #8f8577;
    border: 1px solid rgba(212, 163, 93, 60);
}
QPushButton#primaryRunButton {
    background-color: #D4A35D;
    background: #D4A35D;
    color: #1a1207;
    border: 2px solid #E9C58A;
    border-radius: 8px;
    padding: 11px 24px;
    font-size: 16px;
    font-weight: 700;
}
QPushButton#primaryRunButton:hover {
    background-color: #E3B56F;
    background: #E3B56F;
    border: 2px solid #F2D7A9;
}
QPushButton#primaryRunButton:disabled {
    background-color: #4f3c1f;
    background: #4f3c1f;
    color: #bfa37a;
    border: 2px solid rgba(233, 197, 138, 70);
}
QTextEdit {
    background-color: #15120e;
    color: #f2e8d9;
    border: 1px solid rgba(212, 163, 93, 80);
    border-radius: 4px;
}
QSplitter::handle {
    background-color: #4a3820;
}
QLabel {
    color: #f2e8d9;
}
QSlider::groove:vertical {
    background: #2c2418;
    width: 6px;
    border-radius: 3px;
}
QSlider::handle:vertical {
    background: #D4A35D;
    height: 14px;
    width: 14px;
    margin: 0 -4px;
    border-radius: 7px;
}
QCheckBox {
    color: #f2e8d9;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    background-color: #1a1712;
    border: 1px solid #D4A35D;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #D4A35D;
}
"""


def _smoke_test_enabled() -> bool:
    # Only allow smoke mode in explicit headless runs.
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
        return False
    return os.environ.get("SPINE_SMOKE_TEST") == "1" or os.environ.get("EDH_SMOKE_TEST") == "1"


def _stdout_log_enabled() -> bool:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
        return False
    return os.environ.get("SPINE_STDOUT_LOG") == "1" or os.environ.get("EDH_STDOUT_LOG") == "1"

LABEL_MAP = {
    1: ("Vertebral body",          (255,  55,  55, 160)),  # vivid red
    2: ("Pedicles",                ( 30, 130, 255, 160)),  # vivid blue
    3: ("Lamina",                  (  0, 220, 200, 160)),  # vivid teal
    4: ("Spinous process",         (255, 220,   0, 160)),  # vivid yellow
    5: ("Transverse process",      ( 40, 220,  80, 160)),  # vivid green
    6: ("Sup. articular process",  (255,  90, 200, 160)),  # vivid pink
    7: ("Inf. articular process",  (160,  50, 255, 160)),  # vivid violet
}

LEGACY_LABEL_MAP = dict(LABEL_MAP)

CT_WINDOW_PRESETS = {
    "Spine Soft": (400, 50),
    "Bone Detail": (2000, 400),
    "Canal": (300, 40),
}


def _wc_to_range(width: float, center: float) -> tuple[float, float]:
    width = max(1.0, float(width))
    center = float(center)
    return center - width / 2.0, center + width / 2.0


def _range_to_wc(lo: float, hi: float) -> tuple[float, float]:
    lo = float(lo)
    hi = float(hi)
    if hi <= lo:
        hi = lo + 1.0
    width = hi - lo
    center = (hi + lo) / 2.0
    return width, center


def _status_chip_style(color_hex: str) -> str:
    color = QColor(color_hex)
    bg = QColor(color)
    border = QColor(color)
    bg.setAlpha(38)
    border.setAlpha(120)
    return (
        "QLabel {"
        f"color: {color.name()};"
        f"background-color: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
        f"border: 1px solid rgba({border.red()},{border.green()},{border.blue()},{border.alpha()});"
        "border-radius: 8px;"
        "padding: 2px 10px;"
        "font-weight: 600;"
        "}"
    )


try:
    from inference_pipeline import run_pipeline
except (ImportError, AttributeError):
    def run_pipeline(*args, **kwargs):
        raise RuntimeError("inference_pipeline.run_pipeline not found")


class PipelineWorker(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, dcm_dir, output_dir):
        super().__init__()
        self.dcm_dir = dcm_dir
        self.output_dir = output_dir

    def run(self):
        try:
            run_pipeline_fn = globals().get("run_pipeline", run_pipeline)
            if _smoke_test_enabled() or _stdout_log_enabled():
                sys.stderr.write(f"WORKER_RUN fn={getattr(run_pipeline_fn, '__name__', type(run_pipeline_fn).__name__)}\n")
                sys.stderr.flush()
            run_pipeline_fn(self.dcm_dir, self.output_dir, self.log_line.emit)
            self.finished.emit(True, "Segmentation completed successfully.")
        except Exception as e:
            if _smoke_test_enabled() or _stdout_log_enabled():
                sys.stderr.write(f"WORKER_ERR {e}\n")
                sys.stderr.flush()
            self.finished.emit(False, str(e))


class DicomLoadWorker(QThread):
    """Converts a DICOM directory to NIfTI (if not already done) then emits paths."""
    done = pyqtSignal(str, str)   # ct_path, seg_path (may be empty string)
    error = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, dcm_dir, out_dir):
        super().__init__()
        self._dcm_dir = dcm_dir
        self._out_dir = out_dir

    def run(self):
        from pathlib import Path
        from dicom2nifti import convert_directory
        nifti_tmp = Path(self._out_dir) / "nifti_tmp"
        nifti_tmp.mkdir(parents=True, exist_ok=True)
        existing = sorted(nifti_tmp.glob("*.nii.gz"))
        if not existing:
            self.log_line.emit("Converting DICOM to NIfTI for preview…")
            try:
                convert_directory(self._dcm_dir, str(nifti_tmp), compression=True, reorient=True)
            except Exception as e:
                self.error.emit(f"DICOM conversion failed: {e}")
                return
            existing = sorted(nifti_tmp.glob("*.nii.gz"))
        if not existing:
            self.error.emit("DICOM conversion produced no NIfTI files.")
            return
        ct_path = str(existing[0])
        seg_files = sorted(Path(self._out_dir).glob("segmentation/*.nii.gz"))
        seg_path = str(seg_files[0]) if seg_files else ""
        self.done.emit(ct_path, seg_path)


# ── Stage → progress mapping (first match wins) ──────────────────────────────
_STAGE_MAP = [
    ("Step 1:",                           5,  "DICOM → NIfTI"),
    ("NIfTI file(s) produced",           12,  "DICOM → NIfTI"),
    ("Step 2:",                          15,  "File preparation"),
    ("Step 3:",                          22,  "Environment setup"),
    ("Step 4:",                          30,  "Output directory"),
    ("Step 5: Using cached",             80,  "Cached result found"),
    ("Step 5: Running nnUNet",           38,  "nnUNet launching"),
    ("emptying cuda cache",              40,  "Loading model"),
    ("loading parameters",               42,  "Loading model"),
    ("starting preprocessing generator", 46,  "Preprocessing"),
    ("preprocessing",                    49,  "Preprocessing"),
    ("starting prediction",              53,  "Running inference"),
    ("predicting",                       62,  "Running inference"),
    ("inference done",                   85,  "Exporting result"),
    ("postprocessing",                   90,  "Postprocessing"),
    ("Done.",                           100,  "Complete"),
]


class PipelineProgressBar(QWidget):
    """Progress bar with a black-and-blue light-strip theme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self._mid = (29, 78, 216)
        self._bright = (147, 197, 253)
        self._track_h = 2
        self._bg_h = 14
        self._seg_frac = 0.46
        self._progress = 0.0   # 0.0–1.0
        self._indeterminate = False
        self._failed = False
        self._phase = 0.0
        self._phase2 = 0.52
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._stage_markers = sorted({
            pct / 100.0
            for _keyword, pct, _label in _STAGE_MAP
            if 0 < pct < 100
        })

    def set_progress(self, pct):
        self._progress = max(0.0, min(1.0, pct / 100.0))
        self._failed = False
        self._sync_timer()
        self.update()

    def set_indeterminate(self, on):
        self._indeterminate = on
        if on:
            self._failed = False
        self._sync_timer()
        self.update()

    def freeze(self, failed=False):
        self._indeterminate = False
        self._failed = failed
        self._timer.stop()
        self.update()

    def reset(self):
        self._progress = 0.0
        self._indeterminate = False
        self._failed = False
        self._phase = 0.0
        self._phase2 = 0.52
        self._sync_timer()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.011) % 1.0
        self._phase2 = (self._phase2 + 0.017) % 1.0
        self.update()

    def _sync_timer(self):
        should_run = self._indeterminate or (0.0 < self._progress < 1.0)
        if should_run and not self._timer.isActive():
            self._timer.start(25)
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def _pulse_gradient(self, x0, seg, strength, alpha_mid, alpha_peak):
        mr, mg, mb = self._mid
        br, bg_, bb = self._bright
        g = QLinearGradient(x0, 0, x0 + seg, 0)
        g.setColorAt(0.0, QColor(mr // 2, mg // 2, mb // 2, 0))
        g.setColorAt(0.32, QColor(mr, mg, mb, int(alpha_mid * strength)))
        g.setColorAt(0.5, QColor(br, bg_, bb, int(alpha_peak * strength)))
        g.setColorAt(0.68, QColor(mr, mg, mb, int(alpha_mid * strength)))
        g.setColorAt(1.0, QColor(mr // 2, mg // 2, mb // 2, 0))
        return g

    def _draw_pulse(self, p, w, h, phase, strength=1.0):
        seg = int(w * self._seg_frac)
        center = phase * (w + seg) - seg // 2
        x0 = int(center - seg // 2)
        draw_x = max(0, x0)
        draw_w = min(x0 + seg, w) - draw_x
        if draw_w <= 0:
            return

        track_cy = h // 2

        outer_h = h - 2
        outer_y = track_cy - outer_h // 2
        p.setBrush(QBrush(self._pulse_gradient(x0, seg, strength, 16, 24)))
        p.drawRoundedRect(draw_x, outer_y, draw_w, outer_h, outer_h // 2, outer_h // 2)

        mid_h = 8
        mid_y = track_cy - mid_h // 2
        p.setBrush(QBrush(self._pulse_gradient(x0, seg, strength, 55, 75)))
        p.drawRoundedRect(draw_x, mid_y, draw_w, mid_h, mid_h // 2, mid_h // 2)

        track_y = track_cy - self._track_h // 2
        p.setBrush(QBrush(self._pulse_gradient(x0, seg, strength, 200, 255)))
        p.drawRoundedRect(draw_x, track_y, draw_w, self._track_h, 1, 1)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track_cy = h // 2
        bg_y = track_cy - self._bg_h // 2
        track_y = track_cy - self._track_h // 2
        complete = (self._progress >= 0.999) and not self._failed

        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#090909"))
        p.drawRect(0, 0, w, h)

        # Draw the rounded background only behind the completed portion.
        outer_track = QColor(44, 25, 10)
        inner_track = QColor(72, 40, 15)
        if self._failed:
            outer_track = QColor(78, 34, 44)
            inner_track = QColor(98, 42, 56)
        bg_w = w if (complete or self._failed) else int(w * self._progress)
        if bg_w > 0:
            p.setBrush(outer_track)
            p.drawRoundedRect(0, bg_y, bg_w, self._bg_h, self._bg_h // 2, self._bg_h // 2)
            inner_w = max(1, bg_w - 2)
            p.setBrush(inner_track)
            p.drawRoundedRect(1, bg_y + 2, inner_w, self._bg_h - 4, (self._bg_h - 4) // 2, (self._bg_h - 4) // 2)

        # Completed portion of the overall pipeline.
        if self._progress > 0:
            fill_w = min(w, max(1, int(w * self._progress)))
            fill_grad = QLinearGradient(0, 0, fill_w, 0)
            if self._failed:
                fill_grad.setColorAt(0.0, QColor(255, 92, 110, 90))
                fill_grad.setColorAt(1.0, QColor(255, 92, 110, 130))
            elif complete:
                fill_grad.setColorAt(0.0, QColor(255, 128, 36, 190))
                fill_grad.setColorAt(0.55, QColor(255, 241, 220, 235))
                fill_grad.setColorAt(1.0, QColor(255, 166, 64, 205))
            else:
                fill_grad.setColorAt(0.0, QColor(255, 112, 20, 125))
                fill_grad.setColorAt(1.0, QColor(255, 167, 66, 165))
            p.setBrush(QBrush(fill_grad))
            p.drawRoundedRect(1, bg_y + 2, max(1, fill_w - 2), self._bg_h - 4, (self._bg_h - 4) // 2, (self._bg_h - 4) // 2)

        # Segment highlights only appear on the completed portion.
        stage_edges = [0.0] + self._stage_markers + [1.0]
        for start, end in zip(stage_edges[:-1], stage_edges[1:]):
            sx = int(start * w)
            ex = max(sx + 1, int(end * w))
            if not complete and not self._failed:
                ex = min(ex, bg_w)
                if ex <= sx:
                    continue
            if self._failed:
                seg_color = QColor(255, 255, 255, 10)
            elif complete:
                seg_color = QColor(255, 255, 255, 18)
            else:
                seg_color = QColor(255, 255, 255, 14)
            p.setBrush(seg_color)
            p.drawRoundedRect(sx, bg_y + 1, ex - sx, self._bg_h - 2, (self._bg_h - 2) // 2, (self._bg_h - 2) // 2)

        # Thin separators between stages.
        p.setBrush(QColor(255, 255, 255, 32 if not complete else 72))
        for marker in self._stage_markers:
            x = min(w - 2, max(0, int(marker * w) - 1))
            p.drawRoundedRect(x, bg_y + 1, 2, self._bg_h - 2, 1, 1)

        # Sharp center line for the active progress.
        line_color = QColor(255, 163, 58, 195)
        if self._failed:
            line_color = QColor(255, 92, 110, 180)
        elif complete:
            line_color = QColor(255, 247, 236, 245)
        if self._progress > 0:
            line_w = min(w, max(1, int(w * self._progress)))
            p.setBrush(line_color)
            p.drawRoundedRect(0, track_y, line_w, self._track_h, 1, 1)

        if complete:
            glow_grad = QLinearGradient(0, 0, w, 0)
            glow_grad.setColorAt(0.0, QColor(255, 123, 28, 26))
            glow_grad.setColorAt(0.25, QColor(255, 168, 80, 42))
            glow_grad.setColorAt(0.5, QColor(255, 243, 228, 92))
            glow_grad.setColorAt(0.75, QColor(255, 168, 80, 42))
            glow_grad.setColorAt(1.0, QColor(255, 123, 28, 26))
            p.setBrush(QBrush(glow_grad))
            p.drawRoundedRect(0, track_y - 7, w, self._track_h + 14, 4, 4)

            cap = QColor(255, 235, 208, 135)
            p.setBrush(cap)
            p.drawRoundedRect(0, bg_y - 1, 4, self._bg_h + 2, 2, 2)
            p.drawRoundedRect(max(0, w - 4), bg_y - 1, 4, self._bg_h + 2, 2, 2)

        if not self._failed and (self._progress > 0 or self._indeterminate):
            if complete:
                self._draw_pulse(p, w, h, 0.5, strength=0.45)
            else:
                self._draw_pulse(p, w, h, self._phase)
                self._draw_pulse(p, w, h, self._phase2, strength=0.65)


def _laser_gradient(length, phase, reverse=False, strength=1.0):
    seg = max(24, int(length * 0.42))
    center = phase * (length + seg) - seg // 2
    start = center - seg // 2
    end = center + seg // 2
    if reverse:
        start, end = length - end, length - start
    g = QLinearGradient(start, 0, end, 0)
    g.setColorAt(0.0, QColor(110, 54, 10, 0))
    g.setColorAt(0.28, QColor(255, 146, 42, int(160 * strength)))
    g.setColorAt(0.5, QColor(255, 244, 214, int(255 * strength)))
    g.setColorAt(0.72, QColor(255, 146, 42, int(160 * strength)))
    g.setColorAt(1.0, QColor(110, 54, 10, 0))
    return g


def _draw_laser_frame(painter, rect, phase, strength=1.0):
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    if w <= 8 or h <= 8:
        return

    painter.setBrush(Qt.NoBrush)

    outer = rect.adjusted(1, 1, -2, -2)
    inner = rect.adjusted(2, 2, -3, -3)

    # Base warm border so the frame still reads when the laser is between passes.
    painter.setPen(QColor(160, 98, 38, 150))
    painter.drawRoundedRect(inner, 8, 8)

    # Horizontal passes
    top_grad = _laser_gradient(inner.width(), phase, strength=strength)
    bot_grad = _laser_gradient(inner.width(), (phase + 0.35) % 1.0, reverse=True, strength=strength * 0.9)
    painter.setPen(QPen(QBrush(top_grad), 3))
    painter.drawLine(inner.left() + 8, inner.top(), inner.right() - 8, inner.top())
    painter.setPen(QPen(QBrush(bot_grad), 3))
    painter.drawLine(inner.left() + 8, inner.bottom(), inner.right() - 8, inner.bottom())

    # Vertical passes
    painter.save()
    painter.translate(inner.left(), inner.top())
    painter.rotate(90)
    left_grad = _laser_gradient(inner.height(), (phase + 0.18) % 1.0, strength=strength * 0.85)
    painter.setPen(QPen(QBrush(left_grad), 3))
    painter.drawLine(8, 0, inner.height() - 8, 0)
    painter.restore()

    painter.save()
    painter.translate(inner.right(), inner.bottom())
    painter.rotate(-90)
    right_grad = _laser_gradient(inner.height(), (phase + 0.58) % 1.0, strength=strength * 0.85)
    painter.setPen(QPen(QBrush(right_grad), 3))
    painter.drawLine(8, 0, inner.height() - 8, 0)
    painter.restore()

    # Soft halo
    halo = QColor(255, 176, 96, int(92 * strength))
    painter.setPen(QPen(halo, 2))
    painter.drawRoundedRect(outer, 9, 9)


def _draw_static_frame(painter, rect):
    painter.setPen(QPen(QColor(255, 140, 26, 180), 2))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)


def _viewer_frame_rect(rect, inset=12):
    return rect.adjusted(inset, inset, -inset, -inset)


def _draw_loading_frame(painter, rect, phase, strength=1.0):
    try:
        _draw_laser_frame(painter, rect, phase, strength=strength)
    except Exception:
        # Qt aborts the whole app if a Python exception escapes paintEvent.
        _draw_static_frame(painter, rect)


def _slice_frame_rect(target: QRect, x_off: int, y_off: int, draw_w: int, draw_h: int) -> QRect:
    del x_off, y_off, draw_w, draw_h
    return _viewer_frame_rect(target, inset=10)


class ResourceMonitor(QThread):
    """Polls CPU, RAM, GPU and process RSS every 2 s; emits (cpu%, sys_ram%, gpu%, rss_mb)."""
    stats = pyqtSignal(float, float, float, float)

    def run(self):
        try:
            import psutil
        except ImportError:
            return
        proc = psutil.Process()
        while not self.isInterruptionRequested():
            cpu = psutil.cpu_percent(interval=0)
            ram = psutil.virtual_memory().percent
            gpu = self._read_gpu()
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            self.stats.emit(cpu, ram, gpu, rss_mb)
            self.msleep(2000)

    @staticmethod
    def _read_gpu():
        return gpu_busy_percent()


class MiniResourceBar(QWidget):
    """Compact label + bar widget for CPU / RAM display."""

    def __init__(self, label, warn_at=75, crit_at=90, parent=None):
        super().__init__(parent)
        self._label = label
        self._value = 0.0
        self._warn_at = warn_at
        self._crit_at = crit_at
        self.setFixedSize(90, 20)

    def set_value(self, pct):
        self._value = max(0.0, min(100.0, pct))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if self._value >= self._crit_at:
            bar_color = QColor("#B45309")
        elif self._value >= self._warn_at:
            bar_color = QColor("#D97706")
        else:
            bar_color = QColor("#D4A35D")

        # Label
        p.setPen(QColor("#d8c7ad"))
        p.setFont(QFont("Segoe UI", 7))
        label_w = 28
        p.drawText(0, 0, label_w, h, Qt.AlignVCenter | Qt.AlignLeft, self._label)

        # Bar track
        bx, bw, bh = label_w + 2, w - label_w - 4, 6
        by = (h - bh) // 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1f1a13"))
        p.drawRoundedRect(bx, by, bw, bh, 3, 3)

        # Fill
        fill = max(0, int(bw * self._value / 100.0))
        if fill > 0:
            p.setBrush(bar_color)
            p.drawRoundedRect(bx, by, fill, bh, 3, 3)

        # Percentage text
        p.setPen(QColor("#f0dfc7"))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(bx + bw + 2, 0, 26, h, Qt.AlignVCenter | Qt.AlignLeft,
                   f"{int(self._value)}%")


class ShinyRunButton(QPushButton):
    def __init__(self, text="Run", parent=None):
        super().__init__(text, parent)
        self._shine_phase = 0.0
        self._pulse_phase = 0.0
        self._running = False
        self._shine_timer = QTimer(self)
        self._shine_timer.timeout.connect(self._tick_shine)
        self._shine_timer.start(28)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setMinimumWidth(150)

    def _tick_shine(self):
        if self.isVisible() and (self.isEnabled() or self._running):
            self._shine_phase = (self._shine_phase + 0.006) % 1.0
            self._pulse_phase = (self._pulse_phase + 0.025) % 1.0
            self.update()

    def set_running(self, running: bool):
        self._running = bool(running)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        active = self.isDown() or self._running

        if not self.isEnabled() and not self._running:
            base = QColor("#4f3c1f")
            border = QColor(233, 197, 138, 70)
            text = QColor("#bfa37a")
            glass_alpha = 210
        elif active:
            base = QColor("#A87732")
            border = QColor("#DDB36F")
            text = QColor("#140d05")
            glass_alpha = 255
        elif self.underMouse():
            base = QColor("#D9A65B")
            border = QColor("#F0C985")
            text = QColor("#140d05")
            glass_alpha = 245
        else:
            base = QColor("#C99445")
            border = QColor("#E5BD78")
            text = QColor("#1a1207")
            glass_alpha = 238

        painter.setPen(Qt.NoPen)
        base.setAlpha(glass_alpha)
        painter.setBrush(QColor(0, 0, 0, 55))
        painter.drawRoundedRect(rect.translated(0, 2), 8, 8)
        painter.setBrush(base)
        painter.drawRoundedRect(rect, 8, 8)

        glass = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        if active:
            glass.setColorAt(0.00, QColor(225, 178, 100, 130))
            glass.setColorAt(0.52, QColor(167, 105, 39, 110))
            glass.setColorAt(1.00, QColor(78, 49, 18, 90))
        else:
            glass.setColorAt(0.00, QColor(255, 235, 190, 115))
            glass.setColorAt(0.38, QColor(231, 171, 82, 45))
            glass.setColorAt(0.78, QColor(118, 72, 24, 70))
            glass.setColorAt(1.00, QColor(255, 216, 143, 28))
        painter.setBrush(QBrush(glass))
        painter.drawRoundedRect(rect, 8, 8)

        highlight = QRect(rect.left() + 5, rect.top() + 4, max(1, rect.width() - 10), max(1, rect.height() // 3))
        painter.setBrush(QColor(255, 247, 220, 14 if active else 36))
        painter.drawRoundedRect(highlight, 6, 6)

        if self.isEnabled() or self._running:
            x = int((self._shine_phase * (rect.width() + 140)) - 140)
            shine = QLinearGradient(x, rect.top(), x + 140, rect.bottom())
            shine.setColorAt(0.00, QColor(255, 255, 255, 0))
            shine.setColorAt(0.45, QColor(255, 246, 220, 0))
            shine.setColorAt(0.50, QColor(255, 248, 224, 28 if active else 72))
            shine.setColorAt(0.55, QColor(255, 246, 220, 0))
            shine.setColorAt(1.00, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(shine))
            painter.drawRoundedRect(rect, 8, 8)

        if active:
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * math.tau)
            pulse_alpha = int(55 + 85 * pulse)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 218, 145, pulse_alpha), 3))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 9, 9)
            painter.setPen(QPen(QColor(255, 194, 89, int(34 + 46 * pulse)), 6))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 8, 8)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 225, 170, 25 if active else 55), 3))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 9, 9)
        painter.setPen(QPen(border, 2))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(text)
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignCenter, self.text())


class ReconstructionWidget(QWidget):
    """3-D surface reconstruction of segmentation labels using VTK marching cubes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._vtk_ok = False
        self._loading = False
        self._has_mesh = False
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._stack = QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._layout.addLayout(self._stack)
        self._idle_panel = QWidget(self)
        self._idle_panel.setStyleSheet("background-color: #000000; border: 1px solid #1f1a13;")
        self._stack.addWidget(self._idle_panel)

        try:
            if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
                raise RuntimeError("offscreen — skip VTK")

            from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
            import vtk

            self._vtkWidget = QVTKRenderWindowInteractor(self)
            self._stack.addWidget(self._vtkWidget)

            self._renderer = vtk.vtkRenderer()
            self._renderer.SetBackground(0.0, 0.0, 0.0)
            self._vtkWidget.GetRenderWindow().AddRenderer(self._renderer)

            style = vtk.vtkInteractorStyleTrackballCamera()
            self._vtkWidget.SetInteractorStyle(vtk.vtkInteractorStyleTrackballCamera())
            self._vtkWidget.GetRenderWindow().Render()
            self._stack.setCurrentWidget(self._idle_panel)
            self._vtk_ok = True

        except Exception:
            placeholder = QLabel("3D view unavailable\n(VTK import failed)")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #d8c7ad; background: #15120e; border: 2px solid #D4A35D; border-radius: 4px;")
            self._stack.addWidget(placeholder)
            self._stack.setCurrentWidget(placeholder)

    def _tick_pulse(self):
        self._pulse_phase = (self._pulse_phase + 0.018) % 1.0
        self.update()

    def set_loading(self, on):
        self._loading = on
        if on:
            if not self._pulse_timer.isActive():
                self._pulse_timer.start(35)
        else:
            self._pulse_timer.stop()
            self._pulse_phase = 0.0
        self.update()

    def paintEvent(self, event):
        try:
            super().paintEvent(event)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            frame_rect = _viewer_frame_rect(self.rect(), inset=5)
            if self._loading:
                _draw_loading_frame(painter, frame_rect, self._pulse_phase, strength=1.0)
        except Exception:
            # Never let a Python exception escape paintEvent; Qt will abort the app.
            fallback = QPainter(self)
            fallback.setRenderHint(QPainter.Antialiasing)
            fallback.fillRect(self.rect(), QColor("#101010"))

    def load(self, seg_path):
        if not self._vtk_ok:
            return
        import vtk
        import nibabel as nib
        from vtk.util import numpy_support

        nib_img = nib.load(seg_path)
        seg_vol = np.asarray(nib_img.dataobj, dtype=np.uint8)

        # Voxel spacing from NIfTI header — fixes z-axis compression
        zooms = nib_img.header.get_zooms()
        spacing = (float(zooms[0]), float(zooms[1]), float(zooms[2]))

        self._renderer.RemoveAllViewProps()

        # Two-point lighting for depth
        for pos in [(1, 1, 1), (-1, -0.5, -1)]:
            light = vtk.vtkLight()
            light.SetLightTypeToSceneLight()
            light.SetPosition(*pos)
            light.SetIntensity(0.8)
            self._renderer.AddLight(light)

        any_mesh = False
        for label_id, (_, color) in LABEL_MAP.items():
            mask = np.ascontiguousarray((seg_vol == label_id).astype(np.uint8))
            if not mask.any():
                continue

            vtk_array = numpy_support.numpy_to_vtk(
                mask.flatten(order="F"), deep=True, array_type=vtk.VTK_UNSIGNED_CHAR
            )
            img = vtk.vtkImageData()
            img.SetDimensions(mask.shape[0], mask.shape[1], mask.shape[2])
            img.SetSpacing(*spacing)
            img.GetPointData().SetScalars(vtk_array)

            mc = vtk.vtkMarchingCubes()
            mc.SetInputData(img)
            mc.SetValue(0, 0.5)
            mc.Update()

            if mc.GetOutput().GetNumberOfPoints() == 0:
                continue

            smooth = vtk.vtkSmoothPolyDataFilter()
            smooth.SetInputConnection(mc.GetOutputPort())
            smooth.SetNumberOfIterations(20)
            smooth.SetRelaxationFactor(0.1)
            smooth.Update()

            normals = vtk.vtkPolyDataNormals()
            normals.SetInputConnection(smooth.GetOutputPort())
            normals.Update()

            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(normals.GetOutputPort())
            mapper.ScalarVisibilityOff()

            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(color[0] / 255, color[1] / 255, color[2] / 255)
            actor.GetProperty().SetOpacity(0.85)
            actor.GetProperty().SetSpecular(0.3)
            actor.GetProperty().SetSpecularPower(20)

            self._renderer.AddActor(actor)
            any_mesh = True

        self._has_mesh = any_mesh
        if any_mesh:
            self._renderer.ResetCamera()
            self._stack.setCurrentWidget(self._vtkWidget)
            self._vtkWidget.GetRenderWindow().Render()
        else:
            self._stack.setCurrentWidget(self._idle_panel)


class CTViewerWidget(QWidget):
    def __init__(self, parent=None, label="CT + Segmentation", show_overlay=True, plane="sagittal"):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #000000; border: none;")
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.WheelFocus)
        self._ct = None
        self._seg = None
        self._spacing = (1.0, 1.0, 1.0)
        self._slice = 0
        self._show_overlay = show_overlay
        self._label = label
        self._meta_text = ""
        self._plane = plane
        self.n_slices = 0
        self._placeholder = True
        self._loading = False
        self._pulse_phase = 0.0
        self._window = (0.0, 255.0)
        self._drag_pos = None
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._glow_color = QColor(212, 163, 93, 0)
        self._glow_core = QColor(245, 191, 112, 0)

    def _tick_pulse(self):
        self._pulse_phase = (self._pulse_phase + 0.018) % 1.0
        self.update()

    def set_loading(self, on):
        self._loading = on
        if on:
            if not self._pulse_timer.isActive():
                self._pulse_timer.start(35)
        else:
            self._pulse_timer.stop()
            self._pulse_phase = 0.0
        self.update()

    def load(self, ct_path, seg_path=None):
        import nibabel as nib
        from nibabel.orientations import io_orientation, axcodes2ornt, ornt_transform, apply_orientation
        import gc
        ct_raw = nib.load(ct_path)
        ct_can = nib.as_closest_canonical(ct_raw)
        ct_vol = np.asarray(ct_can.dataobj, dtype=np.int16)
        raw_spacing = tuple(float(v) for v in ct_can.header.get_zooms()[:3])
        # Guard against malformed headers (0/NaN/huge spacing) collapsing display.
        safe_spacing = []
        for v in raw_spacing:
            if not np.isfinite(v) or v <= 0.0 or v > 10.0:
                safe_spacing.append(1.0)
            else:
                safe_spacing.append(v)
        self._spacing = tuple(safe_spacing)
        self._window = self._choose_display_window(ct_vol)
        if seg_path:
            # Apply the CT's own orientation transform to the SEG instead of
            # canonicalizing the SEG independently.  When the CT has
            # qform_code=0/sform_code=0 and the nnUNet SEG carries a different
            # axis code on the Y axis (e.g. 'P' vs the CT's 'A'), independent
            # canonicalization flips the SEG Y axis while leaving the CT
            # unchanged, shifting the overlay by ~90 voxels coronally.
            ct_to_ras = ornt_transform(
                io_orientation(ct_raw.affine),
                axcodes2ornt(('R', 'A', 'S')),
            )
            seg_raw_vol = np.asarray(nib.load(seg_path).dataobj, dtype=np.uint8)
            seg_vol = apply_orientation(seg_raw_vol, ct_to_ras).astype(np.uint8)
            shape = tuple(min(a, b) for a, b in zip(ct_vol.shape, seg_vol.shape))
            self._ct = np.ascontiguousarray(ct_vol[:shape[0], :shape[1], :shape[2]])
            self._seg = np.ascontiguousarray(seg_vol[:shape[0], :shape[1], :shape[2]])
            self._meta_text = "Mask aligned to CT orientation"
            del ct_vol, seg_vol, seg_raw_vol, ct_raw, ct_can
        else:
            self._ct = ct_vol
            self._seg = np.zeros(ct_vol.shape, dtype=np.uint8)
            self._meta_text = ""
            del ct_raw, ct_can
        gc.collect()
        self.n_slices = self._slice_count()
        self._slice = self._best_mask_slice() if seg_path and self._seg.any() else self._best_content_slice()
        self._placeholder = False
        self.update()

    @staticmethod
    def _choose_display_window(ct_vol: np.ndarray) -> tuple[float, float]:
        if ct_vol.size == 0:
            return 0.0, 255.0
        if np.issubdtype(ct_vol.dtype, np.integer) and ct_vol.min() >= 0 and ct_vol.max() <= 255:
            return 0.0, 255.0
        flat = np.asarray(ct_vol, dtype=np.float32).ravel()
        if flat.size > 2_000_000:
            stride = max(1, flat.size // 2_000_000)
            flat = flat[::stride]
        finite = flat[np.isfinite(flat)]
        if finite.size == 0:
            return 0.0, 1.0

        positive = finite[finite > 0]
        sample = positive if positive.size >= max(4096, finite.size // 20) else finite
        lo = float(np.percentile(sample, 0.5))
        hi = float(np.percentile(sample, 99.5))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = float(np.percentile(finite, 1))
            hi = float(np.percentile(finite, 99))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = float(np.min(finite))
            hi = float(np.max(finite))
        if hi <= lo:
            hi = lo + 1.0
        pad = max(1.0, (hi - lo) * 0.03)
        return lo - pad, hi + pad

    def _slice_count(self):
        if self._ct is None:
            return 0
        axis = {"sagittal": 0, "coronal": 1, "axial": 2}[self._plane]
        return self._ct.shape[axis]

    def _current_slices(self):
        idx = self._slice
        if self._plane == "sagittal":
            return np.flipud(self._ct[idx, :, :].T), np.flipud(self._seg[idx, :, :].T)
        if self._plane == "coronal":
            return np.flipud(self._ct[:, idx, :].T), np.flipud(self._seg[:, idx, :].T)
        return self._ct[:, :, idx].T, self._seg[:, :, idx].T

    def _display_spacing(self):
        sx, sy, sz = self._spacing
        if self._plane == "sagittal":
            return sy, sz
        if self._plane == "coronal":
            return sx, sz
        return sx, sy

    def _best_mask_slice(self):
        if self._seg is None or not self._seg.any():
            return self._best_content_slice()
        if self._plane == "sagittal":
            idx = int(np.argmax(self._seg.sum(axis=(1, 2))))
        elif self._plane == "coronal":
            idx = int(np.argmax(self._seg.sum(axis=(0, 2))))
        else:
            idx = int(np.argmax(self._seg.sum(axis=(0, 1))))
        return max(0, min(self.n_slices - 1, idx))

    def _best_content_slice(self):
        if self._ct is None or not self.n_slices:
            return 0
        if self._plane == "sagittal":
            scores = self._ct.sum(axis=(1, 2))
        elif self._plane == "coronal":
            scores = self._ct.sum(axis=(0, 2))
        else:
            scores = self._ct.sum(axis=(0, 1))
        idx = int(np.argmax(scores))
        return max(0, min(self.n_slices - 1, idx))

    def set_slice(self, z):
        if self._ct is None:
            return
        self._slice = max(0, min(z, self.n_slices - 1))
        self.update()

    def set_overlay(self, visible):
        self._show_overlay = visible
        self.update()

    def set_meta_text(self, text):
        self._meta_text = text or ""
        self.update()

    def set_window(self, width: float, center: float):
        self._window = _wc_to_range(width, center)
        self.update()

    def set_window_preset(self, name: str):
        if name in CT_WINDOW_PRESETS:
            self.set_window(*CT_WINDOW_PRESETS[name])

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton and self._ct is not None:
            self._drag_pos = event.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._drag_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._ct is None or self._drag_pos is None or not (event.buttons() & Qt.RightButton):
            return super().mouseMoveEvent(event)
        dx = event.x() - self._drag_pos.x()
        dy = event.y() - self._drag_pos.y()
        width, center = _range_to_wc(*self._window)
        width = max(1.0, width + dx * 6.0)
        center = center - dy * 3.0
        self._window = _wc_to_range(width, center)
        self._drag_pos = event.pos()
        self.update()
        event.accept()

    def wheelEvent(self, event):
        if self._ct is None:
            return
        mods = event.modifiers()
        if mods & Qt.ControlModifier:
            step = 40 if event.angleDelta().y() > 0 else -40
            width, center = _range_to_wc(*self._window)
            self._window = _wc_to_range(max(1.0, width + step), center)
            self.update()
            return
        if mods & Qt.ShiftModifier:
            step = 20 if event.angleDelta().y() > 0 else -20
            width, center = _range_to_wc(*self._window)
            self._window = _wc_to_range(width, center + step)
            self.update()
            return
        delta = -1 if event.angleDelta().y() > 0 else 1
        new_slice = max(0, min(self.n_slices - 1, self._slice + delta))
        if new_slice != self._slice:
            self._slice = new_slice
            self.update()

    def keyPressEvent(self, event):
        if self._ct is None:
            return super().keyPressEvent(event)
        if event.key() in (Qt.Key_Up, Qt.Key_Left):
            delta = -1
        elif event.key() in (Qt.Key_Down, Qt.Key_Right):
            delta = 1
        else:
            return super().keyPressEvent(event)
        new_slice = max(0, min(self.n_slices - 1, self._slice + delta))
        if new_slice != self._slice:
            self._slice = new_slice
            self.update()

    def paintEvent(self, _event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)

            if self._placeholder or self._ct is None:
                painter.fillRect(self.rect(), QColor("#000000"))
                painter.setPen(QColor("#8fa3bf"))
                painter.setFont(QFont("Segoe UI", 11))
                painter.drawText(self.rect(), Qt.AlignCenter, self._label)
                self._paint_laser_frame(painter, _viewer_frame_rect(self.rect(), inset=14), strength=0.22)
                frame_rect = _viewer_frame_rect(self.rect(), inset=14)
                if self._loading:
                    _draw_loading_frame(painter, frame_rect, self._pulse_phase, strength=1.0)
                return

            ct_slice, seg_slice = self._current_slices()

            w_min, w_max = self._window
            ct_float = ct_slice.astype(np.float32)
            denom = max(1e-6, w_max - w_min)
            ct_norm = np.clip((ct_float - w_min) / denom, 0, 1)
            # If global windowing collapses local contrast or drives the slice too dark,
            # re-window per slice with robust percentiles.
            local_span = float(ct_norm.max() - ct_norm.min()) if ct_norm.size else 0.0
            p01 = float(np.percentile(ct_norm, 1.0)) if ct_norm.size else 0.0
            p99 = float(np.percentile(ct_norm, 99.0)) if ct_norm.size else 0.0
            too_flat = local_span < 0.08 or (p99 - p01) < 0.07
            too_dark = p99 < 0.12
            too_bright = p01 > 0.88
            if too_flat or too_dark or too_bright:
                nz = ct_float[np.isfinite(ct_float)]
                if nz.size:
                    lmin = float(np.percentile(nz, 0.5))
                    lmax = float(np.percentile(nz, 99.5))
                    if lmax <= lmin:
                        lmin = float(np.min(nz))
                        lmax = float(np.max(nz))
                    if lmax <= lmin:
                        lmax = lmin + 1.0
                    ct_norm = np.clip((ct_float - lmin) / max(1e-6, lmax - lmin), 0, 1)
            ct_u8 = np.ascontiguousarray((ct_norm * 255).astype(np.uint8))

            h, w = ct_u8.shape
            ct_img = QImage(ct_u8.data, w, h, w, QImage.Format_Grayscale8).copy()
            ct_px = QPixmap.fromImage(ct_img)

            target = self.rect()
            spacing_x, spacing_y = self._display_spacing()
            physical_w = max(1e-6, w * spacing_x)
            physical_h = max(1e-6, h * spacing_y)
            scale = min(target.width() / physical_w, target.height() / physical_h)
            draw_w = max(1, int(physical_w * scale))
            draw_h = max(1, int(physical_h * scale))
            x_off = (target.width() - draw_w) // 2
            y_off = (target.height() - draw_h) // 2

            painter.fillRect(self.rect(), QColor("#000000"))
            painter.drawPixmap(x_off, y_off, draw_w, draw_h, ct_px)
            frame_rect = _slice_frame_rect(self.rect(), x_off, y_off, draw_w, draw_h)
            self._paint_laser_frame(painter, frame_rect, strength=0.42)

            if self._show_overlay:
                rgba = np.zeros((h, w, 4), dtype=np.uint8)
                for label_id, (_, color) in LABEL_MAP.items():
                    mask = seg_slice == label_id
                    if mask.any():
                        rgba[mask, 0] = color[0]
                        rgba[mask, 1] = color[1]
                        rgba[mask, 2] = color[2]
                        rgba[mask, 3] = min(55, int(color[3]))
                ov_img = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
                ov_px = QPixmap.fromImage(ov_img)
                painter.drawPixmap(x_off, y_off, draw_w, draw_h, ov_px)

            if self._meta_text:
                margin = 10
                text_rect = target.adjusted(margin, margin, -margin, -margin)
                painter.setFont(QFont("Segoe UI", 9))
                metrics = painter.fontMetrics()
                text_w = metrics.horizontalAdvance(self._meta_text) + 14
                text_h = metrics.height() + 8
                box = text_rect
                box.setWidth(min(text_w, max(80, target.width() - 2 * margin)))
                box.setHeight(text_h)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, 150))
                painter.drawRoundedRect(box, 6, 6)
                painter.setPen(QColor("#c7d6f8"))
                painter.drawText(box.adjusted(7, 0, -7, 0), Qt.AlignVCenter | Qt.AlignLeft, self._meta_text)

            if self._loading:
                _draw_loading_frame(painter, frame_rect, self._pulse_phase, strength=1.0)
        except Exception:
            # Never let a Python exception escape paintEvent; Qt will abort the app.
            fallback = QPainter(self)
            fallback.setRenderHint(QPainter.Antialiasing)
            fallback.fillRect(self.rect(), QColor("#000000"))
            fallback.setPen(QColor("#8fa3bf"))
            fallback.setFont(QFont("Segoe UI", 11))
            fallback.drawText(self.rect(), Qt.AlignCenter, self._label)

    def _paint_laser_frame(self, painter, rect, strength=1.0):
        if rect.isEmpty():
            return
        prev_pen = painter.pen()
        prev_brush = painter.brush()
        try:
            painter.setBrush(Qt.NoBrush)
            for spread, alpha, width in ((12, 8, 1), (8, 16, 1), (4, 30, 1), (1, 56, 2)):
                c = QColor(self._glow_color)
                c.setAlpha(int(alpha * strength))
                painter.setPen(QPen(c, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawRoundedRect(rect.adjusted(spread, spread, -spread, -spread), 12, 12)
            core = QColor(self._glow_core)
            core.setAlpha(int(170 * strength))
            painter.setPen(QPen(core, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 10, 10)
        finally:
            painter.setPen(prev_pen)
            painter.setBrush(prev_brush)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spine Segmentation")
        self.setMinimumSize(900, 600)
        self.worker = None
        self._pipeline_start = None
        self._last_progress = 0
        self._viewer_pulse_started_at = 0.0
        self._viewer_pulse_prelude_ms = 2000
        self._last_preview_output = ""
        self._eta_timer = QTimer(self)
        self._eta_timer.timeout.connect(self._update_eta)
        self.init_ui()
        self._resource_monitor = ResourceMonitor()
        self._resource_monitor.stats.connect(self._on_resource_stats)
        self._resource_monitor.start()

    @staticmethod
    def _apply_glow(widget, color="#D4A35D", blur=18, offset=(0, 0), alpha=90):
        effect = QGraphicsDropShadowEffect(widget)
        glow = QColor(color)
        glow.setAlpha(alpha)
        effect.setColor(glow)
        effect.setBlurRadius(blur)
        effect.setOffset(*offset)
        widget.setGraphicsEffect(effect)

    def init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(6)

        # ── Top control bar ──────────────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(8)

        top.addWidget(QLabel("Input"))
        self.input_mode_combo = QComboBox()
        self.input_mode_combo.addItem("DICOM Folder", "dicom_dir")
        self.input_mode_combo.addItem("NIfTI File", "nifti_file")
        self.input_mode_combo.currentIndexChanged.connect(self._on_input_mode_changed)
        top.addWidget(self.input_mode_combo)

        self.dcm_input = QLineEdit()
        self.dcm_input.setReadOnly(True)
        self.dcm_input.setPlaceholderText("Choose DICOM folder")
        top.addWidget(self.dcm_input, 3)
        self.dcm_button = QPushButton("Browse")
        self.dcm_button.clicked.connect(self.browse_input)
        top.addWidget(self.dcm_button)

        top.addWidget(QLabel("Output"))
        self.output_input = QLineEdit()
        self.output_input.setReadOnly(True)
        self.output_input.setPlaceholderText("Choose output folder")
        top.addWidget(self.output_input, 3)
        self.output_button = QPushButton("Browse")
        self.output_button.clicked.connect(self.browse_output)
        top.addWidget(self.output_button)

        self.run_button = ShinyRunButton("Run")
        self.run_button.setObjectName("primaryRunButton")
        self.run_button.setMinimumHeight(44)
        self.run_button.setMinimumWidth(150)
        self.run_button.clicked.connect(self.start_pipeline)
        self._apply_glow(self.run_button, color="#D4A35D", blur=26, alpha=145)

        self.load_2d_button = QPushButton("Load 2D")
        self.load_2d_button.clicked.connect(self.load_overlay_visualization)
        self.load_2d_button.setEnabled(False)

        self.load_3d_button = QPushButton("Load 3D")
        self.load_3d_button.clicked.connect(self.load_3d_visualization)
        self.load_3d_button.setEnabled(False)

        self.status_label = QLabel("● Idle")
        self.status_label.setObjectName("statuslabel")
        self.status_label.setMinimumWidth(120)
        self.status_label.setStyleSheet(_status_chip_style("#8f9db8"))
        top.addWidget(self.status_label)

        root_layout.addLayout(top)

        # ── Main splitter ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("background-color: #000000;")

        # Left sidebar
        sidebar = QWidget()
        sidebar.setMaximumWidth(240)
        sidebar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        sidebar.setStyleSheet("background-color: #000000;")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.setSpacing(6)

        cmd_hdr = QLabel("ACTIONS")
        cmd_hdr.setFont(QFont("Segoe UI", 9, QFont.Bold))
        cmd_hdr.setStyleSheet("color: #D4A35D; letter-spacing: 2px;")
        side_layout.addWidget(cmd_hdr, alignment=Qt.AlignHCenter)

        self.run_button.setMinimumHeight(38)
        side_layout.addWidget(self.run_button)
        side_layout.addWidget(self.load_2d_button)
        side_layout.addWidget(self.load_3d_button)

        select_sep = QFrame()
        select_sep.setFrameShape(QFrame.HLine)
        select_sep.setStyleSheet("color: #5a4426;")
        side_layout.addWidget(select_sep)

        select_hdr = QLabel("SELECTION")
        select_hdr.setFont(QFont("Segoe UI", 9, QFont.Bold))
        select_hdr.setStyleSheet("color: #D4A35D; letter-spacing: 2px;")
        side_layout.addWidget(select_hdr, alignment=Qt.AlignHCenter)

        scan_lbl = QLabel("Scan")
        scan_lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        scan_lbl.setStyleSheet("color: #d8c7ad;")
        side_layout.addWidget(scan_lbl)
        self.ct_series_combo = QComboBox()
        self.ct_series_combo.setEnabled(False)
        self.ct_series_combo.currentIndexChanged.connect(self._on_series_selection_changed)
        side_layout.addWidget(self.ct_series_combo)

        mask_lbl = QLabel("Mask")
        mask_lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        mask_lbl.setStyleSheet("color: #d8c7ad;")
        side_layout.addWidget(mask_lbl)
        self.seg_series_combo = QComboBox()
        self.seg_series_combo.setEnabled(False)
        self.seg_series_combo.currentIndexChanged.connect(self._on_series_selection_changed)
        side_layout.addWidget(self.seg_series_combo)

        cmd_sep = QFrame()
        cmd_sep.setFrameShape(QFrame.HLine)
        cmd_sep.setStyleSheet("color: #5a4426;")
        side_layout.addWidget(cmd_sep)

        win_hdr = QLabel("WINDOWING")
        win_hdr.setFont(QFont("Segoe UI", 9, QFont.Bold))
        win_hdr.setStyleSheet("color: #8fa3bf; letter-spacing: 2px;")
        side_layout.addWidget(win_hdr, alignment=Qt.AlignHCenter)

        win_hint = QLabel("Right-drag: W/L\nCtrl+wheel: width\nShift+wheel: center")
        win_hint.setAlignment(Qt.AlignHCenter)
        win_hint.setStyleSheet("color: #8f9db8;")
        side_layout.addWidget(win_hint)

        win_row = QHBoxLayout()
        win_row.setSpacing(4)
        for preset_name in ("Spine Soft", "Bone Detail", "Canal"):
            b = QPushButton(preset_name)
            b.setMinimumHeight(26)
            b.clicked.connect(lambda _checked=False, n=preset_name: self._set_viewer_window_preset(n))
            win_row.addWidget(b)
        side_layout.addLayout(win_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #263554;")
        side_layout.addWidget(sep)

        side_layout.addStretch()

        logos = QHBoxLayout()
        logos.setSpacing(10)
        logos.setContentsMargins(0, 8, 0, 0)
        logos.addStretch()
        used_logo_names = set()
        for path, max_w, max_h in (
            ("assets/branding/Logo_USZ.png", 60, 28),
            ("assets/branding/uzh-logo-white.png", 60, 28),
            ("assets/branding/micn_logo_transparent_smooth.png", 48, 28),
            ("Spine/code/Logo_USZ.png", 60, 28),
            ("Spine/code/uzh-logo-white.png", 60, 28),
            ("Spine/code/micn_logo_transparent_smooth.png", 48, 28),
        ):
            key = os.path.basename(path).lower()
            if key in used_logo_names:
                continue
            label = self._make_logo_label(path, max_w, max_h)
            if label is not None:
                used_logo_names.add(key)
                logos.addWidget(label, alignment=Qt.AlignVCenter)
        logos.addStretch()
        side_layout.addLayout(logos)

        splitter.addWidget(sidebar)

        # Multi-view container
        viewer_container = QWidget()
        viewer_container.setStyleSheet("background-color: #000000;")
        viewer_grid = QGridLayout(viewer_container)
        viewer_grid.setContentsMargins(0, 0, 0, 0)
        viewer_grid.setSpacing(4)
        viewer_grid.setColumnStretch(0, 1)
        viewer_grid.setColumnStretch(1, 1)
        viewer_grid.setRowStretch(0, 1)
        viewer_grid.setRowStretch(1, 1)

        def make_panel(title, widget):
            wrap = QWidget()
            wrap.setStyleSheet("background-color: #000000;")
            box = QVBoxLayout(wrap)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            hdr = QLabel(title)
            hdr.setAlignment(Qt.AlignHCenter)
            hdr.setFont(QFont("Segoe UI", 8, QFont.Bold))
            hdr.setStyleSheet("color: #8fa3bf; letter-spacing: 1px;")
            box.addWidget(hdr)
            box.addWidget(widget)
            return wrap

        self.viewer_axial = CTViewerWidget(label="Run to view axial slices", show_overlay=True, plane="axial")
        self.viewer_coronal = CTViewerWidget(label="Run to view coronal slices", show_overlay=True, plane="coronal")
        self.viewer_sagittal = CTViewerWidget(label="Run to view sagittal slices", show_overlay=True, plane="sagittal")
        self.viewer_3d = ReconstructionWidget()

        viewer_grid.addWidget(make_panel("Axial", self.viewer_axial), 0, 0)
        viewer_grid.addWidget(make_panel("Coronal", self.viewer_coronal), 0, 1)
        viewer_grid.addWidget(make_panel("Sagittal", self.viewer_sagittal), 1, 0)
        viewer_grid.addWidget(make_panel("3D", self.viewer_3d), 1, 1)
        splitter.addWidget(viewer_container)
        splitter.setSizes([220, 780])

        root_layout.addWidget(splitter, stretch=1)

        # ── Log area ─────────────────────────────────────────────────────────
        log_header = QHBoxLayout()
        log_header.setContentsMargins(2, 0, 2, 0)
        log_label = QLabel("Log")
        log_label.setFont(QFont("Segoe UI", 8, QFont.Bold))
        log_label.setStyleSheet("color: #D4A35D; letter-spacing: 1px;")
        log_header.addWidget(log_label)
        log_header.addStretch()
        self._log_toggle = QPushButton("▼ Hide")
        self._log_toggle.setCheckable(True)
        self._log_toggle.setChecked(True)
        self._log_toggle.setFixedHeight(22)
        self._log_toggle.setFont(QFont("Segoe UI", 8))
        self._log_toggle.toggled.connect(self._toggle_log)
        log_header.addWidget(self._log_toggle)
        root_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 9))
        self.log_text.setMaximumHeight(220)
        root_layout.addWidget(self.log_text)

        # ── Status strip ─────────────────────────────────────────────────────
        self.progress_bar = PipelineProgressBar()
        root_layout.addWidget(self.progress_bar)

        strip = QWidget()
        strip.setFixedHeight(26)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(4, 0, 4, 0)
        strip_layout.setSpacing(8)

        self.stage_label = QLabel("Ready")
        self.stage_label.setFont(QFont("Segoe UI", 8))
        self.stage_label.setStyleSheet("color: #8f9db8;")
        strip_layout.addWidget(self.stage_label)

        strip_layout.addStretch()

        self.eta_label = QLabel("")
        self.eta_label.setFont(QFont("Segoe UI", 8))
        self.eta_label.setStyleSheet("color: #b9c7de;")
        strip_layout.addWidget(self.eta_label)

        strip_layout.addSpacing(12)

        self._cpu_bar = MiniResourceBar("CPU")
        self._ram_bar = MiniResourceBar("RAM", warn_at=80, crit_at=92)
        self._gpu_bar = MiniResourceBar("GPU")
        strip_layout.addWidget(self._cpu_bar)
        strip_layout.addWidget(self._ram_bar)
        strip_layout.addWidget(self._gpu_bar)

        self._rss_label = QLabel("Mem —")
        self._rss_label.setFont(QFont("Segoe UI", 7))
        self._rss_label.setStyleSheet("color: #8fa3bf;")
        self._rss_label.setFixedWidth(68)
        strip_layout.addWidget(self._rss_label)

        self._free_btn = QPushButton("Clear")
        self._free_btn.setFixedSize(64, 18)
        self._free_btn.setFont(QFont("Segoe UI", 7))
        self._free_btn.clicked.connect(self._free_ct_data)
        strip_layout.addWidget(self._free_btn)

        root_layout.addWidget(strip)

    def _make_logo_label(self, rel_path, max_width, max_height):
        candidate_paths = []
        script_dir = Path(__file__).resolve().parent
        candidate_paths.append(script_dir / rel_path)
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidate_paths.append(exe_dir / rel_path)
            candidate_paths.append(exe_dir / "_internal" / rel_path)
            for parent in exe_dir.parents:
                candidate_paths.append(parent / rel_path)
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidate_paths.append(Path(meipass) / rel_path)
        candidate_paths.append(Path.cwd() / rel_path)
        candidate_paths.append(Path.cwd() / "GUI" / rel_path)

        logo_path = None
        for candidate in candidate_paths:
            if candidate.exists():
                logo_path = candidate
                break
        if logo_path is None:
            return None

        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return None

        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("background: transparent;")
        label.setPixmap(
            pixmap.scaled(
                max_width,
                max_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        return label

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_input_mode_changed(self, _index):
        if self.input_mode_combo.currentData() == "nifti_file":
            self.dcm_input.setPlaceholderText("Choose NIfTI file (.nii or .nii.gz)")
        else:
            self.dcm_input.setPlaceholderText("Choose DICOM folder")

    @staticmethod
    def _default_output_dir(input_path):
        if os.path.isfile(input_path):
            return os.path.join(os.path.dirname(input_path), "output")
        return os.path.join(os.path.dirname(input_path), "output")

    def browse_input(self):
        try:
            import psutil
            rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            if rss_mb > 3072:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "High memory use",
                    f"Process is using {rss_mb / 1024:.1f} GB of RAM.\n"
                    "Click 'Clear' first."
                )
                return
        except Exception:
            pass

        if self.input_mode_combo.currentData() == "nifti_file":
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "Choose NIfTI file",
                "",
                "NIfTI files (*.nii *.nii.gz)",
            )
        else:
            selected = QFileDialog.getExistingDirectory(self, "Choose DICOM folder")

        if selected:
            self.dcm_input.setText(selected)
            out = self._default_output_dir(selected)
            self.output_input.setText(out)
            self._refresh_visualization_buttons(auto_load=False)

    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            resolved = self._resolve_output_root(folder)
            self.output_input.setText(resolved)
            self._refresh_visualization_buttons(auto_load=False)
            if resolved != folder:
                self._append_log(f"Viewer mode: using results root {resolved}")

    def start_pipeline(self):
        if not self.dcm_input.text() or not self.output_input.text():
            return
        self._free_ct_data()
        self.run_button.set_running(True)
        self.run_button.setEnabled(False)
        self._set_status("Running", "#dbeafe")
        self._pipeline_start = time.time()
        self._last_progress = 0
        self.progress_bar.reset()
        self.progress_bar.set_indeterminate(True)
        self.stage_label.setText("Starting")
        self.stage_label.setStyleSheet("color: #8fa3bf;")
        self.eta_label.setText("")
        self._eta_timer.start(1000)
        self.worker = PipelineWorker(self.dcm_input.text(), self.output_input.text())
        self.worker.log_line.connect(self._append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.setStackSize(64 * 1024 * 1024)  # OpenBLAS needs large stack
        self.worker.start()

    def on_finished(self, success, message):
        self._eta_timer.stop()
        self._pipeline_start = None
        self.run_button.set_running(False)
        self.run_button.setEnabled(True)
        self.progress_bar.set_indeterminate(False)
        if success:
            self._set_status("Done", "#dbeafe")
            self.progress_bar.set_progress(100)
            self.progress_bar.freeze(failed=False)
            self.stage_label.setText("Done")
            self.stage_label.setStyleSheet("color: #fff0db; font-weight: 600;")
            self.eta_label.setText("")
            self._refresh_visualization_buttons(auto_load=True)
            if not open_path_in_file_manager(self.output_input.text()):
                QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_input.text()))
        else:
            self._set_status("Error", "#ff4757")
            self.progress_bar.freeze(failed=True)
            self.stage_label.setText("Failed")
            self.stage_label.setStyleSheet("color: #ff4757;")
            self.eta_label.setText("")
            self._append_log(f"Error: {message}")

    def _auto_load_viewers(self):
        ct_path, seg_path = self._selected_visualization_files()
        if ct_path:
            self._append_log("Auto-loading 2D viewer…")
            self.load_overlay_visualization()
        if seg_path:
            self._append_log("3D viewer ready; use Load 3D when needed.")

    def _append_log(self, line):
        self.log_text.append(line)
        self.log_text.ensureCursorVisible()
        if _smoke_test_enabled() or _stdout_log_enabled():
            sys.stderr.write(f"{line}\n")
            sys.stderr.flush()
        if self._pipeline_start is not None:
            self._parse_stage(line)

    def _toggle_log(self, checked: bool):
        self.log_text.setVisible(checked)
        self._log_toggle.setText("▼ Hide" if checked else "▲ Show")

    def _parse_stage(self, line):
        line_lower = line.lower()
        for keyword, pct, label in _STAGE_MAP:
            if keyword.lower() in line_lower and pct > self._last_progress:
                self._last_progress = pct
                self.progress_bar.set_indeterminate(False)
                self.progress_bar.set_progress(pct)
                self.stage_label.setText(label)
                self.stage_label.setStyleSheet("color: #8f9db8;")
                break

    def _update_eta(self):
        if self._pipeline_start is None or self._last_progress <= 0:
            return
        elapsed = time.time() - self._pipeline_start
        pct = self._last_progress
        if pct >= 100:
            self.eta_label.setText("")
            return
        total_est = elapsed / (pct / 100.0)
        remaining = max(0, total_est - elapsed)
        if remaining < 60:
            eta_str = f"~{int(remaining)}s"
        else:
            m = int(remaining // 60)
            s = int(remaining % 60)
            eta_str = f"~{m}m {s:02d}s"
        self.eta_label.setText(eta_str)

    def _on_resource_stats(self, cpu, ram, gpu, rss_mb):
        self._cpu_bar.set_value(cpu)
        self._ram_bar.set_value(ram)
        self._gpu_bar.set_value(gpu)
        if rss_mb < 1024:
            txt = f"Mem {int(rss_mb)}M"
        else:
            txt = f"Mem {rss_mb / 1024:.1f}G"
        if rss_mb > 3072:
            color = "#B45309"
        elif rss_mb > 2048:
            color = "#D97706"
        else:
            color = "#d8c7ad"
        self._rss_label.setStyleSheet(f"color: {color};")
        self._rss_label.setText(txt)

    def _free_ct_data(self):
        import gc
        for viewer in (self.viewer_axial, self.viewer_coronal, self.viewer_sagittal):
            viewer._ct = None
            viewer._seg = None
            viewer._placeholder = True
            viewer.n_slices = 0
            viewer.update()
        if hasattr(self.viewer_3d, '_vtk_ok') and self.viewer_3d._vtk_ok:
            self.viewer_3d._renderer.RemoveAllViewProps()
            self.viewer_3d._vtkWidget.GetRenderWindow().Render()
        gc.collect()
        self._append_log("Memory cleared.")

    def _set_status(self, text, color="#dbeafe"):
        self.status_label.setStyleSheet(_status_chip_style(color))
        self.status_label.setText(f"● {text}")

    @staticmethod
    def _resolve_output_root(output_dir):
        if not output_dir:
            return ""
        path = Path(output_dir).expanduser()
        if not path.exists():
            return str(path)
        candidates = [path, *path.parents]
        for candidate in candidates:
            if (candidate / "nifti_tmp").is_dir() or (candidate / "segmentation").is_dir():
                return str(candidate)
        return str(path)

    @staticmethod
    def _normalize_series_name(name):
        norm = name.lower().replace(".nii.gz", "")
        norm = norm.replace("_0000", "")
        norm = re.sub(r"[^a-z0-9]+", "", norm)
        return norm

    @staticmethod
    def _series_key(path):
        name = os.path.basename(path)
        if name.endswith("_0000.nii.gz"):
            return name[:-12]
        if name.endswith(".nii.gz"):
            return name[:-7]
        return os.path.splitext(name)[0]

    @staticmethod
    def _looks_like_label_map(path):
        try:
            import nibabel as nib
            img = nib.load(str(path))
            data = np.asanyarray(img.dataobj)
            if data.size == 0 or not np.issubdtype(data.dtype, np.integer):
                return False
            flat = data.ravel()
            if flat.size > 500_000:
                flat = flat[::max(1, flat.size // 500_000)]
            finite = flat[np.isfinite(flat)]
            if finite.size == 0:
                return False
            unique = np.unique(finite)
            return unique.size <= 32 and float(unique.min()) >= 0.0 and float(unique.max()) <= 32.0
        except Exception:
            return False

    def _filter_scan_files(self, paths):
        ordered = sorted(paths, key=lambda p: (0 if os.path.basename(p).endswith("_0000.nii.gz") else 1, p))
        filtered = [p for p in ordered if not self._looks_like_label_map(p)]
        return filtered or ordered

    def _discover_visualization_files(self, output_dir):
        if not output_dir:
            return [], []
        selected = Path(output_dir).expanduser()
        resolved = Path(self._resolve_output_root(output_dir))
        if resolved.is_dir():
            ct_files = sorted(str(path) for path in (resolved / "nifti_tmp").glob("*.nii.gz")) if (resolved / "nifti_tmp").is_dir() else []
            seg_files = sorted(str(path) for path in (resolved / "segmentation").glob("*.nii.gz")) if (resolved / "segmentation").is_dir() else []
            ct_files = self._filter_scan_files(ct_files)
            if ct_files or seg_files:
                return ct_files, seg_files

        descendant_root = self._find_visualization_tree_root(selected)
        if descendant_root is not None:
            resolved = descendant_root
        ct_dir = resolved / "nifti_tmp"
        seg_dir = resolved / "segmentation"

        if selected.is_dir() and selected.name == "nifti_tmp":
            ct_dir = selected
        elif selected.is_dir() and selected.name == "nnunet_input" and not ct_dir.is_dir():
            ct_dir = selected
        elif selected.is_dir() and selected.name == "segmentation":
            seg_dir = selected

        ct_files = sorted(str(path) for path in ct_dir.glob("*.nii.gz")) if ct_dir.is_dir() else []
        seg_files = sorted(str(path) for path in seg_dir.glob("*.nii.gz")) if seg_dir.is_dir() else []
        ct_files = self._filter_scan_files(ct_files)

        if not ct_files and not seg_files and selected.is_dir():
            direct_files = sorted(str(path) for path in selected.glob("*.nii.gz"))
            if selected.name == "segmentation":
                seg_files = direct_files
            elif selected.name in {"nifti_tmp", "nnunet_input"}:
                ct_files = self._filter_scan_files(direct_files)
        return ct_files, seg_files

    def _find_visualization_tree_root(self, selected_dir: Path) -> Path | None:
        if not selected_dir.exists() or not selected_dir.is_dir():
            return None

        best_root: Path | None = None
        best_score: tuple[int, int] | None = None
        for child in selected_dir.rglob("*"):
            if not child.is_dir():
                continue
            ct_dir = child / "nifti_tmp"
            seg_dir = child / "segmentation"
            has_ct = ct_dir.is_dir() and any(ct_dir.glob("*.nii.gz"))
            has_seg = seg_dir.is_dir() and any(seg_dir.glob("*.nii.gz"))
            if not has_ct and not has_seg:
                continue
            depth = len(child.relative_to(selected_dir).parts)
            score = (0 if (has_ct and has_seg) else 1, depth)
            if best_score is None or score < best_score:
                best_root = child
                best_score = score
                if score[0] == 0 and depth <= 1:
                    break
        return best_root

    def _series_timestamp_map(self):
        dcm_root = self.dcm_input.text().strip()
        if not dcm_root or not os.path.isdir(dcm_root):
            return {}
        try:
            import pydicom
        except Exception:
            return {}

        timestamp_map = {}
        for root, _dirs, files in os.walk(dcm_root):
            dicoms = sorted(
                os.path.join(root, f)
                for f in files
                if not f.startswith(".")
            )
            if not dicoms:
                continue
            sample = None
            for candidate in dicoms[:8]:
                try:
                    ds = pydicom.dcmread(candidate, stop_before_pixels=True, force=True)
                    if getattr(ds, "SeriesInstanceUID", None) or getattr(ds, "SeriesDescription", None):
                        sample = ds
                        break
                except Exception:
                    continue
            if sample is None:
                continue

            date_str = (
                getattr(sample, "AcquisitionDate", "")
                or getattr(sample, "SeriesDate", "")
                or getattr(sample, "StudyDate", "")
            )
            time_str = (
                getattr(sample, "AcquisitionTime", "")
                or getattr(sample, "SeriesTime", "")
                or getattr(sample, "StudyTime", "")
            )
            if not date_str and not time_str:
                continue

            label_parts = []
            if date_str and len(date_str) >= 8:
                label_parts.append(f"{date_str[6:8]}.{date_str[4:6]}.{date_str[:4]}")
            if time_str:
                digits = "".join(ch for ch in str(time_str) if ch.isdigit())
                if len(digits) >= 6:
                    label_parts.append(f"{digits[:2]}:{digits[2:4]}:{digits[4:6]}")
                elif len(digits) >= 4:
                    label_parts.append(f"{digits[:2]}:{digits[2:4]}")
            if not label_parts:
                continue

            timestamp = " ".join(label_parts)
            candidates = {
                self._normalize_series_name(os.path.basename(root)),
                self._normalize_series_name(getattr(sample, "SeriesDescription", "") or ""),
            }
            series_number = getattr(sample, "SeriesNumber", None)
            if series_number is not None:
                candidates.add(self._normalize_series_name(f"{series_number}_{os.path.basename(root)}"))
                candidates.add(self._normalize_series_name(f"{series_number}_{getattr(sample, 'SeriesDescription', '') or ''}"))

            for key in candidates:
                if key:
                    timestamp_map.setdefault(key, timestamp)
        return timestamp_map

    def _series_label(self, path, timestamp_map):
        base = os.path.basename(path)
        key = self._normalize_series_name(base)
        timestamp = timestamp_map.get(key)
        if timestamp:
            return timestamp
        return self._series_key(path)

    def _set_combo_items(self, combo, paths, placeholder):
        current_path = combo.currentData()
        timestamp_map = self._series_timestamp_map()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder, None)
        for path in paths:
            combo.addItem(self._series_label(path, timestamp_map), path)
        if current_path and current_path in paths:
            combo.setCurrentIndex(paths.index(current_path) + 1)
        combo.blockSignals(False)

    def _sync_series_selectors(self, ct_files, seg_files):
        previous_ct = self.ct_series_combo.currentData()
        previous_seg = self.seg_series_combo.currentData()

        self._set_combo_items(self.ct_series_combo, ct_files, "Choose scan")
        self._set_combo_items(self.seg_series_combo, seg_files, "Choose mask")

        if previous_ct in ct_files:
            self.ct_series_combo.setCurrentIndex(ct_files.index(previous_ct) + 1)
        elif ct_files:
            self.ct_series_combo.setCurrentIndex(1)

        selected_ct = self.ct_series_combo.currentData()
        if previous_seg in seg_files:
            self.seg_series_combo.setCurrentIndex(seg_files.index(previous_seg) + 1)
        elif selected_ct:
            selected_key = self._series_key(selected_ct)
            matched_idx = next(
                (i for i, path in enumerate(seg_files) if self._series_key(path) == selected_key),
                None,
            )
            if matched_idx is not None:
                self.seg_series_combo.setCurrentIndex(matched_idx + 1)
            elif seg_files:
                self.seg_series_combo.setCurrentIndex(1)
        elif seg_files:
            self.seg_series_combo.setCurrentIndex(1)

        self.ct_series_combo.setEnabled(bool(ct_files))
        self.seg_series_combo.setEnabled(bool(seg_files))

    def _selected_visualization_files(self):
        ct_path = self.ct_series_combo.currentData()
        seg_path = self.seg_series_combo.currentData()
        return ct_path, seg_path

    def _selected_ct_label(self):
        idx = self.ct_series_combo.currentIndex()
        if idx <= 0:
            return ""
        return self.ct_series_combo.itemText(idx)

    def _set_viewers_loading(self, on, include_2d=True, include_3d=False):
        for viewer in (self.viewer_axial, self.viewer_coronal, self.viewer_sagittal):
            viewer.set_loading(on if include_2d else False)
        self.viewer_3d.set_loading(on if include_3d else False)
        if on:
            self._viewer_pulse_started_at = time.time()

    def _finish_viewer_loading(self, include_2d=True, include_3d=False):
        elapsed_ms = int((time.time() - self._viewer_pulse_started_at) * 1000.0)
        delay_ms = max(0, 450 - elapsed_ms)

        def _stop():
            self._set_viewers_loading(False, include_2d=include_2d, include_3d=include_3d)

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, _stop)
        else:
            _stop()

    def _refresh_visualization_buttons(self, auto_load=False):
        ct_files, seg_files = self._discover_visualization_files(self.output_input.text())
        self._sync_series_selectors(ct_files, seg_files)
        ct_path, seg_path = self._selected_visualization_files()
        self.load_2d_button.setEnabled(bool(ct_path))
        self.load_3d_button.setEnabled(bool(seg_path))
        if ct_files or seg_files:
            self._append_log(f"Files: {len(ct_files)} scans, {len(seg_files)} masks.")
            output_root = self.output_input.text().strip()
            if output_root and output_root != self._last_preview_output:
                self._last_preview_output = output_root
                if auto_load:
                    self._auto_load_viewers()
                else:
                    self._append_log("Viewer mode: auto-load disabled; use Load 2D/Load 3D manually.")

    def _set_viewer_window_preset(self, name: str):
        for viewer in (self.viewer_axial, self.viewer_coronal, self.viewer_sagittal):
            viewer.set_window_preset(name)

    def _set_segmentation_labels(self, seg_path):
        global LABEL_MAP
        if seg_path:
            import nibabel as nib
            values = np.unique(np.asarray(nib.load(seg_path).dataobj))
            LABEL_MAP = label_map_for_values(values, LEGACY_LABEL_MAP)
        else:
            LABEL_MAP = dict(LEGACY_LABEL_MAP)

    def load_overlay_visualization(self):
        ct_path, seg_path = self._selected_visualization_files()
        if not ct_path:
            ct_files, seg_files = self._discover_visualization_files(self.output_input.text())
            ct_path = ct_files[0] if ct_files else None
            if ct_path and not seg_path:
                selected_key = self._series_key(ct_path)
                seg_path = next((path for path in seg_files if self._series_key(path) == selected_key), None)
        if not ct_path:
            self._append_log("2D: no scan found.")
            return
        self._set_segmentation_labels(seg_path)
        self._set_viewers_loading(True, include_2d=True, include_3d=False)

        def _do_load():
            try:
                meta_text = self._selected_ct_label()
                self._append_log(f"2D: loading {os.path.basename(ct_path)}")
                self.viewer_axial.load(ct_path, seg_path)
                self.viewer_coronal.load(ct_path, seg_path)
                self.viewer_sagittal.load(ct_path, seg_path)
                self.viewer_axial.set_meta_text(meta_text)
                self.viewer_coronal.set_meta_text(meta_text)
                self.viewer_sagittal.set_meta_text(meta_text)
            except Exception as e:
                self._append_log(f"2D load error: {e}")
            finally:
                self._finish_viewer_loading(include_2d=True, include_3d=False)

        QTimer.singleShot(self._viewer_pulse_prelude_ms, _do_load)

    def load_3d_visualization(self):
        _ct_path, seg_path = self._selected_visualization_files()
        if not seg_path:
            self._append_log("3D viewer: no segmentation found in output.")
            return
        self._set_segmentation_labels(seg_path)
        self._set_viewers_loading(True, include_2d=False, include_3d=True)

        def _do_load():
            try:
                self._append_log(f"Building 3D reconstruction from {os.path.basename(seg_path)}…")
                self.viewer_3d.load(seg_path)
                self._append_log("3D reconstruction ready.")
            except Exception as e:
                self._append_log(f"3D viewer load error: {e}")
            finally:
                self._finish_viewer_loading(include_2d=False, include_3d=True)

        QTimer.singleShot(self._viewer_pulse_prelude_ms, _do_load)

    def _on_series_selection_changed(self, _index):
        ct_path, seg_path = self._selected_visualization_files()
        self.load_2d_button.setEnabled(bool(ct_path))
        self.load_3d_button.setEnabled(bool(seg_path))


class SplashScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen)
        self.setFixedSize(500, 310)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )
        self._status = "Initialising…"
        self._logos = []
        used_logo_names = set()
        for rel, mw, mh in [
            ("assets/branding/Logo_USZ.png",                   88, 44),
            ("assets/branding/uzh-logo-white.png",             88, 44),
            ("assets/branding/micn_logo_transparent_smooth.png", 76, 44),
            ("Spine/code/Logo_USZ.png",                   88, 44),
            ("Spine/code/uzh-logo-white.png",             88, 44),
            ("Spine/code/micn_logo_transparent_smooth.png", 76, 44),
        ]:
            path = os.path.join(os.path.dirname(__file__), rel)
            key = os.path.basename(path).lower()
            if key in used_logo_names:
                continue
            px = QPixmap(path)
            if not px.isNull():
                used_logo_names.add(key)
                self._logos.append(
                    px.scaled(mw, mh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)

    def set_status(self, text):
        self._status = text
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.016) % 1.0
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background + border
        p.fillRect(0, 0, w, h, QColor(13, 13, 13))
        p.setPen(QPen(QColor(90, 52, 18, 180), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)

        # Top accent line
        ag = QLinearGradient(0, 0, w, 0)
        ag.setColorAt(0.0, QColor(255, 140, 26, 0))
        ag.setColorAt(0.25, QColor(255, 140, 26, 210))
        ag.setColorAt(0.75, QColor(255, 140, 26, 210))
        ag.setColorAt(1.0, QColor(255, 140, 26, 0))
        p.fillRect(0, 0, w, 2, QBrush(ag))

        # Title
        tf = QFont("Arial", 17, QFont.Bold)
        p.setFont(tf)
        p.setPen(QColor(246, 223, 195))
        p.drawText(QRect(0, 28, w, 38), Qt.AlignCenter, "Spine Subregions Segmentation")

        # Subtitle
        sf = QFont("Arial", 9)
        p.setFont(sf)
        p.setPen(QColor(115, 85, 55))
        p.drawText(QRect(0, 66, w, 20), Qt.AlignCenter, "MICNLab · University Hospital Zurich")

        # Logos
        if self._logos:
            logo_h = 44
            logo_y = 106
            gap = 28
            total_w = sum(px.width() for px in self._logos) + gap * (len(self._logos) - 1)
            lx = (w - total_w) // 2
            for px in self._logos:
                p.drawPixmap(lx, logo_y + (logo_h - px.height()) // 2, px)
                lx += px.width() + gap

        # Separator
        sy = 178
        sg = QLinearGradient(40, 0, w - 40, 0)
        sg.setColorAt(0.0, QColor(90, 52, 18, 0))
        sg.setColorAt(0.5, QColor(90, 52, 18, 150))
        sg.setColorAt(1.0, QColor(90, 52, 18, 0))
        p.fillRect(40, sy, w - 80, 1, QBrush(sg))

        # Status text
        p.setFont(QFont("Arial", 8))
        p.setPen(QColor(150, 110, 70))
        p.drawText(QRect(0, 186, w, 20), Qt.AlignCenter, self._status)

        # Pulse bar — taller track, brighter glow so it's clearly visible
        bx, by, bw, bh = 40, h - 22, w - 80, 5
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(40, 25, 10))
        p.drawRoundedRect(bx, by, bw, bh, 2, 2)
        seg = int(bw * 0.40)
        cx = self._phase * (bw + seg) - seg // 2
        dx = max(bx, bx + int(cx - seg // 2))
        dw = min(bx + int(cx + seg // 2), bx + bw) - dx
        if dw > 0:
            # outer glow
            pg2 = QLinearGradient(bx + cx - seg // 2, 0, bx + cx + seg // 2, 0)
            pg2.setColorAt(0.0, QColor(255, 120, 10,  0))
            pg2.setColorAt(0.5, QColor(255, 160, 40, 80))
            pg2.setColorAt(1.0, QColor(255, 120, 10,  0))
            p.setBrush(QBrush(pg2))
            p.drawRoundedRect(dx, by - 3, dw, bh + 6, 3, 3)
            # bright core
            pg = QLinearGradient(bx + cx - seg // 2, 0, bx + cx + seg // 2, 0)
            pg.setColorAt(0.0, QColor(255, 140, 26,   0))
            pg.setColorAt(0.4, QColor(255, 190, 80,  230))
            pg.setColorAt(0.5, QColor(255, 240, 180, 255))
            pg.setColorAt(0.6, QColor(255, 190, 80,  230))
            pg.setColorAt(1.0, QColor(255, 140, 26,   0))
            p.setBrush(QBrush(pg))
            p.drawRoundedRect(dx, by, dw, bh, 2, 2)

        p.end()


if __name__ == "__main__":
    # When the frozen exe is called as a script runner by inference_pipeline.py
    # (e.g. SpineSegmentation _nnunet_runner.py -i ... -o ...) execute the script
    # directly using runpy instead of launching the GUI.
    if getattr(sys, "frozen", False) and len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
        import runpy
        script = sys.argv[1]
        sys.argv = sys.argv[1:]
        runpy.run_path(script, run_name="__main__")
        sys.exit(0)

    app = QApplication(sys.argv)
    if ui_font_family() != "Segoe UI":
        try:
            QFont.insertSubstitution("Segoe UI", ui_font_family())
        except Exception:
            pass
    app.setStyleSheet(DARK_STYLE)

    _smoke = _smoke_test_enabled()
    splash = SplashScreen()
    if not _smoke:
        splash.show()
        app.processEvents()

    splash.set_status("Loading main window…")
    app.processEvents()

    window = MainWindow()

    splash.set_status("Ready.")

    # Keep splash visible for at least 1.8 s so the animation is seen
    _t0 = time.time()
    while time.time() - _t0 < 1.8:
        app.processEvents()
        QTimer.singleShot(0, lambda: None)  # yield to event loop

    window.show()
    splash.close()
    splash.deleteLater()

    if os.environ.get("SPINE_AUTOLOAD") == "1":
        _al_dcm = os.environ.get("SPINE_DCM_DIR", "")
        _al_out = os.environ.get("SPINE_OUT_DIR", "")
    elif os.environ.get("EDH_AUTOLOAD") == "1":
        _al_dcm = os.environ.get("EDH_DCM_DIR", "")
        _al_out = os.environ.get("EDH_OUT_DIR", "")
    else:
        _al_dcm = _al_out = ""
    if _al_dcm or _al_out:
        if _al_dcm:
            window.dcm_input.setText(_al_dcm)
        if _al_out:
            window.output_input.setText(_al_out)
        window._refresh_visualization_buttons(auto_load=True)

    if _smoke:
        _integration_smoke = (
            os.environ.get("SPINE_INTEGRATION_TEST") == "1"
            or os.environ.get("EDH_INTEGRATION_TEST") == "1"
        )

        def _smoke_done():
            sys.stderr.write("SMOKE_DONE success=True message=GUI startup OK\n")
            sys.stderr.flush()
            sys.exit(0)

        def _smoke_run_pipeline():
            sys.stderr.write("SMOKE_INIT GUI startup OK\n")
            sys.stderr.flush()
            try:
                run_pipeline(_al_dcm, _al_out, lambda line: sys.stderr.write(f"{line}\n"))
            except Exception as exc:
                sys.stderr.write(f"SMOKE_DONE success=False message={exc}\n")
                sys.stderr.flush()
                sys.exit(1)
            sys.stderr.write("SMOKE_DONE success=True message=Pipeline completed\n")
            sys.stderr.flush()
            sys.exit(0)

        sys.stderr.write("SMOKE_INIT GUI startup OK\n")
        sys.stderr.flush()
        if _integration_smoke and _al_dcm and _al_out:
            QTimer.singleShot(250, _smoke_run_pipeline)
        else:
            QTimer.singleShot(250, _smoke_done)

    sys.exit(app.exec_())
