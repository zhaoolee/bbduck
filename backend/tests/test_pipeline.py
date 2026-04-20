from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from app.services import metrics as metrics_module
from app.services.compress import CompressionService


def build_image_bytes(fmt: str = 'JPEG', size: tuple[int, int] = (48, 48), color: tuple[int, ...] = (110, 170, 230)) -> bytes:
    mode = 'RGBA' if len(color) == 4 else 'RGB'
    image = Image.new(mode, size, color=color)
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def build_alpha_png_bytes(alpha_value: int) -> bytes:
    image = Image.new('RGBA', (32, 32), color=(80, 160, 240, alpha_value))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def candidate_metrics(
    *,
    ssim: float = 0.99,
    psnr: float = 42.0,
    dimensions_equal: bool = True,
    alpha_safe: bool = True,
    animation_safe: bool = True,
) -> metrics_module.CandidateMetrics:
    return metrics_module.CandidateMetrics(
        ssim=ssim,
        psnr=psnr,
        dimensions_equal=dimensions_equal,
        alpha_safe=alpha_safe,
        animation_safe=animation_safe,
    )


def test_get_available_toolchain_detects_external_commands(monkeypatch):
    service = CompressionService()
    command_map = {
        'cjpeg': '/usr/bin/cjpeg',
        'pngquant': '/usr/bin/pngquant',
        'zopflipng': '/usr/bin/zopflipng',
        'cwebp': '/usr/bin/cwebp',
        'gifsicle': '/usr/bin/gifsicle',
    }

    monkeypatch.setattr('app.services.compress.which', lambda name: command_map.get(name))

    toolchain = service.get_available_toolchain()

    assert toolchain['jpeg'] == ['cjpeg']
    assert toolchain['png'] == ['pngquant', 'zopflipng']
    assert toolchain['webp'] == ['cwebp']
    assert toolchain['gif'] == ['gifsicle']


def test_build_candidates_prefers_external_jpeg_when_tool_exists(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('JPEG')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'balanced')
    monkeypatch.setattr(service, 'get_available_toolchain', lambda: {'jpeg': ['cjpeg'], 'png': [], 'webp': [], 'gif': []})
    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'external-jpeg-result')
    monkeypatch.setattr(service, '_compress_raster', lambda *args, **kwargs: b'pillow-jpeg-result')

    candidates = service._build_candidates('demo.jpg', payload, 'jpg')
    algorithms = [candidate.algorithm for candidate in candidates]

    assert candidates[0].algorithm == 'passthrough'
    assert candidates[1].algorithm.startswith('mozjpeg-cjpeg')
    assert 'jpeg-webp-lossless' not in algorithms


def test_choose_candidate_prefers_threshold_passing_result(monkeypatch):
    service = CompressionService()
    original = b'original' * 20

    candidates = [
        service.candidate_type(algorithm='passthrough', payload=original),
        service.candidate_type(algorithm='tool-a', payload=b'a' * 90),
        service.candidate_type(algorithm='tool-b', payload=b'b' * 80),
    ]

    metrics_map = {
        b'a' * 90: candidate_metrics(ssim=0.99, psnr=40.0),
        b'b' * 80: candidate_metrics(ssim=0.80, psnr=25.0),
    }

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            pass

        def compute(self, payload: bytes) -> tuple[float, float]:
            metrics = metrics_map[payload]
            return metrics.ssim, metrics.psnr

        def evaluate(self, payload: bytes, **_kwargs) -> metrics_module.CandidateMetrics:
            return metrics_map[payload]

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'balanced')

    chosen = service._choose_candidate(original, candidates)

    assert chosen.candidate.algorithm == 'tool-a'


def test_choose_candidate_returns_original_when_compressed_is_larger(monkeypatch):
    service = CompressionService()
    original = b'original-bytes'
    larger_candidate = service.candidate_type(algorithm='jpeg-pillow-q84', payload=b'x' * 200)
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            raise AssertionError('MetricEvaluator should not be created for oversized-only candidates')

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'balanced')

    chosen = service._choose_candidate(original, [passthrough, larger_candidate])

    assert chosen.candidate.algorithm == 'passthrough'


def test_build_png_candidates_uses_color_safe_defaults(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('PNG')

    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'command-result')
    monkeypatch.setattr(service, '_compress_raster', lambda *args, **kwargs: b'pillow-png')

    fidelity_candidates = service._build_png_candidates('demo.png', payload, ['pngquant', 'zopflipng'], 'fidelity')
    fidelity_algorithms = [candidate.algorithm for candidate in fidelity_candidates]
    assert 'pngquant-70-90' not in fidelity_algorithms
    assert 'pngquant-60-85' not in fidelity_algorithms
    assert 'zopflipng-i15' in fidelity_algorithms

    smallest_candidates = service._build_png_candidates('demo.png', payload, ['pngquant', 'zopflipng'], 'smallest')
    smallest_algorithms = [candidate.algorithm for candidate in smallest_candidates]
    assert 'pngquant-70-90' in smallest_algorithms


