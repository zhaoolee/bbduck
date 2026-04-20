from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image, ImageSequence
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from app.core.config import settings


WHITE_RGBA = (255, 255, 255, 255)
BLACK_RGBA = (0, 0, 0, 255)


@dataclass
class MetricBundle:
    ssim: float
    psnr: float


@dataclass
class AnimationDiagnostics:
    frame_count_equal: bool
    loop_equal: bool
    duration_equal: bool
    disposal_equal: bool
    sampled_frame_ssim: float
    sampled_frame_psnr: float


@dataclass
class CandidateMetrics:
    ssim: float
    psnr: float
    dimensions_equal: bool
    alpha_safe: bool
    animation_safe: bool
    alpha_metrics: MetricBundle | None = None
    composited_white_metrics: MetricBundle | None = None
    composited_black_metrics: MetricBundle | None = None
    animation_diagnostics: AnimationDiagnostics | None = None


@dataclass
class ImageAnalysis:
    rgb: np.ndarray
    rgba: np.ndarray
    alpha: np.ndarray | None
    size: tuple[int, int]
    has_alpha: bool
    frame_count: int
    loop: int
    durations: list[int]
    disposals: list[int]
    sampled_rgba_frames: list[np.ndarray]



def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {'RGBA', 'LA'} or (image.mode == 'P' and 'transparency' in image.info)



def _load_rgb(image_bytes: bytes) -> np.ndarray:
    with Image.open(BytesIO(image_bytes)) as image:
        return np.asarray(image.convert('RGB'))



