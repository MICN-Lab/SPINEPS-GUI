from pathlib import Path

import nibabel as nib
import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget


class MaskLegend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('maskLegend')
        self.entries = {}
        self._cache = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        self.title = QLabel('Mask legend · 2D / 3D')
        self.title.setStyleSheet('font-weight: bold; font-size: 10px;')
        layout.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(34)
        self.scroll.setMaximumHeight(170)
        layout.addWidget(self.scroll)
        self.set_labels({})

    def set_labels(self, mapping, counts=None):
        self.entries = {int(key): (name, tuple(color)) for key, (name, color) in mapping.items()}
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 4, 0)
        rows.setSpacing(4)
        if not self.entries:
            label = QLabel('No mask loaded')
            label.setWordWrap(True)
            rows.addWidget(label)
        for value, (name, color) in sorted(self.entries.items()):
            row = QWidget()
            row.setObjectName(f'legendLabel{value}')
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            swatch = QLabel()
            swatch.setObjectName(f'legendSwatch{value}')
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(f'background: rgb({color[0]}, {color[1]}, {color[2]}); border: 1px solid #aaa; border-radius: 2px;')
            layout.addWidget(swatch, 0, Qt.AlignTop)
            text = f'{value} · {name}'
            if counts is not None and not counts.get(value, 0):
                text += ' (absent)'
            label = QLabel(text)
            label.setObjectName(f'legendName{value}')
            label.setWordWrap(True)
            label.setStyleSheet('font-size: 10px;')
            layout.addWidget(label, 1)
            row.setToolTip(f'Label {value}: {name}' + (f' · {counts.get(value, 0):,} voxels' if counts is not None else ''))
            rows.addWidget(row)
        rows.addStretch(1)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(content)
        self.scroll.setMaximumHeight(min(170, max(34, len(self.entries) * 30)))

    def set_mask(self, path, mapping):
        if not path:
            self._cache = None
            self.set_labels({})
            return
        source = Path(path)
        key = (str(source.resolve()), source.stat().st_mtime_ns, repr(mapping))
        if key == self._cache:
            return
        values, frequencies = np.unique(np.asarray(nib.load(source).dataobj), return_counts=True)
        counts = {int(value): int(count) for value, count in zip(values, frequencies) if value}
        self.set_labels(mapping, counts)
        self._cache = key