def test_compress_png_keeps_png_extension_and_mime(monkeypatch, tmp_path):
    service = CompressionService()
    payload = build_image_bytes('PNG')

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        service,
        '_build_candidates',
        lambda file_name, payload, suffix: [
            service.candidate_type(algorithm='passthrough', payload=payload),
            service.candidate_type(algorithm='png-pillow-optimize', payload=b'png-result'),
        ],
    )
    monkeypatch.setattr(
        service,
        '_choose_candidate',
        lambda original, candidates, suffix=None: service.assessment_type(candidate=candidates[1], ssim=1.0, psnr=99.0),
    )

    item = service.compress_bytes('demo.png', payload)

    assert item.compressed_url.endswith('.compressed.png?kind=output')
    assert item.mime_type == 'image/png'
    assert item.algorithm == 'png-pillow-optimize'


def test_pillow_save_kwargs_preserve_icc_profile():
    service = CompressionService()
    image = Image.new('RGB', (10, 10), color=(255, 0, 0))
    image.info['icc_profile'] = b'fake-icc-profile'

    save_kwargs = service._build_pillow_save_kwargs(image=image, fmt='JPEG', quality=82, optimize=True)

    assert save_kwargs['icc_profile'] == b'fake-icc-profile'
    assert save_kwargs['quality'] == 82
    assert save_kwargs['subsampling'] == 0


def test_build_gif_candidates_adds_lossy_variant_only_outside_fidelity(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('GIF')

    def fake_compress(command: str, payload: bytes, quality, source_suffix: str, profile: str) -> bytes:
        if command != 'gifsicle':
            raise AssertionError(f'unexpected command: {command}')
        if quality == 2:
            return b'gifsicle-o2'
        if quality == 3:
            return b'gifsicle-o3'
        if quality == (3, 30):
            return b'gifsicle-o3-lossy30'
        raise AssertionError(f'unexpected quality: {quality!r}')

    monkeypatch.setattr(service, '_compress_with_command', fake_compress)
    monkeypatch.setattr(service, '_compress_gif', lambda payload: b'gif-pillow')

    fidelity_algorithms = [candidate.algorithm for candidate in service._build_gif_candidates(payload, ['gifsicle'], 'fidelity')]
    balanced_algorithms = [candidate.algorithm for candidate in service._build_gif_candidates(payload, ['gifsicle'], 'balanced')]

    assert fidelity_algorithms == ['gifsicle-o2', 'gifsicle-o3']
    assert balanced_algorithms == ['gifsicle-o2', 'gifsicle-o3', 'gifsicle-o3-lossy30', 'gif-optimized']


def test_build_command_line_supports_gifsicle_lossy_tuple():
    service = CompressionService()

    command = service._build_command_line(
        'gifsicle',
        Path('/tmp/input.gif'),
        Path('/tmp/output.gif'),
        (3, 30),
        profile='balanced',
    )

    assert command == ['gifsicle', '-O3', '--lossy=30', '/tmp/input.gif', '-o', '/tmp/output.gif']


def test_choose_candidate_accepts_gifsicle_lossy_variant_with_relaxed_gif_thresholds(monkeypatch):
    service = CompressionService()
    original = b'original-bytes' * 20
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    normal = service.candidate_type(algorithm='gifsicle-o3', payload=b'a' * 220)
    lossy = service.candidate_type(algorithm='gifsicle-o3-lossy30', payload=b'b' * 140)

    metrics_map = {
        normal.payload: candidate_metrics(ssim=1.0, psnr=99.0),
        lossy.payload: candidate_metrics(ssim=0.9569, psnr=36.7),
    }

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            pass

        def compute(self, payload: bytes) -> tuple[float, float]:
            metrics = metrics_map[payload]
            return metrics.ssim, metrics.psnr

        def evaluate(self, payload: bytes, **_kwargs) -> metrics_module.CandidateMetrics:
            return metrics_map[payload]

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'balanced')
    monkeypatch.setattr('app.services.compress.settings.ssim_threshold', 0.985)
    monkeypatch.setattr('app.services.compress.settings.psnr_threshold', 40.0)
    monkeypatch.setattr('app.services.compress.settings.gif_lossy_ssim_threshold', 0.95, raising=False)
    monkeypatch.setattr('app.services.compress.settings.gif_lossy_psnr_threshold', 36.0, raising=False)

    chosen = service._choose_candidate(original, [passthrough, normal, lossy], suffix='gif')

    assert chosen.candidate.algorithm == 'gifsicle-o3-lossy30'


