from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
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


@dataclass
class CandidateTask:
    algorithm: str
    start_message: str
    build: Callable[[], bytes | None]


class CompressionService:
    """图片压缩服务。默认走接近 PP鸭 的 visual-lossless 模式。"""

    candidate_type = CompressionCandidate
    assessment_type = CandidateAssessment

    def _emit_progress(self, progress_callback: Callable[[dict[str, object]], None] | None, stage: str, message: str) -> None:
        if progress_callback is None:
            return
        progress_callback({'stage': stage, 'message': message})

    def _run_candidate_tasks(
        self,
        tasks: list[CandidateTask],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> list[CompressionCandidate]:
        if not tasks:
            return []

        for task in tasks:
            self._emit_progress(progress_callback, 'candidate', task.start_message)

        results: list[CompressionCandidate | None] = [None] * len(tasks)
        max_workers = max(1, len(tasks))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(task.build): (index, task) for index, task in enumerate(tasks)}
            for future in as_completed(future_map):
                index, task = future_map[future]
                payload = future.result()
                if not payload:
                    self._emit_progress(progress_callback, 'candidate', f'{task.algorithm} 未生成有效结果，已跳过')
                    continue
                results[index] = CompressionCandidate(algorithm=task.algorithm, payload=payload)
                self._emit_progress(progress_callback, 'candidate', f'生成候选 {task.algorithm}：{len(payload) / 1024:.1f} KB')

        return [candidate for candidate in results if candidate is not None]

    def _evaluate_candidate(
        self,
        original_payload: bytes,
        candidate: CompressionCandidate,
    ) -> tuple[CandidateAssessment | None, str]:
        if len(candidate.payload) >= len(original_payload):
            return None, f'{candidate.algorithm} 被淘汰：结果不比原图更小'

        saving_percent = 100 * (1 - (len(candidate.payload) / max(len(original_payload), 1)))
        if saving_percent < settings.min_compression_saving_percent:
            return None, f'{candidate.algorithm} 被淘汰：压缩收益仅 {saving_percent:.2f}%'

        ssim, psnr = compute_metrics(original_payload, candidate.payload)
        threshold_ssim, threshold_psnr = self._thresholds_for_candidate(candidate)
        if ssim >= threshold_ssim and psnr >= threshold_psnr:
            return (
                CandidateAssessment(candidate=candidate, ssim=ssim, psnr=psnr),
                f'{candidate.algorithm} 通过评估：{len(candidate.payload) / 1024:.1f} KB，节省 {saving_percent:.2f}%，SSIM {ssim:.4f}，PSNR {psnr:.2f}',
            )

        return None, f'{candidate.algorithm} 被淘汰：SSIM {ssim:.4f}/{threshold_ssim:.4f}，PSNR {psnr:.2f}/{threshold_psnr:.2f}'

    def compress_bytes(
        self,
        file_name: str,
        payload: bytes,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CompressionItem:
        suffix = Path(file_name).suffix.lower().lstrip('.')
        if suffix not in settings.allowed_suffixes:
            raise ValueError(f'Unsupported file type: {suffix}')

        safe_file_name = self._sanitize_upload_file_name(file_name)
        original_path = settings.upload_dir / f'{uuid4().hex}-{safe_file_name}'
        original_path.write_bytes(payload)
        self._emit_progress(progress_callback, 'start', f'已接收 {safe_file_name}，原图 {len(payload) / 1024:.1f} KB，开始按 {self._profile()} 模式压缩')

        toolchain = self.get_available_toolchain()
        available_tools = toolchain.get(suffix if suffix in {'png', 'gif', 'webp'} else 'jpeg', [])
        if available_tools:
            self._emit_progress(progress_callback, 'toolchain', f'检测到可用工具：{", ".join(available_tools)}')
        else:
            self._emit_progress(progress_callback, 'toolchain', '未检测到外部压缩工具，将使用 Pillow 内置编码兜底')

        chosen = self._compress_by_suffix(
            file_name=safe_file_name,
            payload=payload,
            suffix=suffix,
            toolchain=toolchain,
            progress_callback=progress_callback,
        )

        compressed_name = f'{original_path.stem}.compressed.{self._target_extension(suffix, chosen.candidate.algorithm)}'
        compressed_path = settings.output_dir / compressed_name
        compressed_path.write_bytes(chosen.candidate.payload)

        ratio = round(100 * (1 - (len(chosen.candidate.payload) / max(len(payload), 1))), 2)
        self._emit_progress(
            progress_callback,
            'finish',
            f'最终选择 {chosen.candidate.algorithm}，输出 {len(chosen.candidate.payload) / 1024:.1f} KB，压缩率 {ratio:.2f}%，SSIM {chosen.ssim:.4f}，PSNR {chosen.psnr:.2f}',
        )

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

    def _compress_by_suffix(
        self,
        file_name: str,
        payload: bytes,
        suffix: str,
        toolchain: dict[str, list[str]],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CandidateAssessment:
        if suffix in {'jpg', 'jpeg'}:
            return self._compress_jpeg(file_name, payload, toolchain['jpeg'], progress_callback)
        if suffix == 'png':
            return self._compress_png(file_name, payload, toolchain['png'], progress_callback)
        if suffix == 'webp':
            return self._compress_webp(file_name, payload, toolchain['webp'], progress_callback)
        if suffix == 'gif':
            return self._compress_gif(file_name, payload, toolchain['gif'], progress_callback)
        raise ValueError(f'Unsupported file type: {suffix}')

    def _compress_jpeg(
        self,
        file_name: str,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CandidateAssessment:
        candidates = self._build_jpeg_candidates(file_name=file_name, payload=payload, tools=tools, progress_callback=progress_callback)
        return self._choose_candidate(payload, [CompressionCandidate(algorithm='passthrough', payload=payload), *candidates], progress_callback)

    def _compress_png(
        self,
        file_name: str,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CandidateAssessment:
        candidates = self._build_png_candidates(file_name=file_name, payload=payload, tools=tools, progress_callback=progress_callback)
        return self._choose_candidate(payload, [CompressionCandidate(algorithm='passthrough', payload=payload), *candidates], progress_callback)

    def _compress_webp(
        self,
        file_name: str,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CandidateAssessment:
        _ = file_name
        candidates = self._build_webp_candidates(payload=payload, tools=tools, progress_callback=progress_callback)
        return self._choose_candidate(payload, [CompressionCandidate(algorithm='passthrough', payload=payload), *candidates], progress_callback)

    def _compress_gif(
        self,
        file_name: str,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CandidateAssessment:
        _ = file_name
        candidates = self._build_gif_candidates(payload=payload, tools=tools, progress_callback=progress_callback)
        return self._choose_candidate(payload, [CompressionCandidate(algorithm='passthrough', payload=payload), *candidates], progress_callback)

    def _profile(self) -> str:
        return settings.compression_profile

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

    def _choose_candidate(
        self,
        original_payload: bytes,
        candidates: list[CompressionCandidate],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> CandidateAssessment:
        accepted: list[CandidateAssessment] = []
        original_candidate: CandidateAssessment | None = None

        self._emit_progress(progress_callback, 'select', f'开始并行评估 {max(len(candidates) - 1, 0)} 个压缩候选')

        evaluation_targets: list[tuple[int, CompressionCandidate]] = []
        for index, candidate in enumerate(candidates):
            if candidate.algorithm == 'passthrough':
                original_candidate = CandidateAssessment(candidate=candidate, ssim=1.0, psnr=99.0)
                self._emit_progress(progress_callback, 'select', '保留原图作为安全兜底候选')
                continue
            evaluation_targets.append((index, candidate))

        evaluated: list[tuple[int, CandidateAssessment | None, str]] = []
        if evaluation_targets:
            with ThreadPoolExecutor(max_workers=max(1, len(evaluation_targets))) as executor:
                future_map = {
                    executor.submit(self._evaluate_candidate, original_payload, candidate): (index, candidate)
                    for index, candidate in evaluation_targets
                }
                for future in as_completed(future_map):
                    index, candidate = future_map[future]
                    assessment, message = future.result()
                    evaluated.append((index, assessment, message))
                    self._emit_progress(progress_callback, 'select', message)

        for _index, assessment, _message in sorted(evaluated, key=lambda item: item[0]):
            if assessment is not None:
                accepted.append(assessment)

        if accepted:
            accepted.sort(key=lambda item: len(item.candidate.payload))
            return accepted[0]

        if original_candidate is not None:
            self._emit_progress(progress_callback, 'select', '没有候选通过评估，回退到原图')
            return original_candidate

        fallback = min(candidates, key=lambda item: len(item.payload))
        if fallback.algorithm == 'passthrough':
            return CandidateAssessment(candidate=fallback, ssim=1.0, psnr=99.0)
        ssim, psnr = compute_metrics(original_payload, fallback.payload)
        self._emit_progress(progress_callback, 'select', f'未找到合格候选，使用最小结果 {fallback.algorithm} 作为最后兜底')
        return CandidateAssessment(candidate=fallback, ssim=ssim, psnr=psnr)

    def _build_candidates(self, file_name: str, payload: bytes, suffix: str) -> list[CompressionCandidate]:
        candidates: list[CompressionCandidate] = [CompressionCandidate(algorithm='passthrough', payload=payload)]
        if suffix in {'jpg', 'jpeg'}:
            candidates.extend(self._build_jpeg_candidates(file_name=file_name, payload=payload, tools=self.get_available_toolchain()['jpeg']))
            return candidates
        if suffix == 'png':
            candidates.extend(self._build_png_candidates(file_name=file_name, payload=payload, tools=self.get_available_toolchain()['png']))
            return candidates
        if suffix == 'webp':
            candidates.extend(self._build_webp_candidates(payload=payload, tools=self.get_available_toolchain()['webp']))
            return candidates
        if suffix == 'gif':
            candidates.extend(self._build_gif_candidates(payload=payload, tools=self.get_available_toolchain()['gif']))
            return candidates
        return candidates

    def _build_jpeg_candidates(
        self,
        file_name: str,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> list[CompressionCandidate]:
        profile = self._profile()
        qualities = {
            'safe': [96, 94],
            'visual-lossless': [96, 92, 88],
            'aggressive': [92, 88, 84],
        }[profile]
        self._emit_progress(progress_callback, 'candidate', f'JPEG 将并行尝试 {len(qualities)} 档质量和 {len(tools)} 个外部工具路径')

        tasks: list[CandidateTask] = []
        if 'jpegtran' in tools:
            tasks.append(
                CandidateTask(
                    algorithm='jpegtran-optimize',
                    start_message='开始生成候选 jpegtran-optimize…',
                    build=lambda: self._compress_with_command('jpegtran', payload, quality=None, source_suffix=Path(file_name).suffix or '.jpg'),
                )
            )

        if 'cjpeg' in tools:
            for quality in qualities:
                tasks.append(
                    CandidateTask(
                        algorithm=f'mozjpeg-cjpeg-q{quality}',
                        start_message=f'开始生成候选 mozjpeg-cjpeg-q{quality}…',
                        build=lambda quality=quality: self._compress_with_command('cjpeg', payload, quality=quality, source_suffix=Path(file_name).suffix or '.jpg'),
                    )
                )

        for quality in qualities:
            tasks.append(
                CandidateTask(
                    algorithm=f'jpeg-pillow-q{quality}',
                    start_message=f'开始生成候选 jpeg-pillow-q{quality}…',
                    build=lambda quality=quality: self._compress_raster(payload, 'JPEG', quality),
                )
            )

        return self._run_candidate_tasks(tasks, progress_callback)

    def _build_png_candidates(
        self,
        file_name: str,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> list[CompressionCandidate]:
        profile = self._profile()
        self._emit_progress(progress_callback, 'candidate', f'PNG 将并行尝试 {len(tools)} 个外部工具路径和 1 个 Pillow 兜底候选')

        tasks: list[CandidateTask] = []
        if profile in {'visual-lossless', 'aggressive'} and 'pngquant' in tools:
            quality_ranges = [(85, 98)] if profile == 'visual-lossless' else [(75, 95), (65, 85)]
            for quality_range in quality_ranges:
                tasks.append(
                    CandidateTask(
                        algorithm=f'pngquant-{quality_range[0]}-{quality_range[1]}',
                        start_message=f'开始生成候选 pngquant-{quality_range[0]}-{quality_range[1]}…',
                        build=lambda quality_range=quality_range: self._compress_with_command('pngquant', payload, quality=quality_range, source_suffix=Path(file_name).suffix or '.png'),
                    )
                )

        if 'zopflipng' in tools:
            iterations: list[int] = []
            if profile == 'safe':
                iterations = [15]
            elif profile == 'aggressive':
                iterations = [15, 50, 80]
            for iteration_count in iterations:
                tasks.append(
                    CandidateTask(
                        algorithm=f'zopflipng-i{iteration_count}',
                        start_message=f'开始生成候选 zopflipng-i{iteration_count}… 这一步可能比较慢',
                        build=lambda iteration_count=iteration_count: self._compress_with_command('zopflipng', payload, quality=iteration_count, source_suffix=Path(file_name).suffix or '.png'),
                    )
                )

        tasks.append(
            CandidateTask(
                algorithm='png-pillow-optimize',
                start_message='开始生成候选 png-pillow-optimize…',
                build=lambda: self._compress_raster(payload, 'PNG', 0, optimize=True),
            )
        )
        return self._run_candidate_tasks(tasks, progress_callback)

    def _build_webp_candidates(
        self,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> list[CompressionCandidate]:
        profile = self._profile()
        self._emit_progress(progress_callback, 'candidate', f'WebP 将并行尝试 {len(tools)} 个外部工具路径和 Pillow 回退编码')

        tasks: list[CandidateTask] = []
        if 'cwebp' in tools:
            if profile in {'safe', 'visual-lossless'}:
                tasks.append(
                    CandidateTask(
                        algorithm='cwebp-lossless',
                        start_message='开始生成候选 cwebp-lossless…',
                        build=lambda: self._compress_with_command('cwebp', payload, quality=('lossless', 100), source_suffix='.webp'),
                    )
                )
            if profile == 'visual-lossless':
                tasks.append(
                    CandidateTask(
                        algorithm='cwebp-near-lossless-80',
                        start_message='开始生成候选 cwebp-near-lossless-80…',
                        build=lambda: self._compress_with_command('cwebp', payload, quality=('near_lossless', 80), source_suffix='.webp'),
                    )
                )
            if profile == 'aggressive':
                tasks.append(
                    CandidateTask(
                        algorithm='cwebp-near-lossless-60',
                        start_message='开始生成候选 cwebp-near-lossless-60…',
                        build=lambda: self._compress_with_command('cwebp', payload, quality=('near_lossless', 60), source_suffix='.webp'),
                    )
                )

            qualities = {
                'safe': [92],
                'visual-lossless': [90, 85],
                'aggressive': [82, 75],
            }[profile]
            for quality in qualities:
                tasks.append(
                    CandidateTask(
                        algorithm=f'cwebp-q{quality}',
                        start_message=f'开始生成候选 cwebp-q{quality}…',
                        build=lambda quality=quality: self._compress_with_command('cwebp', payload, quality=quality, source_suffix='.webp'),
                    )
                )

        pillow_qualities = {
            'safe': [92],
            'visual-lossless': [90, 85],
            'aggressive': [82, 75],
        }[profile]
        for quality in pillow_qualities:
            tasks.append(
                CandidateTask(
                    algorithm=f'webp-q{quality}',
                    start_message=f'开始生成候选 webp-q{quality}…',
                    build=lambda quality=quality: self._encode_webp(payload, lossless=False, quality=quality),
                )
            )

        return self._run_candidate_tasks(tasks, progress_callback)

    def _build_gif_candidates(
        self,
        payload: bytes,
        tools: list[str],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> list[CompressionCandidate]:
        profile = self._profile()
        self._emit_progress(progress_callback, 'candidate', f'GIF 将并行尝试 {len(tools)} 个外部工具路径和 Pillow 重编码兜底')

        tasks: list[CandidateTask] = []
        if 'gifsicle' in tools:
            levels = [2, 3] if profile != 'aggressive' else [3]
            for level in levels:
                tasks.append(
                    CandidateTask(
                        algorithm=f'gifsicle-o{level}',
                        start_message=f'开始生成候选 gifsicle-o{level}…',
                        build=lambda level=level: self._compress_with_command('gifsicle', payload, quality=level, source_suffix='.gif'),
                    )
                )

            lossy_levels = {
                'safe': [],
                'visual-lossless': [(3, 20)],
                'aggressive': [(3, 30), (3, 60)],
            }[profile]
            for quality in lossy_levels:
                tasks.append(
                    CandidateTask(
                        algorithm=f'gifsicle-o{quality[0]}-lossy{quality[1]}',
                        start_message=f'开始生成候选 gifsicle-o{quality[0]}-lossy{quality[1]}…',
                        build=lambda quality=quality: self._compress_with_command('gifsicle', payload, quality=quality, source_suffix='.gif'),
                    )
                )

        if 'gifsicle' not in tools:
            tasks.append(
                CandidateTask(
                    algorithm='gif-optimized',
                    start_message='开始生成候选 gif-optimized…',
                    build=lambda: self._encode_gif(payload),
                )
            )
        else:
            self._emit_progress(progress_callback, 'candidate', '检测到 gifsicle，跳过高内存的 Pillow GIF 重编码兜底')
        return self._run_candidate_tasks(tasks, progress_callback)

    def _compress_with_command(
        self,
        command: str,
        payload: bytes,
        quality: int | tuple[int, int] | tuple[str, int] | None,
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
        quality: int | tuple[int, int] | tuple[str, int] | None,
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
            quality_min, quality_max = quality if isinstance(quality, tuple) and isinstance(quality[0], int) else (85, 98)
            return ['pngquant', '--force', '--skip-if-larger', '--output', str(output_path), '--quality', f'{quality_min}-{quality_max}', '--', str(input_path)]
        if command == 'zopflipng':
            iterations = quality if isinstance(quality, int) else 15
            return ['zopflipng', f'--iterations={iterations}', '--filters=01234mepb', str(input_path), str(output_path)]
        if command == 'cwebp':
            if isinstance(quality, tuple) and quality[0] == 'lossless':
                return ['cwebp', '-lossless', '-q', str(quality[1]), '-m', '6', '-mt', str(input_path), '-o', str(output_path)]
            if isinstance(quality, tuple) and quality[0] == 'near_lossless':
                return ['cwebp', '-near_lossless', str(quality[1]), '-q', '100', '-m', '6', '-mt', str(input_path), '-o', str(output_path)]
            quality_value = 80 if not isinstance(quality, int) else quality
            return ['cwebp', '-q', str(quality_value), '-m', '6', '-mt', str(input_path), '-o', str(output_path)]
        if command == 'gifsicle':
            if isinstance(quality, tuple) and isinstance(quality[0], int):
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

    def _encode_webp(self, payload: bytes, lossless: bool, quality: int) -> bytes:
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

    def _encode_gif(self, payload: bytes) -> bytes:
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
        profile_thresholds = {
            'safe': (max(settings.ssim_threshold, 0.992), max(settings.psnr_threshold, 42.0)),
            'visual-lossless': (settings.ssim_threshold, settings.psnr_threshold),
            'aggressive': (0.97, 34.0),
        }
        gif_lossy_thresholds = {
            'safe': (1.0, 99.0),
            'visual-lossless': (max(settings.gif_lossy_ssim_threshold, 0.97), max(settings.gif_lossy_psnr_threshold, 38.0)),
            'aggressive': (settings.gif_lossy_ssim_threshold, settings.gif_lossy_psnr_threshold),
        }
        if candidate.algorithm.startswith('gifsicle-o') and 'lossy' in candidate.algorithm:
            return gif_lossy_thresholds[self._profile()]
        return profile_thresholds[self._profile()]

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
