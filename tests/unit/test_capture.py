"""Unit tests for moonlight.capture.

All tests are platform-independent: Win32 window APIs, ``mss`` and ``dxcam``
are mocked so the suite runs in CI (Linux) without a display, a Tibia window,
or the optional dxcam package. The one behaviour that is genuinely
platform-specific (dxcam availability) is guarded with ``skipif``.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from moonlight.capture import CaptureError, ScreenCapture, WindowNotFoundError

_IS_WINDOWS = sys.platform == "win32"


class TestConstruction:
    """Backend selection and construction validation."""

    def test_default_backend_is_mss(self) -> None:
        assert ScreenCapture().backend == "mss"

    def test_custom_window_title(self) -> None:
        assert ScreenCapture(window_title="My Game").window_title == "My Game"

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(CaptureError):
            ScreenCapture(backend="bogus")

    @pytest.mark.skipif(_IS_WINDOWS, reason="dxcam is allowed on Windows")
    def test_dxcam_backend_rejected_off_windows(self) -> None:
        with pytest.raises(CaptureError):
            ScreenCapture(backend="dxcam")

    @pytest.mark.skipif(not _IS_WINDOWS, reason="dxcam backend only on Windows")
    def test_dxcam_backend_allowed_on_windows(self) -> None:
        assert ScreenCapture(backend="dxcam").backend == "dxcam"


class TestCaptureRegion:
    """Low-level region capture and BGRA -> RGB conversion."""

    def setup_method(self) -> None:
        self.cap = ScreenCapture()

    def _mock_mss(self, bgra: np.ndarray) -> None:
        import unittest.mock as mock

        fake = mock.MagicMock()
        fake.grab.return_value = bgra
        self.cap._sct = fake

    def test_returns_rgb_shape(self) -> None:
        self._mock_mss(np.zeros((48, 64, 4), dtype=np.uint8))
        frame = self.cap.capture_region(0, 0, 64, 48)
        assert frame.shape == (48, 64, 3)
        assert frame.dtype == np.uint8

    def test_bgra_to_rgb_channel_order(self) -> None:
        bgra = np.zeros((4, 4, 4), dtype=np.uint8)
        bgra[0, 0] = (10, 20, 30, 255)  # B, G, R, A
        self._mock_mss(bgra)
        frame = self.cap.capture_region(0, 0, 4, 4)
        # RGB should be R=30, G=20, B=10 (not the input BGR order).
        assert frame[0, 0].tolist() == [30, 20, 10]

    def test_region_passed_to_mss(self) -> None:
        import unittest.mock as mock

        fake = mock.MagicMock()
        fake.grab.return_value = np.zeros((2, 2, 4), dtype=np.uint8)
        self.cap._sct = fake
        self.cap.capture_region(5, 7, 64, 48)
        fake.grab.assert_called_once_with(
            {"left": 5, "top": 7, "width": 64, "height": 48}
        )

    @pytest.mark.parametrize(
        "x, y, w, h",
        [(0, 0, 0, 10), (0, 0, 10, 0), (0, 0, -1, 10), (0, 0, 10, -5)],
    )
    def test_invalid_region_raises(self, x: int, y: int, w: int, h: int) -> None:
        with pytest.raises(CaptureError):
            self.cap.capture_region(x, y, w, h)


class TestGetFrame:
    """High-level get_frame: window lookup, fallback, and errors."""

    def test_uses_last_rect_when_window_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import unittest.mock as mock

        cap = ScreenCapture()
        monkeypatch.setattr(cap, "get_window_rect", mock.MagicMock(return_value=None))
        cap._last_rect = (10, 20, 100, 50)
        mock_frame = np.zeros((50, 100, 3), dtype=np.uint8)
        mock_capture = mock.MagicMock(return_value=mock_frame)
        monkeypatch.setattr(cap, "capture_region", mock_capture)
        frame = cap.get_frame()
        assert frame.shape == (50, 100, 3)
        mock_capture.assert_called_once_with(10, 20, 100, 50)

    def test_raises_when_window_never_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = ScreenCapture()
        monkeypatch.setattr(cap, "get_window_rect", lambda: None)
        cap._last_rect = None
        with pytest.raises(WindowNotFoundError):
            cap.get_frame()

    def test_updates_stats_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = ScreenCapture()
        monkeypatch.setattr(cap, "get_window_rect", lambda: None)
        cap._last_rect = (0, 0, 100, 50)
        monkeypatch.setattr(
            cap,
            "capture_region",
            lambda x, y, w, h: np.zeros((50, 100, 3), dtype=np.uint8),
        )
        cap.get_frame()
        stats = cap.get_stats()
        assert stats["frames_captured"] == 1
        assert stats["new_frames"] == 1
        assert stats["window_found"] is False


class TestDxcamStaleFrame:
    """dxcam 'no new frame' handling: reuse the last captured frame.

    We drive ``_grab_dxcam`` with a mocked camera so this runs anywhere,
    without dxcam installed.
    """

    def _cap_with_cam(self, monkeypatch: pytest.MonkeyPatch, cam: object) -> ScreenCapture:
        cap = ScreenCapture()
        monkeypatch.setattr(cap, "_load_dxcam", lambda: "dummy_mod")
        monkeypatch.setattr(cap, "_dxcam_cam", cam)
        return cap

    def test_new_frame_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import unittest.mock as mock

        cam = mock.MagicMock()
        frame1 = np.zeros((100, 200, 3), dtype=np.uint8)
        cam.grab.return_value = frame1
        cap = self._cap_with_cam(monkeypatch, cam)

        frame, is_new = cap._grab_dxcam((0, 0, 200, 100))
        assert is_new is True
        assert frame is cap._last_frame
        # dxcam region is (left, top, right, bottom).
        cam.grab.assert_called_once_with(region=(0, 0, 200, 100), new_frame_only=True)

    def test_reuses_last_frame_when_none_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import unittest.mock as mock

        cam = mock.MagicMock()
        frame1 = np.zeros((100, 200, 3), dtype=np.uint8)
        frame1[0, 0] = (1, 2, 3)
        cam.grab.side_effect = [frame1, None, None]
        cap = self._cap_with_cam(monkeypatch, cam)

        f1, n1 = cap._grab_dxcam((0, 0, 200, 100))
        f2, n2 = cap._grab_dxcam((0, 0, 200, 100))
        f3, n3 = cap._grab_dxcam((0, 0, 200, 100))
        assert n1 is True
        assert n2 is False
        assert n3 is False
        # Stale frames are the same array as the first capture.
        assert f2 is f1
        assert f3 is f1

    def test_no_frame_ever_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import unittest.mock as mock

        cam = mock.MagicMock()
        cam.grab.return_value = None
        cap = self._cap_with_cam(monkeypatch, cam)
        with pytest.raises(CaptureError):
            cap._grab_dxcam((0, 0, 200, 100))


class TestStats:
    """get_stats() structure and update behaviour."""

    def test_initial_stats(self) -> None:
        cap = ScreenCapture()
        stats = cap.get_stats()
        assert stats["backend"] == "mss"
        assert stats["frames_captured"] == 0
        assert stats["fps"] == 0.0
        assert stats["avg_frame_time_ms"] == 0.0
        assert stats["last_frame_time_ms"] is None
        assert stats["window_found"] is False
        assert stats["window_rect"] is None

    def test_fps_positive_over_multiple_frames(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        cap = ScreenCapture()
        monkeypatch.setattr(cap, "get_window_rect", lambda: None)
        cap._last_rect = (0, 0, 32, 32)
        monkeypatch.setattr(
            cap,
            "capture_region",
            lambda x, y, w, h: np.zeros((32, 32, 3), dtype=np.uint8),
        )
        for _ in range(5):
            cap.get_frame()
            time.sleep(0.005)
        stats = cap.get_stats()
        assert stats["frames_captured"] == 5
        fps = stats["fps"]
        assert isinstance(fps, (int, float))
        assert fps > 0.0


class TestLifecycle:
    """Context manager and close() semantics."""

    def test_context_manager_closes(self) -> None:
        with ScreenCapture() as cap:
            pass
        assert cap._closed is True

    def test_close_is_idempotent(self) -> None:
        cap = ScreenCapture()
        cap.close()
        cap.close()  # must not raise
        assert cap._closed is True

    def test_get_frame_after_close_raises(self) -> None:
        cap = ScreenCapture()
        cap.close()
        with pytest.raises(CaptureError):
            cap.get_frame()
