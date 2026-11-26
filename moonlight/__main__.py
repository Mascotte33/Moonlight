"""``python -m moonlight`` -> the GUI (:func:`moonlight.gui.app.main`)."""

import os

# One worker thread per math library: the frames are small, and idle pool
# threads spinning on every core made the PC sluggish.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import cv2  # noqa: E402

cv2.setNumThreads(1)

from moonlight.gui.app import main  # noqa: E402

raise SystemExit(main())
