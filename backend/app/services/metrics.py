from __future__ import annotations

import math
from io import BytesIO

import numpy as np
from PIL import Image
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from app.core.config import settings


def _load_rgb(image_bytes: bytes) -> np.ndarray:
    with Image.open(BytesIO(image_bytes)) as image:
        rgb = image.convert('RGB')
        return np.asarray(rgb)


def _crop_to_common_size(original: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height = min(original.shape[0], candidate.shape[0])
    width = min(original.shape[1], candidate.shape[1])
    return original[:height, :width], candidate[:height, :width]


def _downscale_if_needed(original: np.ndarray, candidate: np.ndarray, max_dimension: int) -> tuple[np.ndarray, np.ndarray]:
    if max_dimension <= 0:
        return original, candidate

    current_max_dimension = max(original.shape[0], original.shape[1], candidate.shape[0], candidate.shape[1])
    if current_max_dimension <= max_dimension:
        return original, candidate

    scale = max_dimension / current_max_dimension
    target_size = (
        max(1, int(round(original.shape[1] * scale))),
        max(1, int(round(original.shape[0] * scale))),
    )

    original_image = Image.fromarray(original)
    candidate_image = Image.fromarray(candidate)
    resample = Image.Resampling.LANCZOS
    original_small = np.asarray(original_image.resize(target_size, resample=resample))
    candidate_small = np.asarray(candidate_image.resize(target_size, resample=resample))
    return original_small, candidate_small


def compute_metrics(original_bytes: bytes, candidate_bytes: bytes, max_dimension: int | None = None) -> tuple[float, float]:
    original = _load_rgb(original_bytes)
    candidate = _load_rgb(candidate_bytes)

    original, candidate = _crop_to_common_size(original, candidate)
    original, candidate = _downscale_if_needed(
        original,
        candidate,
        max_dimension=max_dimension or settings.metrics_max_dimension,
    )

    ssim = float(structural_similarity(original, candidate, channel_axis=2))
    if np.array_equal(original, candidate):
        psnr = 99.0
    else:
        psnr = float(peak_signal_noise_ratio(original, candidate, data_range=255))
        if math.isinf(psnr):
            psnr = 99.0
    return ssim, psnr
