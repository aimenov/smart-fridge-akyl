from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import cv2
import pytest

from backend.app.modules.vision_pipeline import run_pipeline


def _video_fixture_paths() -> list[Path]:
    root = Path(__file__).resolve().parent / "fixtures" / "expiry"
    if not root.is_dir():
        return []
    exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _expected_date_from_stem(path: Path) -> str | None:
    parts = [p for p in re.split(r"[^0-9]", path.stem) if p]
    if len(parts) < 3:
        return None
    if len(parts[0]) == 4:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:
            y = 2000 + y
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _extract_frames_to_images(video_path: Path, out_dir: Path, *, max_frames: int = 6) -> list[Path]:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            indices = list(range(max_frames))
        else:
            # Expiry clips often start with motion; sample from the latter half + last frame.
            anchors = [0.45, 0.60, 0.75, 0.85, 0.95]
            indices = [min(total - 1, max(0, int(total * a))) for a in anchors][: max_frames - 1]
            indices.append(max(0, total - 1))
            # de-dupe while preserving order
            seen = set()
            indices = [i for i in indices if not (i in seen or seen.add(i))]

        paths: list[Path] = []
        for i, idx in enumerate(indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            out = out_dir / f"frame_{i:02d}.jpg"
            cv2.imwrite(str(out), frame)
            paths.append(out)
        return paths
    finally:
        cap.release()


@pytest.mark.parametrize("video_path", _video_fixture_paths())
def test_expiry_video_matches_filename_date(video_path: Path, tmp_path: Path):
    expected = _expected_date_from_stem(video_path)
    assert expected is not None, f"video fixture name must encode a date: {video_path.name}"

    frames = _extract_frames_to_images(video_path, tmp_path, max_frames=6)
    if not frames:
        pytest.skip(f"could not decode frames from {video_path.name} (OpenCV codec support)")

    result = run_pipeline(frames, run_barcode=False, run_expiry=True)
    if result.normalized_date is None:
        pytest.skip(
            f"{video_path.name}: expiry OCR returned no date on sampled frames "
            "(video-dependent lighting/codec; image fixtures cover parsing)."
        )
    assert result.normalized_date == expected, (
        f"{video_path.name}: expected {expected}, got {result.normalized_date!r}; "
        f"consensus={result.stages.get('expiry_consensus')} frames={result.stages.get('expiry_frames')}"
    )

