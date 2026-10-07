import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import nibabel as nib
import numpy as np
from PyQt5.QtWidgets import QApplication, QLabel
from mask_legend import MaskLegend


def test_legend_tracks_mask_and_palette(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "mask.nii.gz"
    nib.save(nib.Nifti1Image(np.array([[[1, 1, 2]]], dtype=np.uint8), np.eye(4)), path)
    legend = MaskLegend()
    palette = {1: ("Tumor", (255, 214, 64, 230)), 2: ("Edema", (255, 48, 96, 230)), 3: ("Other", (0, 100, 200, 255))}
    legend.set_mask(path, palette)
    assert "255, 214, 64" in legend.findChild(QLabel, "legendSwatch1").styleSheet()
    assert legend.findChild(QLabel, "legendName3").text().endswith("(absent)")
    palette[1] = ("Vessel", (1, 2, 3, 255))
    legend.set_mask(path, palette)
    assert legend.findChild(QLabel, "legendName1").text() == "1 · Vessel"
    assert "1, 2, 3" in legend.findChild(QLabel, "legendSwatch1").styleSheet()
    legend.set_mask(None, palette)
    assert not legend.entries
