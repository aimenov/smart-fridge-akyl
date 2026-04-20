from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from backend.app.modules.barcode_gtin import normalize_barcode_to_gtin14
from backend.app.modules.vision_pipeline import run_pipeline


def _video_fixture_paths() -> list[Path]:
    root = Path(__file__).resolve().parent / "fixtures" / "barcode"
    if not root.is_dir():
        return []
    exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _expected_gtin14_from_stem(path: Path) -> str | None:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    if len(digits) < 8:
        return None
    gn = normalize_barcode_to_gtin14(digits)
    return gn.normalized_gtin_14


def _extract_frames_to_images(video_path: Path, out_dir: Path, *, max_frames: int = 7) -> list[Path]:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            # Fallback: just read sequentially.
            indices = list(range(max_frames))
        else:
            step = max(1, total // max_frames)
            indices = list(range(0, total, step))[:max_frames]

        paths: list[Path] = []
        for i, idx in enumerate(indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            out = out_dir / f"frame_{i:02d}.jpg"
            # OpenCV encodes BGR->JPEG
            cv2.imwrite(str(out), frame)
            paths.append(out)
        return paths
    finally:
        cap.release()


@pytest.mark.parametrize("video_path", _video_fixture_paths())
def test_barcode_video_matches_filename_gtin(video_path: Path, tmp_path: Path):
    expected = _expected_gtin14_from_stem(video_path)
    assert expected is not None, f"video fixture name must encode >=8 digits: {video_path.name}"

    frames = _extract_frames_to_images(video_path, tmp_path, max_frames=7)
    assert frames, f"could not read frames from {video_path.name}"

    result = run_pipeline(frames, run_expiry=False)
    assert result.normalized_gtin_14 == expected, (
        f"{video_path.name}: expected normalized GTIN-14 {expected}, got {result.normalized_gtin_14!r}; "
        f"consensus={result.stages.get('barcode_consensus')} decoded={result.stages.get('barcodes_decoded')}"
    )

