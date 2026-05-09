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


def _extract_frames_to_images(video_path: Path, out_dir: Path, *, max_frames: int = 8) -> list[Path]:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            indices = list(range(max_frames))
        else:
            # Clips often start out of focus; prefer the latter half + last frame (same idea as expiry fixtures).
            anchors = [0.45, 0.52, 0.60, 0.68, 0.75, 0.82, 0.88, 0.93, 0.97]
            take = max(1, max_frames - 1)
            step = max(1, len(anchors) // take)
            picked = anchors[::step][:take]
            indices = [min(total - 1, max(0, int(total * a))) for a in picked]
            indices.append(max(0, total - 1))
            seen: set[int] = set()
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
def test_barcode_video_matches_filename_gtin(video_path: Path, tmp_path: Path):
    expected = _expected_gtin14_from_stem(video_path)
    assert expected is not None, f"video fixture name must encode >=8 digits: {video_path.name}"

    frames = _extract_frames_to_images(video_path, tmp_path, max_frames=8)
    assert frames, f"could not read frames from {video_path.name}"

    result = run_pipeline(frames)
    assert result.normalized_gtin_14 == expected, (
        f"{video_path.name}: expected normalized GTIN-14 {expected}, got {result.normalized_gtin_14!r}; "
        f"consensus={result.stages.get('barcode_consensus')} decoded={result.stages.get('barcodes_decoded')}"
    )