def test_choose_candidate_skips_metrics_for_candidates_not_smaller(monkeypatch):
    service = CompressionService()
    original = b'original-bytes'
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    smaller = service.candidate_type(algorithm='jpeg-pillow-q92', payload=b'a' * 8)
    larger = service.candidate_type(algorithm='jpeg-webp-lossless', payload=b'b' * 200)

    calls: list[bytes] = []

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            pass

        def compute(self, payload: bytes) -> tuple[float, float]:
            calls.append(payload)
            return (0.99, 42.0)

        def evaluate(self, payload: bytes, **_kwargs) -> metrics_module.CandidateMetrics:
            calls.append(payload)
            return candidate_metrics(ssim=0.99, psnr=42.0)

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'balanced')

    chosen = service._choose_candidate(original, [passthrough, larger, smaller])

    assert chosen.candidate.algorithm == 'jpeg-pillow-q92'
    assert calls == [smaller.payload]


def test_metric_evaluator_reuses_original_decode(monkeypatch):
    original = b'original-image'
    candidate_a = b'candidate-a'
    candidate_b = b'candidate-b'

    payload_map = {
        original: np.zeros((24, 24, 3), dtype=np.uint8),
        candidate_a: np.ones((24, 24, 3), dtype=np.uint8),
        candidate_b: np.full((24, 24, 3), 2, dtype=np.uint8),
    }
    calls: list[bytes] = []

    def fake_load_rgb(image_bytes: bytes) -> np.ndarray:
        calls.append(image_bytes)
        return payload_map[image_bytes]

    class FakeAnalysis:
        def __init__(self, rgb: np.ndarray):
            self.rgb = rgb
            self.rgba = np.dstack([rgb, np.full(rgb.shape[:2], 255, dtype=np.uint8)])
            self.alpha = None
            self.size = (rgb.shape[1], rgb.shape[0])
            self.has_alpha = False
            self.frame_count = 1
            self.loop = 0
            self.durations = [0]
            self.disposals = [0]
            self.sampled_rgba_frames = [self.rgba]

    monkeypatch.setattr(metrics_module, '_load_rgb', fake_load_rgb)
    monkeypatch.setattr(metrics_module, '_load_analysis', lambda image_bytes: FakeAnalysis(fake_load_rgb(image_bytes)))
    monkeypatch.setattr(metrics_module, 'structural_similarity', lambda *_args, **_kwargs: 0.99)
    monkeypatch.setattr(metrics_module, 'peak_signal_noise_ratio', lambda *_args, **_kwargs: 42.0)

    evaluator = metrics_module.MetricEvaluator(original)
    evaluator.compute(candidate_a)
    evaluator.compute(candidate_b)

    assert calls == [original, candidate_a, candidate_b]


def test_compress_bytes_sanitizes_uploaded_filename(monkeypatch, tmp_path):
    service = CompressionService()
    payload = b'gif-data'

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        service,
        '_build_candidates',
        lambda file_name, payload, suffix: [service.candidate_type(algorithm='passthrough', payload=payload)],
    )

    item = service.compress_bytes('../../escape.gif', payload)

    upload_files = list((tmp_path / 'uploads').iterdir())
    output_files = list((tmp_path / 'output').iterdir())

    assert len(upload_files) == 1
    assert len(output_files) == 1
    assert upload_files[0].parent == tmp_path / 'uploads'
    assert output_files[0].parent == tmp_path / 'output'
    assert 'escape.gif' in upload_files[0].name
    assert '..' not in upload_files[0].name
    assert item.file_name == '../../escape.gif'


def test_compute_metrics_downscales_before_expensive_similarity(monkeypatch):
    original = Image.new('RGB', (3200, 1600), color=(10, 20, 30))
    candidate = Image.new('RGB', (3200, 1600), color=(12, 22, 32))

    original_buffer = BytesIO()
    candidate_buffer = BytesIO()
    original.save(original_buffer, format='PNG')
    candidate.save(candidate_buffer, format='PNG')

    observed_shapes: list[tuple[tuple[int, ...], tuple[int, ...]]] = []

    def fake_ssim(left: np.ndarray, right: np.ndarray, channel_axis: int, data_range: int) -> float:
        observed_shapes.append((left.shape, right.shape))
        assert data_range == 255
        return 0.99

    def fake_psnr(left: np.ndarray, right: np.ndarray, data_range: int) -> float:
        observed_shapes.append((left.shape, right.shape))
        return 42.0

    monkeypatch.setattr(metrics_module, 'structural_similarity', fake_ssim)
    monkeypatch.setattr(metrics_module, 'peak_signal_noise_ratio', fake_psnr)

    ssim, psnr = metrics_module.compute_metrics(original_buffer.getvalue(), candidate_buffer.getvalue())

    assert (ssim, psnr) == (0.99, 42.0)
    assert observed_shapes
    height, width, _channels = observed_shapes[0][0]
    assert max(height, width) <= 768


