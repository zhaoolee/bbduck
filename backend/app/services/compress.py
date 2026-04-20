from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from shutil import which
from uuid import uuid4

from PIL import Image, ImageSequence

from app.core.config import settings
from app.schemas import CompressionItem, CompressionMetrics
from app.services.metrics import compute_metrics


@dataclass
class CompressionCandidate:
    algorithm: str
    payload: bytes


@dataclass
class CandidateAssessment:
    candidate: CompressionCandidate
    ssim: float
    psnr: float


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

    def compress_bytes(self, file_name: str, payload: bytes) -> CompressionItem:
        suffix = Path(file_name).suffix.lower().lstrip('.')
        if suffix not in settings.allowed_suffixes:
            raise ValueError(f'Unsupported file type: {suffix}')

        safe_file_name = self._sanitize_upload_file_name(file_name)
        original_path = settings.upload_dir / f'{uuid4().hex}-{safe_file_name}'
        original_path.write_bytes(payload)

        candidates = self._build_candidates(file_name=safe_file_name, payload=payload, suffix=suffix)
        chosen = self._choose_candidate(payload, candidates)

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

    def _choose_candidate(self, original_payload: bytes, candidates: list[CompressionCandidate]) -> CandidateAssessment:
        accepted: list[CandidateAssessment] = []
        original_candidate: CandidateAssessment | None = None

        for candidate in candidates:
            if candidate.algorithm == 'passthrough':
                original_candidate = CandidateAssessment(candidate=candidate, ssim=1.0, psnr=99.0)
                continue
            if len(candidate.payload) >= len(original_payload):
                continue

            ssim, psnr = compute_metrics(original_payload, candidate.payload)
            threshold_ssim, threshold_psnr = self._thresholds_for_candidate(candidate)
            if ssim >= threshold_ssim and psnr >= threshold_psnr:
                accepted.append(CandidateAssessment(candidate=candidate, ssim=ssim, psnr=psnr))

        if accepted:
            accepted.sort(key=lambda item: len(item.candidate.payload))
            return accepted[0]

        if original_candidate is not None:
            return original_candidate

        fallback = min(candidates, key=lambda item: len(item.payload))
        if fallback.algorithm == 'passthrough':
            return CandidateAssessment(candidate=fallback, ssim=1.0, psnr=99.0)
        ssim, psnr = compute_metrics(original_payload, fallback.payload)
        return CandidateAssessment(candidate=fallback, ssim=ssim, psnr=psnr)

    def _build_candidates(self, file_name: str, payload: bytes, suffix: str) -> list[CompressionCandidate]:
        toolchain = self.get_available_toolchain()
        candidates: list[CompressionCandidate] = [CompressionCandidate(algorithm='passthrough', payload=payload)]

        if suffix in {'jpg', 'jpeg'}:
            candidates.extend(self._build_jpeg_candidates(file_name=file_name, payload=payload, tools=toolchain['jpeg']))
            return candidates
        if suffix == 'png':
            candidates.extend(self._build_png_candidates(file_name=file_name, payload=payload, tools=toolchain['png']))
            return candidates
        if suffix == 'webp':
            candidates.extend(self._build_webp_candidates(payload=payload, tools=toolchain['webp']))
            return candidates
        if suffix == 'gif':
            candidates.extend(self._build_gif_candidates(payload=payload, tools=toolchain['gif']))
            return candidates
        return candidates

    def _build_jpeg_candidates(self, file_name: str, payload: bytes, tools: list[str]) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []

        if 'jpegtran' in tools:
            compressed = self._compress_with_command('jpegtran', payload, quality=None, source_suffix=Path(file_name).suffix or '.jpg')
            if compressed:
                candidates.append(CompressionCandidate(algorithm='mozjpeg-jpegtran-optimize', payload=compressed))

        if 'cjpeg' in tools:
            for quality in [92, 88, 84]:
                compressed = self._compress_with_command('cjpeg', payload, quality=quality, source_suffix=Path(file_name).suffix or '.jpg')
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'mozjpeg-cjpeg-q{quality}', payload=compressed))

        for quality in [92, 88, 84]:
            compressed = self._compress_raster(payload, 'JPEG', quality)
            candidates.append(CompressionCandidate(algorithm=f'jpeg-pillow-q{quality}', payload=compressed))

        return candidates

    def _build_png_candidates(self, file_name: str, payload: bytes, tools: list[str]) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []

        if 'zopflipng' in tools:
            for iterations in [15, 50]:
                compressed = self._compress_with_command(
                    'zopflipng',
                    payload,
                    quality=iterations,
                    source_suffix=Path(file_name).suffix or '.png',
                )
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'zopflipng-i{iterations}', payload=compressed))

        candidates.append(CompressionCandidate(algorithm='png-pillow-optimize', payload=self._compress_raster(payload, 'PNG', 0, optimize=True)))
        return candidates

    def _build_webp_candidates(self, payload: bytes, tools: list[str]) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []

        if 'cwebp' in tools:
            for quality in [85, 80, 75]:
                compressed = self._compress_with_command('cwebp', payload, quality=quality, source_suffix='.webp')
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'cwebp-q{quality}', payload=compressed))

        for quality in [85, 80, 75]:
            candidates.append(CompressionCandidate(algorithm=f'webp-q{quality}', payload=self._compress_webp(payload, lossless=False, quality=quality)))

        return candidates

    def _build_gif_candidates(self, payload: bytes, tools: list[str]) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = []

        if 'gifsicle' in tools:
            for level in [2, 3]:
                compressed = self._compress_with_command('gifsicle', payload, quality=level, source_suffix='.gif')
                if compressed:
                    candidates.append(CompressionCandidate(algorithm=f'gifsicle-o{level}', payload=compressed))

            lossy_compressed = self._compress_with_command('gifsicle', payload, quality=(3, 30), source_suffix='.gif')
            if lossy_compressed:
                candidates.append(CompressionCandidate(algorithm='gifsicle-o3-lossy30', payload=lossy_compressed))

        candidates.append(CompressionCandidate(algorithm='gif-optimized', payload=self._compress_gif(payload)))
        return candidates

    def _compress_with_command(
        self,
        command: str,
        payload: bytes,
        quality: int | tuple[int, int] | None,
        source_suffix: str,
    ) -> bytes | None:
        if not which(command):
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_path = tmpdir_path / f'input{source_suffix}'
            output_suffix = self._output_suffix_for_command(command, source_suffix)
            output_path = tmpdir_path / f'output{output_suffix}'
            input_path.write_bytes(payload)

            command_line = self._build_command_line(command, input_path, output_path, quality)
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
        quality: int | tuple[int, int] | None,
    ) -> list[str] | None:
        if command == 'cjpeg':
            quality_value = 82 if not isinstance(quality, int) else quality
            ppm_input = self._convert_to_ppm_bytes(input_path.read_bytes())
            ppm_path = input_path.with_suffix('.ppm')
            ppm_path.write_bytes(ppm_input)
            return ['cjpeg', '-quality', str(quality_value), '-optimize', '-progressive', '-outfile', str(output_path), str(ppm_path)]
        if command == 'jpegtran':
            return ['jpegtran', '-copy', 'none', '-optimize', '-progressive', '-outfile', str(output_path), str(input_path)]
        if command == 'pngquant':
            quality_min, quality_max = quality if isinstance(quality, tuple) else (70, 90)
            return ['pngquant', '--force', '--skip-if-larger', '--output', str(output_path), '--quality', f'{quality_min}-{quality_max}', '--', str(input_path)]
        if command == 'zopflipng':
            iterations = quality if isinstance(quality, int) else 15
            return ['zopflipng', f'--iterations={iterations}', '--filters=01234mepb', str(input_path), str(output_path)]
        if command == 'cwebp':
            quality_value = 80 if not isinstance(quality, int) else quality
            return ['cwebp', '-q', str(quality_value), '-mt', str(input_path), '-o', str(output_path)]
        if command == 'gifsicle':
            if isinstance(quality, tuple):
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
        save_kwargs = {'format': fmt, 'optimize': optimize}
        if fmt == 'JPEG':
            save_kwargs.update({'quality': quality, 'progressive': True, 'subsampling': 0})
        if image.info.get('icc_profile'):
            save_kwargs['icc_profile'] = image.info['icc_profile']
        if image.info.get('exif') and fmt in {'JPEG', 'WEBP'}:
            save_kwargs['exif'] = image.info['exif']
        return save_kwargs

    def _compress_webp(self, payload: bytes, lossless: bool, quality: int) -> bytes:
        with Image.open(BytesIO(payload)) as source_image:
            image = source_image
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA')
            buffer = BytesIO()
            save_kwargs = {
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
            )
            return buffer.getvalue()

    def _target_extension(self, suffix: str, algorithm: str) -> str:
        if 'webp' in algorithm:
            return 'webp'
        return suffix

    def _thresholds_for_candidate(self, candidate: CompressionCandidate) -> tuple[float, float]:
        if candidate.algorithm.startswith('gifsicle-o') and 'lossy' in candidate.algorithm:
            return (settings.gif_lossy_ssim_threshold, settings.gif_lossy_psnr_threshold)
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


compression_service = CompressionService()
