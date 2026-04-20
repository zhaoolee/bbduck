from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from shutil import which
from uuid import uuid4

from PIL import Image, ImageSequence, PngImagePlugin

from app.core.config import settings
from app.schemas import CompressionItem, CompressionMetrics
from app.services.metrics import CandidateMetrics, MetricEvaluator


@dataclass
class CompressionCandidate:
    algorithm: str
    payload: bytes
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass
class CandidateAssessment:
    candidate: CompressionCandidate
    payload_size: int = 0
    compression_ratio: float = 0.0
    ssim: float = 1.0
    psnr: float = 99.0
    accepted: bool = True
    rejection_reason: str | None = None
    quality_tier: str = 'lossless'
    is_lossless_like: bool = True
    alpha_safe: bool = True
    animation_safe: bool = True
    dimensions_safe: bool = True


class CompressionService:
    """图片压缩服务。

    优先尝试真实外部压缩器：
    - JPEG: cjpeg / jpegtran
    - PNG: pngquant / zopflipng
    - WebP: cwebp
    - GIF: gifsicle

    外部工具不可用时自动回退到 Pillow，保证本地开发也能跑通。
    """

    candidate_type = CompressionCandidate
    assessment_type = CandidateAssessment

    def __init__(self) -> None:
        self.last_assessments: list[CandidateAssessment] = []
        self._tool_identity_cache: dict[str, str] = {}

    def compress_bytes(self, file_name: str, payload: bytes) -> CompressionItem:
        suffix = Path(file_name).suffix.lower().lstrip('.')
        if suffix not in settings.allowed_suffixes:
            raise ValueError(f'Unsupported file type: {suffix}')

        safe_file_name = self._sanitize_upload_file_name(file_name)
        original_path = settings.upload_dir / f'{uuid4().hex}-{safe_file_name}'
        original_path.write_bytes(payload)

        candidates = self._build_candidates(file_name=safe_file_name, payload=payload, suffix=suffix)
        chosen = self._choose_candidate(payload, candidates, suffix=suffix)

        compressed_name = f'{original_path.stem}.compressed.{self._target_extension(suffix, chosen.candidate.algorithm)}'
        compressed_path = settings.output_dir / compressed_name
        compressed_path.write_bytes(chosen.candidate.payload)

        ratio = round(100 * (1 - (len(chosen.candidate.payload) / max(len(payload), 1))), 2)

        return CompressionItem(
            file_name=file_name,
            original_size=len(payload),
            compressed_size=len(chosen.candidate.payload),
            original_url=f'/api/files/{original_path.name}?kind=upload',
            compressed_url=f'/api/files/{compressed_path.name}?kind=output',
            mime_type=self._mime_from_algorithm_or_suffix(chosen.candidate.algorithm, suffix),
            status='skipped' if chosen.candidate.algorithm == 'passthrough' else 'completed',
            algorithm=chosen.candidate.algorithm,
            metrics=CompressionMetrics(
                compression_ratio=ratio,
                ssim=round(chosen.ssim, 4),
                psnr=round(chosen.psnr, 2),
            ),
        )

    def get_available_toolchain(self) -> dict[str, list[str]]:
        tools = {
            'jpeg': [],
            'png': [],
            'webp': [],
            'gif': [],
        }

        if which('cjpeg'):
            tools['jpeg'].append('cjpeg')
        if which('jpegtran'):
            tools['jpeg'].append('jpegtran')
        if which('pngquant'):
            tools['png'].append('pngquant')
        if which('zopflipng'):
            tools['png'].append('zopflipng')
        if which('cwebp'):
            tools['webp'].append('cwebp')
        if which('gifsicle'):
            tools['gif'].append('gifsicle')

        return tools

    def _profile(self) -> str:
        return settings.compression_profile

    def _choose_candidate(
        self,
        original_payload: bytes,
        candidates: list[CompressionCandidate],
        suffix: str | None = None,
    ) -> CandidateAssessment:
        profile = self._profile()
        evaluator: MetricEvaluator | None = None

        def get_evaluator() -> MetricEvaluator:
            nonlocal evaluator
            if evaluator is None:
                evaluator = MetricEvaluator(original_payload)
            return evaluator

        assessments = [self._build_passthrough_assessment(original_payload, candidate) for candidate in candidates if candidate.algorithm == 'passthrough']
        passthrough = assessments[0] if assessments else self._build_passthrough_assessment(
            original_payload,
            CompressionCandidate(algorithm='passthrough', payload=original_payload),
        )

        for candidate in candidates:
            if candidate.algorithm == 'passthrough':
                continue
            assessments.append(self._assess_candidate(original_payload, candidate, get_evaluator, profile=profile, suffix=suffix))

        self.last_assessments = assessments
        accepted = [item for item in assessments if item.accepted and item.candidate.algorithm != 'passthrough']

        if profile == 'fidelity':
            accepted.sort(key=self._fidelity_sort_key)
            for assessment in accepted:
                if self._passes_full_resolution_verification(get_evaluator(), assessment, suffix=suffix):
                    return assessment
            return passthrough

        if profile == 'balanced':
            accepted.sort(key=lambda item: item.payload_size)
            return accepted[0] if accepted else passthrough

        accepted.sort(key=lambda item: (item.payload_size, -item.ssim, -item.psnr))
        return accepted[0] if accepted else passthrough

    def _build_passthrough_assessment(self, original_payload: bytes, candidate: CompressionCandidate) -> CandidateAssessment:
        return CandidateAssessment(
            candidate=candidate,
            payload_size=len(candidate.payload),
            compression_ratio=0.0,
            ssim=1.0,
            psnr=99.0,
            accepted=True,
            rejection_reason=None,
            quality_tier='lossless',
            is_lossless_like=True,
            alpha_safe=True,
            animation_safe=True,
            dimensions_safe=True,
        )

    def _assess_candidate(
        self,
        original_payload: bytes,
        candidate: CompressionCandidate,
        evaluator_factory,
        *,
        profile: str,
        suffix: str | None,
    ) -> CandidateAssessment:
        payload_size = len(candidate.payload)
        compression_ratio = round(100 * (1 - (payload_size / max(len(original_payload), 1))), 2)
        quality_tier = self._quality_tier(candidate)
        is_lossless_like = self._is_lossless_like(candidate)

        if payload_size >= len(original_payload):
            return CandidateAssessment(
                candidate=candidate,
                payload_size=payload_size,
                compression_ratio=compression_ratio,
                ssim=0.0,
                psnr=0.0,
                accepted=False,
                rejection_reason='not_smaller_than_original',
                quality_tier=quality_tier,
                is_lossless_like=is_lossless_like,
                alpha_safe=True,
                animation_safe=True,
                dimensions_safe=True,
            )

        if profile == 'fidelity' and compression_ratio < settings.min_compression_saving_percent:
            return CandidateAssessment(
                candidate=candidate,
                payload_size=payload_size,
                compression_ratio=compression_ratio,
                ssim=0.0,
                psnr=0.0,
                accepted=False,
                rejection_reason='below_minimum_savings',
                quality_tier=quality_tier,
                is_lossless_like=is_lossless_like,
                alpha_safe=True,
                animation_safe=True,
                dimensions_safe=True,
            )

        metrics = evaluator_factory().evaluate(candidate.payload, require_same_dimensions=(profile == 'fidelity'))
        thresholds = self._thresholds_for_candidate(candidate, profile=profile)
        rejection_reason = self._rejection_reason(candidate, metrics, thresholds, profile=profile, suffix=suffix)

        return CandidateAssessment(
            candidate=candidate,
            payload_size=payload_size,
            compression_ratio=compression_ratio,
            ssim=metrics.ssim,
            psnr=metrics.psnr,
            accepted=rejection_reason is None,
            rejection_reason=rejection_reason,
            quality_tier=quality_tier,
            is_lossless_like=is_lossless_like,
            alpha_safe=metrics.alpha_safe,
            animation_safe=metrics.animation_safe,
            dimensions_safe=metrics.dimensions_equal,
        )

    def _rejection_reason(
        self,
        candidate: CompressionCandidate,
        metrics: CandidateMetrics,
        thresholds: tuple[float, float],
        *,
        profile: str,
        suffix: str | None,
    ) -> str | None:
        if not metrics.dimensions_equal:
            return 'dimension_mismatch'
        if not metrics.alpha_safe:
            return 'alpha_not_safe'
        if not metrics.animation_safe:
            return 'animation_not_safe'
        if profile == 'fidelity' and not self._allowed_in_fidelity(candidate, suffix=suffix):
            return 'not_allowed_in_fidelity_profile'
        if metrics.ssim < thresholds[0] or metrics.psnr < thresholds[1]:
            return 'quality_below_threshold'
        return None

    def _passes_full_resolution_verification(
        self,
        evaluator: MetricEvaluator,
        assessment: CandidateAssessment,
        *,
        suffix: str | None,
    ) -> bool:
        metrics = evaluator.evaluate(assessment.candidate.payload, full_resolution=True, require_same_dimensions=True)
        thresholds = self._thresholds_for_candidate(assessment.candidate, profile='fidelity')
        rejection_reason = self._rejection_reason(
            assessment.candidate,
            metrics,
            thresholds,
            profile='fidelity',
            suffix=suffix,
        )
        assessment.ssim = metrics.ssim
        assessment.psnr = metrics.psnr
        assessment.alpha_safe = metrics.alpha_safe
        assessment.animation_safe = metrics.animation_safe
        assessment.dimensions_safe = metrics.dimensions_equal
        assessment.accepted = rejection_reason is None
        assessment.rejection_reason = rejection_reason
        return rejection_reason is None

    def _fidelity_sort_key(self, assessment: CandidateAssessment) -> tuple[int, float, float, int]:
        return (-self._quality_rank(assessment), -assessment.ssim, -assessment.psnr, assessment.payload_size)

    def _quality_rank(self, assessment: CandidateAssessment) -> int:
        if assessment.is_lossless_like:
            return 3
        if assessment.quality_tier == 'high':
            return 2
        if assessment.quality_tier == 'medium':
            return 1
        return 0

    def _build_candidates(self, file_name: str, payload: bytes, suffix: str) -> list[CompressionCandidate]:
        toolchain = self.get_available_toolchain()
        candidates: list[CompressionCandidate] = [CompressionCandidate(algorithm='passthrough', payload=payload)]
        profile = self._profile()

        if suffix in {'jpg', 'jpeg'}:
            candidates.extend(self._build_jpeg_candidates(file_name=file_name, payload=payload, tools=toolchain['jpeg'], profile=profile))
            return candidates
        if suffix == 'png':
            candidates.extend(self._build_png_candidates(file_name=file_name, payload=payload, tools=toolchain['png'], profile=profile))
            return candidates
        if suffix == 'webp':
            candidates.extend(self._build_webp_candidates(payload=payload, tools=toolchain['webp'], profile=profile))
            return candidates
        if suffix == 'gif':
            candidates.extend(self._build_gif_candidates(payload=payload, tools=toolchain['gif'], profile=profile))
            return candidates
        return candidates

    def _build_jpeg_candidates(self, file_name: str, payload: bytes, tools: list[str], profile: str) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []
        source_suffix = Path(file_name).suffix or '.jpg'
        qualities = [96, 94, 92, 90] if profile == 'fidelity' else [92, 88, 84] if profile == 'balanced' else [92, 88, 84, 80]

        if 'jpegtran' in tools:
            compressed = self._compress_with_command('jpegtran', payload, quality=None, source_suffix=source_suffix, profile=profile)
            if compressed:
                candidates.append(
                    CompressionCandidate(
                        algorithm='mozjpeg-jpegtran-optimize',
                        payload=compressed,
                        metadata={'tool_identity': self._detect_tool_identity('jpegtran')},
                    )
                )

        if 'cjpeg' in tools:
            for quality in qualities:
                compressed = self._compress_with_command('cjpeg', payload, quality=quality, source_suffix=source_suffix, profile=profile)
                if compressed:
                    candidates.append(
                        CompressionCandidate(
                            algorithm=f'mozjpeg-cjpeg-q{quality}',
                            payload=compressed,
                            metadata={'tool_identity': self._detect_tool_identity('cjpeg'), 'quality': quality},
                        )
                    )

        for quality in qualities:
            compressed = self._compress_raster(payload, 'JPEG', quality)
            candidates.append(CompressionCandidate(algorithm=f'jpeg-pillow-q{quality}', payload=compressed, metadata={'quality': quality}))

        return candidates

    def _build_png_candidates(self, file_name: str, payload: bytes, tools: list[str], profile: str) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []
        source_suffix = Path(file_name).suffix or '.png'
        if 'zopflipng' in tools:
            iterations = [15, 50] if profile != 'smallest' else [15, 50, 80]
            for iteration_count in iterations:
                compressed = self._compress_with_command(
                    'zopflipng',
                    payload,
                    quality=iteration_count,
                    source_suffix=source_suffix,
                    profile=profile,
                )
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'zopflipng-i{iteration_count}', payload=compressed))

        if profile == 'smallest' and 'pngquant' in tools:
            for quality_range in ((70, 90), (60, 85)):
                compressed = self._compress_with_command(
                    'pngquant',
                    payload,
                    quality=quality_range,
                    source_suffix=source_suffix,
                    profile=profile,
                )
                if compressed:
                    candidates.append(
                        CompressionCandidate(
                            algorithm=f'pngquant-{quality_range[0]}-{quality_range[1]}',
                            payload=compressed,
                        )
                    )

        candidates.append(CompressionCandidate(algorithm='png-pillow-optimize', payload=self._compress_raster(payload, 'PNG', 0, optimize=True)))
        return candidates

    def _build_webp_candidates(self, payload: bytes, tools: list[str], profile: str) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []
        has_alpha = self._payload_has_alpha(payload)
        qualities = [95, 92, 90] if profile == 'fidelity' else [85, 80, 75] if profile == 'balanced' else [85, 80, 75, 70]

        if profile == 'fidelity' and has_alpha:
            if 'cwebp' in tools:
                compressed = self._compress_with_command('cwebp', payload, quality=('lossless', 100), source_suffix='.webp', profile=profile)
                if compressed:
                    candidates.append(CompressionCandidate(algorithm='cwebp-lossless', payload=compressed))
            candidates.append(CompressionCandidate(algorithm='webp-lossless', payload=self._compress_webp(payload, lossless=True, quality=100)))

        if 'cwebp' in tools:
            for quality in qualities:
                compressed = self._compress_with_command('cwebp', payload, quality=quality, source_suffix='.webp', profile=profile)
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'cwebp-q{quality}', payload=compressed, metadata={'quality': quality}))

        for quality in qualities:
            candidates.append(
                CompressionCandidate(
                    algorithm=f'webp-q{quality}',
                    payload=self._compress_webp(payload, lossless=False, quality=quality),
                    metadata={'quality': quality},
                )
            )

        return candidates

    def _build_gif_candidates(self, payload: bytes, tools: list[str], profile: str) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []
        if 'gifsicle' in tools:
            for level in [2, 3]:
                compressed = self._compress_with_command('gifsicle', payload, quality=level, source_suffix='.gif', profile=profile)
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'gifsicle-o{level}', payload=compressed))

            if profile != 'fidelity':
                lossy_compressed = self._compress_with_command('gifsicle', payload, quality=(3, 30), source_suffix='.gif', profile=profile)
                if lossy_compressed:
                    candidates.append(CompressionCandidate(algorithm='gifsicle-o3-lossy30', payload=lossy_compressed))

        if profile != 'fidelity':
            candidates.append(CompressionCandidate(algorithm='gif-optimized', payload=self._compress_gif(payload)))
        return candidates

    def _compress_with_command(
        self,
        command: str,
        payload: bytes,
        quality: int | tuple[int, int] | tuple[str, int] | None,
        source_suffix: str,
        profile: str,
    ) -> bytes | None:
        if not which(command):
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / f'input{source_suffix}'
            output_suffix = self._output_suffix_for_command(command, source_suffix)
            output_path = tmpdir_path / f'output{output_suffix}'
            input_path.write_bytes(payload)

            command_line = self._build_command_line(command, input_path, output_path, quality, profile=profile)
            if not command_line:
                return None

            try:
                subprocess.run(command_line, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except (FileNotFoundError, subprocess.CalledProcessError):
                return None

            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path.read_bytes()

            return None

    def _build_command_line(
        self,
        command: str,
        input_path: Path,
        output_path: Path,
        quality: int | tuple[int, int] | tuple[str, int] | None,
        *,
        profile: str,
    ) -> list[str] | None:
        if command == 'cjpeg':
            quality_value = 82 if not isinstance(quality, int) else quality
            ppm_input = self._convert_to_ppm_bytes(input_path.read_bytes())
            ppm_path = input_path.with_suffix('.ppm')
            ppm_path.write_bytes(ppm_input)
            return [
                'cjpeg',
                '-quality', str(quality_value),
                '-optimize',
                '-progressive',
                '-sample', '1x1',
                '-outfile', str(output_path),
                str(ppm_path),
            ]
        if command == 'jpegtran':
            copy_mode = 'all' if profile == 'fidelity' else 'none'
            return ['jpegtran', '-copy', copy_mode, '-optimize', '-progressive', '-outfile', str(output_path), str(input_path)]
        if command == 'pngquant':
            quality_min, quality_max = quality if isinstance(quality, tuple) else (70, 90)
            return ['pngquant', '--force', '--skip-if-larger', '--output', str(output_path), '--quality', f'{quality_min}-{quality_max}', '--', str(input_path)]
        if command == 'zopflipng':
            iterations = quality if isinstance(quality, int) else 15
            return [
                'zopflipng',
                f'--iterations={iterations}',
                '--filters=01234mepb',
                '--keepchunks=iCCP,gAMA,cHRM,sRGB',
                str(input_path),
                str(output_path),
            ]
        if command == 'cwebp':
            if isinstance(quality, tuple) and quality and quality[0] == 'lossless':
                return [
                    'cwebp',
                    '-lossless',
                    '-q', str(quality[1]),
                    '-m', '6',
                    '-alpha_q', '100',
                    '-exact',
                    '-metadata', 'all',
                    '-mt',
                    str(input_path),
                    '-o', str(output_path),
                ]
            quality_value = 80 if not isinstance(quality, int) else quality
            return [
                'cwebp',
                '-q', str(quality_value),
                '-m', '6',
                '-sharp_yuv',
                '-alpha_q', '100',
                '-exact',
                '-metadata', 'all',
                '-mt',
                str(input_path),
                '-o', str(output_path),
            ]
        if command == 'gifsicle':
            if isinstance(quality, tuple) and quality and isinstance(quality[0], int):
                level, lossy = quality
                return ['gifsicle', f'-O{level}', f'--lossy={lossy}', str(input_path), '-o', str(output_path)]
            level = 3 if not isinstance(quality, int) else quality
            return ['gifsicle', f'-O{level}', str(input_path), '-o', str(output_path)]
        return None

    def _output_suffix_for_command(self, command: str, source_suffix: str) -> str:
        if command == 'cwebp':
            return '.webp'
        return source_suffix

    def _convert_to_ppm_bytes(self, payload: bytes) -> bytes:
        with Image.open(BytesIO(payload)) as image:
            image = image.convert('RGB')
            buffer = BytesIO()
            image.save(buffer, format='PPM')
            return buffer.getvalue()

    def _compress_raster(self, payload: bytes, fmt: str, quality: int, optimize: bool = True) -> bytes:
        with Image.open(BytesIO(payload)) as source_image:
            image = source_image.convert('RGB') if fmt == 'JPEG' else source_image.convert('RGBA')
            buffer = BytesIO()
            save_kwargs = self._build_pillow_save_kwargs(image=source_image, fmt=fmt, quality=quality, optimize=optimize)
            image.save(buffer, **save_kwargs)
            return buffer.getvalue()

    def _build_pillow_save_kwargs(self, image: Image.Image, fmt: str, quality: int, optimize: bool) -> dict:
        save_kwargs: dict[str, object] = {'format': fmt, 'optimize': optimize}
        if fmt == 'JPEG':
            save_kwargs.update({'quality': quality, 'progressive': True, 'subsampling': 0})
        if fmt == 'PNG':
            if image.info.get('icc_profile'):
                save_kwargs['icc_profile'] = image.info['icc_profile']
            if image.info.get('gamma') is not None:
                save_kwargs['gamma'] = image.info['gamma']
            if image.info.get('transparency') is not None:
                save_kwargs['transparency'] = image.info['transparency']
            pnginfo = PngImagePlugin.PngInfo()
            for key in ('Comment', 'Description', 'Software'):
                if key in image.info:
                    pnginfo.add_text(key, str(image.info[key]))
            if pnginfo.chunks:
                save_kwargs['pnginfo'] = pnginfo
        if image.info.get('icc_profile') and fmt in {'JPEG', 'WEBP'}:
            save_kwargs['icc_profile'] = image.info['icc_profile']
        if image.info.get('exif') and fmt in {'JPEG', 'WEBP'}:
            save_kwargs['exif'] = image.info['exif']
        return save_kwargs

    def _compress_webp(self, payload: bytes, lossless: bool, quality: int) -> bytes:
        with Image.open(BytesIO(payload)) as source_image:
            image = source_image
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA' if self._payload_has_alpha(payload) else 'RGB')
            buffer = BytesIO()
            save_kwargs: dict[str, object] = {
                'format': 'WEBP',
                'quality': quality,
                'lossless': lossless,
                'method': 6,
            }
            if source_image.info.get('icc_profile'):
                save_kwargs['icc_profile'] = source_image.info['icc_profile']
            if source_image.info.get('exif'):
                save_kwargs['exif'] = source_image.info['exif']
            image.save(buffer, **save_kwargs)
            return buffer.getvalue()

    def _compress_gif(self, payload: bytes) -> bytes:
        with Image.open(BytesIO(payload)) as image:
            frames = [frame.convert('P', palette=Image.ADAPTIVE) for frame in ImageSequence.Iterator(image)]
            buffer = BytesIO()
            frames[0].save(
                buffer,
                format='GIF',
                save_all=True,
                append_images=frames[1:],
                loop=image.info.get('loop', 0),
                duration=image.info.get('duration', 100),
                optimize=True,
                disposal=getattr(image, 'disposal_method', image.info.get('disposal', 0) or 0),
            )
            return buffer.getvalue()

    def _target_extension(self, suffix: str, algorithm: str) -> str:
        if 'webp' in algorithm:
            return 'webp'
        return suffix

    def _thresholds_for_candidate(self, candidate: CompressionCandidate, *, profile: str) -> tuple[float, float]:
        if candidate.algorithm.startswith('gifsicle-o') and 'lossy' in candidate.algorithm:
            return (settings.gif_lossy_ssim_threshold, settings.gif_lossy_psnr_threshold)
        if profile == 'smallest':
            return (max(settings.ssim_threshold - 0.01, 0.0), max(settings.psnr_threshold - 2.0, 0.0))
        return (settings.ssim_threshold, settings.psnr_threshold)

    def _mime_from_algorithm_or_suffix(self, algorithm: str, suffix: str) -> str:
        if 'webp' in algorithm:
            return 'image/webp'
        return self._mime_from_suffix(suffix)

    def _mime_from_suffix(self, suffix: str) -> str:
        mapping = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'webp': 'image/webp',
            'gif': 'image/gif',
        }
        return mapping[suffix]

    def _sanitize_upload_file_name(self, file_name: str) -> str:
        posix_name = PurePosixPath(file_name).name
        windows_name = PureWindowsPath(posix_name).name
        safe_name = windows_name.strip().replace('\x00', '')
        return safe_name or 'upload.bin'

    def _quality_tier(self, candidate: CompressionCandidate) -> str:
        algorithm = candidate.algorithm.lower()
        if self._is_lossless_like(candidate):
            return 'lossless'
        if '-q' in algorithm:
            quality = int(algorithm.rsplit('q', 1)[-1])
            if quality >= 90:
                return 'high'
            if quality >= 80:
                return 'medium'
        return 'aggressive'

    def _is_lossless_like(self, candidate: CompressionCandidate) -> bool:
        algorithm = candidate.algorithm.lower()
        return any(
            keyword in algorithm
            for keyword in ('passthrough', 'jpegtran', 'zopflipng', 'lossless', 'png-pillow-optimize', 'gifsicle-o2', 'gifsicle-o3')
        ) and 'lossy' not in algorithm and 'pngquant' not in algorithm and 'gif-optimized' not in algorithm

    def _allowed_in_fidelity(self, candidate: CompressionCandidate, *, suffix: str | None) -> bool:
        algorithm = candidate.algorithm.lower()
        if suffix == 'png':
            return self._is_lossless_like(candidate) and 'pngquant' not in algorithm
        if suffix == 'gif':
            return algorithm.startswith('gifsicle-o') and 'lossy' not in algorithm
        if suffix in {'jpg', 'jpeg', 'webp'}:
            return self._quality_tier(candidate) in {'lossless', 'high'}
        return True

    def _payload_has_alpha(self, payload: bytes) -> bool:
        with Image.open(BytesIO(payload)) as image:
            return image.mode in {'RGBA', 'LA'} or (image.mode == 'P' and 'transparency' in image.info)

    def _detect_tool_identity(self, command: str) -> str:
        if command in self._tool_identity_cache:
            return self._tool_identity_cache[command]
        if not which(command):
            return 'unavailable'
        for args in ([command, '-version'], [command, '--version'], [command, '-v']):
            try:
                completed = subprocess.run(args, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            except OSError:
                continue
            output = (completed.stdout or completed.stderr).strip()
            if output:
                identity = output.splitlines()[0][:120]
                self._tool_identity_cache[command] = identity
                return identity
        self._tool_identity_cache[command] = 'available'
        return 'available'


compression_service = CompressionService()