def test_fidelity_mode_rejects_dimension_change(monkeypatch):
    service = CompressionService()
    original = b'original' * 20
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    resized = service.candidate_type(algorithm='jpeg-pillow-q96', payload=b'a' * 80)

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            pass

        def evaluate(self, payload: bytes, **_kwargs) -> metrics_module.CandidateMetrics:
            return candidate_metrics(dimensions_equal=False)

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'fidelity')

    chosen = service._choose_candidate(original, [passthrough, resized], suffix='png')

    assert chosen.candidate.algorithm == 'passthrough'
    rejected = next(item for item in service.last_assessments if item.candidate.algorithm == 'jpeg-pillow-q96')
    assert rejected.rejection_reason == 'dimension_mismatch'


def test_fidelity_mode_rejects_transparent_png_when_alpha_changes(monkeypatch):
    service = CompressionService()
    original = b'original' * 40
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    alpha_changed = service.candidate_type(algorithm='png-pillow-optimize', payload=b'a' * 120)

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            pass

        def evaluate(self, payload: bytes, **_kwargs) -> metrics_module.CandidateMetrics:
            return candidate_metrics(alpha_safe=False)

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'fidelity')

    chosen = service._choose_candidate(original, [passthrough, alpha_changed], suffix='png')

    assert chosen.candidate.algorithm == 'passthrough'
    rejected = next(item for item in service.last_assessments if item.candidate.algorithm == 'png-pillow-optimize')
    assert rejected.rejection_reason == 'alpha_not_safe'


def test_fidelity_mode_prefers_higher_quality_candidate_over_smallest(monkeypatch):
    service = CompressionService()
    original = b'original-data' * 40
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    smallest = service.candidate_type(algorithm='jpeg-pillow-q90', payload=b'a' * 200)
    safer = service.candidate_type(algorithm='mozjpeg-cjpeg-q96', payload=b'b' * 220)

    metrics_map = {
        smallest.payload: candidate_metrics(ssim=0.992, psnr=42.0),
        safer.payload: candidate_metrics(ssim=0.999, psnr=50.0),
    }

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            pass

        def evaluate(self, payload: bytes, **_kwargs) -> metrics_module.CandidateMetrics:
            return metrics_map[payload]

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'fidelity')

    chosen = service._choose_candidate(original, [passthrough, smallest, safer], suffix='jpg')

    assert chosen.candidate.algorithm == 'mozjpeg-cjpeg-q96'


def test_fidelity_mode_returns_original_when_savings_below_threshold(monkeypatch):
    service = CompressionService()
    original = b'original-bytes' * 50
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    tiny_saving = service.candidate_type(algorithm='jpeg-pillow-q96', payload=original[:-1])

    class FakeEvaluator:
        def __init__(self, _original: bytes):
            raise AssertionError('below minimum savings should short-circuit before metric evaluation')

    monkeypatch.setattr('app.services.compress.MetricEvaluator', FakeEvaluator)
    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'fidelity')
    monkeypatch.setattr('app.services.compress.settings.min_compression_saving_percent', 3.0)

    chosen = service._choose_candidate(original, [passthrough, tiny_saving], suffix='jpg')

    assert chosen.candidate.algorithm == 'passthrough'
    rejected = next(item for item in service.last_assessments if item.candidate.algorithm == 'jpeg-pillow-q96')
    assert rejected.rejection_reason == 'below_minimum_savings'


def test_build_candidates_falls_back_when_external_tools_are_unavailable(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('WEBP')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'balanced')
    monkeypatch.setattr(service, 'get_available_toolchain', lambda: {'jpeg': [], 'png': [], 'webp': [], 'gif': []})
    monkeypatch.setattr(service, '_compress_webp', lambda *args, **kwargs: b'pillow-webp')

    candidates = service._build_candidates('demo.webp', payload, 'webp')
    algorithms = [candidate.algorithm for candidate in candidates]

    assert algorithms[0] == 'passthrough'
    assert 'cwebp-q85' not in algorithms
    assert 'webp-q85' in algorithms


def test_compute_alpha_and_composited_metrics_detect_transparency_change():
    original = build_alpha_png_bytes(255)
    candidate = build_alpha_png_bytes(120)

    alpha_ssim, alpha_psnr = metrics_module.compute_alpha_metrics(original, candidate)
    white_ssim, white_psnr = metrics_module.compute_composited_metrics(original, candidate)
    black_ssim, black_psnr = metrics_module.compute_composited_metrics(original, candidate, background=(0, 0, 0, 255))

    assert alpha_ssim < 1.0
    assert alpha_psnr < 99.0
    assert white_ssim < 1.0
    assert white_psnr < 99.0
    assert black_ssim < 1.0
    assert black_psnr < 99.0
