# Linux source validation

The source is extracted from the locally tested toolkit, with its existing SPINEPS inference and DICOM conversion logic preserved. No medical data or pretrained weights are included.

Validated runtime: Python 3.10, SPINEPS 2.1.2, PyTorch 2.7.1 + CUDA 12.8, NVIDIA RTX PRO 6000 Blackwell, Ubuntu 25.10.

On 2026-10-07, the toolkit processed an explicit lumbar CT DICOM series (405 slices), completed semantic and vertebra instance segmentation, saved centroid metadata, and produced a nonempty GUI overlay. The standalone source startup and integration checks are recorded below after validation.

This establishes startup, conversion, inference, output geometry, and parity with the toolkit integration. It does not establish clinical segmentation accuracy or validate other operating systems.

To check startup without a visible desktop:

```bash
QT_QPA_PLATFORM=offscreen SPINE_SMOKE_TEST=1 bash run_linux.sh
```

To check the complete GUI pipeline on an explicit CT series or NIfTI file:

```bash
QT_QPA_PLATFORM=offscreen SPINE_SMOKE_TEST=1 SPINE_INTEGRATION_TEST=1 \
  SPINE_AUTOLOAD=1 SPINE_DCM_DIR=/path/to/one/ct-series \
  SPINE_OUT_DIR=/path/to/new/output bash run_linux.sh
```

Use a new output folder to exercise inference rather than cached results.

Standalone validation on 2026-10-07: GUI startup smoke passed; a real lumbar CT NIfTI input completed all three SPINEPS model stages in 88 seconds. Output shape and affine match the toolkit result. The 18 native SPINEPS foreground labels have minimum Dice 0.9998907193; voxel-level differences are consistent with repeated GPU execution. The environment reports no broken requirements (`pip check`).

The source viewer now selects colors and names from native SPINEPS label IDs (including 41–53, sacral structures, and 100 for discs), rather than displaying only the legacy 1–7 subregion labels. Legacy masks retain their original names and colors.