def _crop_to_common_size(original: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    height = min(original.shape[0], candidate.shape[0])
    width = min(original.shape[1], candidate.shape[1])
    if original.ndim == 2:
        return original[:height, :width], candidate[:height, :width]
    return original[:height, :width, ...], candidate[:height, :width, ...]



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
    resample = Image.Resampling.LANCZOS

    def resize(array: np.ndarray) -> np.ndarray:
        return np.asarray(Image.fromarray(array).resize(target_size, resample=resample))

    return resize(original), resize(candidate)



def _compare_arrays(
    original: np.ndarray,
    candidate: np.ndarray,
    *,
    max_dimension: int,
    allow_crop: bool,
) -> MetricBundle:
    if original.shape != candidate.shape:
        if not allow_crop:
            raise ValueError('dimension mismatch')
        original, candidate = _crop_to_common_size(original, candidate)

    original, candidate = _downscale_if_needed(original, candidate, max_dimension=max_dimension)
    channel_axis = None if original.ndim == 2 else 2
    ssim = float(structural_similarity(original, candidate, channel_axis=channel_axis, data_range=255))
    if np.array_equal(original, candidate):
        psnr = 99.0
    else:
        psnr = float(peak_signal_noise_ratio(original, candidate, data_range=255))
        if math.isinf(psnr):
            psnr = 99.0
    return MetricBundle(ssim=ssim, psnr=psnr)



def _sample_indexes(frame_count: int, max_samples: int = 5) -> list[int]:
    if frame_count <= 1:
        return [0]
    if frame_count <= max_samples:
        return list(range(frame_count))
    step = (frame_count - 1) / (max_samples - 1)
    return sorted({min(frame_count - 1, int(round(step * index))) for index in range(max_samples)})



def _load_analysis(image_bytes: bytes) -> ImageAnalysis:
    with Image.open(BytesIO(image_bytes)) as image:
        rgba = np.asarray(image.convert('RGBA'))
        rgb = np.asarray(image.convert('RGB'))
        has_alpha = _has_alpha(image)
        alpha = np.asarray(image.getchannel('A')) if has_alpha else None
        frame_count = getattr(image, 'n_frames', 1)
        loop = int(image.info.get('loop', 0))
        durations: list[int] = []
        disposals: list[int] = []
        sampled_rgba_frames: list[np.ndarray] = []
        for frame_index in _sample_indexes(frame_count):
            image.seek(frame_index)
            frame = image.copy()
            durations.append(int(frame.info.get('duration', image.info.get('duration', 0) or 0)))
            disposals.append(int(getattr(frame, 'disposal_method', frame.info.get('disposal', 0) or 0)))
            sampled_rgba_frames.append(np.asarray(frame.convert('RGBA')))
        image.seek(0)
        return ImageAnalysis(
            rgb=rgb,
            rgba=rgba,
            alpha=alpha,
            size=image.size,
            has_alpha=has_alpha,
            frame_count=frame_count,
            loop=loop,
            durations=durations,
            disposals=disposals,
            sampled_rgba_frames=sampled_rgba_frames,
        )



def _composite_rgba(rgba: np.ndarray, background: tuple[int, int, int, int]) -> np.ndarray:
    image = Image.fromarray(rgba)
    composite = Image.alpha_composite(Image.new('RGBA', image.size, background), image)
    return np.asarray(composite.convert('RGB'))



def compute_metrics(original_bytes: bytes, candidate_bytes: bytes, max_dimension: int | None = None) -> tuple[float, float]:
    effective_max_dimension = settings.metrics_max_dimension if max_dimension is None else max_dimension
    metrics = _compare_arrays(
        _load_rgb(original_bytes),
        _load_rgb(candidate_bytes),
        max_dimension=effective_max_dimension,
        allow_crop=True,
    )
    return metrics.ssim, metrics.psnr



def compute_alpha_metrics(original_bytes: bytes, candidate_bytes: bytes, max_dimension: int | None = None) -> tuple[float, float] | None:
    effective_max_dimension = settings.metrics_max_dimension if max_dimension is None else max_dimension
    original = _load_analysis(original_bytes)
    candidate = _load_analysis(candidate_bytes)
    if original.alpha is None and candidate.alpha is None:
        return None
    original_alpha = original.alpha if original.alpha is not None else np.full(original.rgb.shape[:2], 255, dtype=np.uint8)
    candidate_alpha = candidate.alpha if candidate.alpha is not None else np.full(candidate.rgb.shape[:2], 255, dtype=np.uint8)
    metrics = _compare_arrays(original_alpha, candidate_alpha, max_dimension=effective_max_dimension, allow_crop=True)
    return metrics.ssim, metrics.psnr



def compute_composited_metrics(
    original_bytes: bytes,
    candidate_bytes: bytes,
    background: tuple[int, int, int, int] = WHITE_RGBA,
    max_dimension: int | None = None,
) -> tuple[float, float]:
    effective_max_dimension = settings.metrics_max_dimension if max_dimension is None else max_dimension
    original = _load_analysis(original_bytes)
    candidate = _load_analysis(candidate_bytes)
    metrics = _compare_arrays(
        _composite_rgba(original.rgba, background),
        _composite_rgba(candidate.rgba, background),
        max_dimension=effective_max_dimension,
        allow_crop=True,
    )
    return metrics.ssim, metrics.psnr


class MetricEvaluator:
    def __init__(self, original_bytes: bytes, max_dimension: int | None = None):
        self.original_bytes = original_bytes
        self._original_analysis = _load_analysis(original_bytes)
        self._original_rgb = self._original_analysis.rgb
        self._max_dimension = settings.metrics_max_dimension if max_dimension is None else max_dimension

    def compute(self, candidate_bytes: bytes) -> tuple[float, float]:
        metrics = _compare_arrays(
            self._original_rgb,
            _load_rgb(candidate_bytes),
            max_dimension=self._max_dimension,
            allow_crop=True,
        )
        return metrics.ssim, metrics.psnr

    def evaluate(self, candidate_bytes: bytes, *, full_resolution: bool = False, require_same_dimensions: bool = False) -> CandidateMetrics:
        max_dimension = 0 if full_resolution else self._max_dimension
        candidate = _load_analysis(candidate_bytes)
        dimensions_equal = self._original_analysis.size == candidate.size
        allow_crop = not require_same_dimensions

        if require_same_dimensions and not dimensions_equal:
            return CandidateMetrics(
                ssim=0.0,
                psnr=0.0,
                dimensions_equal=False,
                alpha_safe=False,
                animation_safe=False,
            )

        visual_metrics = _compare_arrays(
            self._original_analysis.rgb,
            candidate.rgb,
            max_dimension=max_dimension,
            allow_crop=allow_crop,
        )

        alpha_metrics: MetricBundle | None = None
        composited_white_metrics: MetricBundle | None = None
        composited_black_metrics: MetricBundle | None = None
        alpha_safe = True
        if self._original_analysis.has_alpha or candidate.has_alpha:
            original_alpha = self._original_analysis.alpha if self._original_analysis.alpha is not None else np.full(self._original_analysis.rgb.shape[:2], 255, dtype=np.uint8)
            candidate_alpha = candidate.alpha if candidate.alpha is not None else np.full(candidate.rgb.shape[:2], 255, dtype=np.uint8)
            alpha_metrics = _compare_arrays(
                original_alpha,
                candidate_alpha,
                max_dimension=max_dimension,
                allow_crop=allow_crop,
            )
            composited_white_metrics = _compare_arrays(
                _composite_rgba(self._original_analysis.rgba, WHITE_RGBA),
                _composite_rgba(candidate.rgba, WHITE_RGBA),
                max_dimension=max_dimension,
                allow_crop=allow_crop,
            )
            composited_black_metrics = _compare_arrays(
                _composite_rgba(self._original_analysis.rgba, BLACK_RGBA),
                _composite_rgba(candidate.rgba, BLACK_RGBA),
                max_dimension=max_dimension,
                allow_crop=allow_crop,
            )
            alpha_safe = (
                dimensions_equal
                and alpha_metrics.ssim >= 0.999
                and alpha_metrics.psnr >= 50.0
                and composited_white_metrics.ssim >= 0.995
                and composited_black_metrics.ssim >= 0.995
            )

        animation_diagnostics: AnimationDiagnostics | None = None
        animation_safe = True
        if self._original_analysis.frame_count > 1 or candidate.frame_count > 1:
            frame_count_equal = self._original_analysis.frame_count == candidate.frame_count
            loop_equal = self._original_analysis.loop == candidate.loop
            duration_equal = self._original_analysis.durations == candidate.durations
            disposal_equal = self._original_analysis.disposals == candidate.disposals
            sampled_frame_ssim = 0.0
            sampled_frame_psnr = 0.0
            if frame_count_equal:
                frame_metrics = [
                    _compare_arrays(
                        original_frame,
                        candidate_frame,
                        max_dimension=max_dimension,
                        allow_crop=allow_crop,
                    )
                    for original_frame, candidate_frame in zip(
                        self._original_analysis.sampled_rgba_frames,
                        candidate.sampled_rgba_frames,
                        strict=True,
                    )
                ]
                sampled_frame_ssim = min(metric.ssim for metric in frame_metrics)
                sampled_frame_psnr = min(metric.psnr for metric in frame_metrics)
            animation_diagnostics = AnimationDiagnostics(
                frame_count_equal=frame_count_equal,
                loop_equal=loop_equal,
                duration_equal=duration_equal,
                disposal_equal=disposal_equal,
                sampled_frame_ssim=sampled_frame_ssim,
                sampled_frame_psnr=sampled_frame_psnr,
            )
            animation_safe = all((frame_count_equal, loop_equal, duration_equal, disposal_equal))
            if frame_count_equal:
                animation_safe = animation_safe and sampled_frame_ssim >= 0.99 and sampled_frame_psnr >= 40.0
            visual_metrics = MetricBundle(
                ssim=min(visual_metrics.ssim, sampled_frame_ssim) if frame_count_equal else 0.0,
                psnr=min(visual_metrics.psnr, sampled_frame_psnr) if frame_count_equal else 0.0,
            )

        return CandidateMetrics(
            ssim=visual_metrics.ssim,
            psnr=visual_metrics.psnr,
            dimensions_equal=dimensions_equal,
            alpha_safe=alpha_safe,
            animation_safe=animation_safe,
            alpha_metrics=alpha_metrics,
            composited_white_metrics=composited_white_metrics,
            composited_black_metrics=composited_black_metrics,
            animation_diagnostics=animation_diagnostics,
        )
