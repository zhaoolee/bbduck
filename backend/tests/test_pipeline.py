from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from app.services.compress import CompressionService
from app.services import metrics as metrics_module


def build_image_bytes(fmt: str = 'JPEG') -> bytes:
    image = Image.new('RGB', (48, 48), color=(110, 170, 230))
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


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

    monkeypatch.setattr(service, 'get_available_toolchain', lambda: {'jpeg': ['cjpeg'], 'png': [], 'webp': [], 'gif': []})
    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'external-jpeg-result')
    monkeypatch.setattr(service, '_compress_raster', lambda *args, **kwargs: b'pillow-jpeg-result')

    candidates = service._build_candidates('demo.jpg', payload, 'jpg')
    algorithms = [candidate.algorithm for candidate in candidates]

    assert candidates[0].algorithm == 'passthrough'
    assert 'jpeg-webp-lossless' not in algorithms
    assert candidates[1].algorithm.startswith('mozjpeg-cjpeg')


def test_compress_bytes_dispatches_to_jpeg_entry(monkeypatch, tmp_path):
    service = CompressionService()
    payload = build_image_bytes('JPEG')
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    def fake_entry(file_name: str, entry_payload: bytes, *_args, **_kwargs):
        calls.append((file_name, 'jpeg'))
        return service.assessment_type(candidate=service.candidate_type(algorithm='jpeg-pillow-q92', payload=b'jpeg-result'), ssim=0.99, psnr=42.0)

    monkeypatch.setattr(service, '_compress_jpeg', fake_entry)
    monkeypatch.setattr(service, '_compress_png', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected png entry')))
    monkeypatch.setattr(service, '_compress_webp', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected webp entry')))
    monkeypatch.setattr(service, '_compress_gif', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected gif entry')))

    item = service.compress_bytes('photo.jpg', payload)

    assert calls == [('photo.jpg', 'jpeg')]
    assert item.algorithm == 'jpeg-pillow-q92'


def test_compress_bytes_dispatches_to_png_entry(monkeypatch, tmp_path):
    service = CompressionService()
    payload = build_image_bytes('PNG')
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    def fake_entry(file_name: str, entry_payload: bytes, *_args, **_kwargs):
        calls.append((file_name, 'png'))
        return service.assessment_type(candidate=service.candidate_type(algorithm='png-pillow-optimize', payload=b'png-result'), ssim=1.0, psnr=99.0)

    monkeypatch.setattr(service, '_compress_png', fake_entry)
    monkeypatch.setattr(service, '_compress_jpeg', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected jpeg entry')))
    monkeypatch.setattr(service, '_compress_webp', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected webp entry')))
    monkeypatch.setattr(service, '_compress_gif', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected gif entry')))

    item = service.compress_bytes('graphic.png', payload)

    assert calls == [('graphic.png', 'png')]
    assert item.algorithm == 'png-pillow-optimize'


def test_compress_bytes_dispatches_to_webp_entry(monkeypatch, tmp_path):
    service = CompressionService()
    payload = build_image_bytes('WEBP')
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    def fake_entry(file_name: str, entry_payload: bytes, *_args, **_kwargs):
        calls.append((file_name, 'webp'))
        return service.assessment_type(candidate=service.candidate_type(algorithm='webp-q85', payload=b'webp-result'), ssim=0.99, psnr=41.0)

    monkeypatch.setattr(service, '_compress_webp', fake_entry)
    monkeypatch.setattr(service, '_compress_jpeg', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected jpeg entry')))
    monkeypatch.setattr(service, '_compress_png', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected png entry')))
    monkeypatch.setattr(service, '_compress_gif', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected gif entry')))

    item = service.compress_bytes('clip.webp', payload)

    assert calls == [('clip.webp', 'webp')]
    assert item.algorithm == 'webp-q85'


def test_compress_bytes_dispatches_to_gif_entry(monkeypatch, tmp_path):
    service = CompressionService()
    payload = build_image_bytes('GIF')
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    def fake_entry(file_name: str, entry_payload: bytes, *_args, **_kwargs):
        calls.append((file_name, 'gif'))
        return service.assessment_type(candidate=service.candidate_type(algorithm='gif-optimized', payload=b'gif-result'), ssim=0.98, psnr=38.0)

    monkeypatch.setattr(service, '_compress_gif', fake_entry)
    monkeypatch.setattr(service, '_compress_jpeg', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected jpeg entry')))
    monkeypatch.setattr(service, '_compress_png', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected png entry')))
    monkeypatch.setattr(service, '_compress_webp', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected webp entry')))

    item = service.compress_bytes('anim.gif', payload)

    assert calls == [('anim.gif', 'gif')]
    assert item.algorithm == 'gif-optimized'


def test_choose_candidate_prefers_threshold_passing_result(monkeypatch):
    service = CompressionService()
    original = b'original' * 20

    candidates = [
        service.candidate_type(algorithm='passthrough', payload=original),
        service.candidate_type(algorithm='tool-a', payload=b'a' * 90),
        service.candidate_type(algorithm='tool-b', payload=b'b' * 80),
    ]

    metrics_map = {
        original: (1.0, 99.0),
        b'a' * 90: (0.99, 40.0),
        b'b' * 80: (0.80, 25.0),
    }
    monkeypatch.setattr('app.services.compress.compute_metrics', lambda _orig, payload: metrics_map[payload])

    chosen = service._choose_candidate(original, candidates)

    assert chosen.candidate.algorithm == 'tool-a'


def test_choose_candidate_returns_original_when_compressed_is_larger(monkeypatch):
    service = CompressionService()
    original = b'original-bytes'
    larger_candidate = service.candidate_type(algorithm='jpeg-pillow-q84', payload=b'x' * 200)
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)

    metrics_map = {
        original: (1.0, 99.0),
        b'x' * 200: (0.99, 42.0),
    }
    monkeypatch.setattr('app.services.compress.compute_metrics', lambda _orig, payload: metrics_map[payload])

    chosen = service._choose_candidate(original, [passthrough, larger_candidate])

    assert chosen.candidate.algorithm == 'passthrough'


def test_build_png_candidates_uses_visual_lossless_defaults(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('PNG')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'visual-lossless', raising=False)
    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'command-result')
    monkeypatch.setattr(service, '_compress_raster', lambda *args, **kwargs: b'pillow-png')

    candidates = service._build_png_candidates('demo.png', payload, ['pngquant', 'zopflipng'])
    algorithms = [candidate.algorithm for candidate in candidates]

    assert 'pngquant-85-98' in algorithms
    assert 'zopflipng-i15' not in algorithms
    assert 'zopflipng-i50' not in algorithms
    assert 'png-pillow-optimize' in algorithms


def test_build_png_candidates_safe_mode_skips_pngquant_and_limits_zopflipng(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('PNG')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'safe', raising=False)
    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'command-result')
    monkeypatch.setattr(service, '_compress_raster', lambda *args, **kwargs: b'pillow-png')

    candidates = service._build_png_candidates('demo.png', payload, ['pngquant', 'zopflipng'])
    algorithms = [candidate.algorithm for candidate in candidates]

    assert 'pngquant-85-98' not in algorithms
    assert 'zopflipng-i15' in algorithms
    assert 'zopflipng-i50' not in algorithms


def test_build_webp_candidates_visual_lossless_adds_lossless_and_near_lossless(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('WEBP')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'visual-lossless', raising=False)
    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'command-result')
    monkeypatch.setattr(service, '_encode_webp', lambda *args, **kwargs: b'pillow-webp')

    candidates = service._build_webp_candidates(payload, ['cwebp'])
    algorithms = [candidate.algorithm for candidate in candidates]

    assert 'cwebp-lossless' in algorithms
    assert 'cwebp-near-lossless-80' in algorithms
    assert 'cwebp-q90' in algorithms


def test_build_jpeg_candidates_visual_lossless_uses_high_quality_ladder(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('JPEG')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'visual-lossless', raising=False)
    monkeypatch.setattr(service, '_compress_with_command', lambda *args, **kwargs: b'jpeg-command')
    monkeypatch.setattr(service, '_compress_raster', lambda *args, **kwargs: b'jpeg-pillow')

    candidates = service._build_jpeg_candidates('demo.jpg', payload, ['jpegtran', 'cjpeg'])
    algorithms = [candidate.algorithm for candidate in candidates]

    assert 'mozjpeg-cjpeg-q96' in algorithms
    assert 'mozjpeg-cjpeg-q92' in algorithms
    assert 'jpeg-pillow-q96' in algorithms


def test_build_gif_candidates_visual_lossless_keeps_light_lossy_option(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('GIF')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'visual-lossless', raising=False)

    def fake_compress(command: str, payload: bytes, quality, source_suffix: str) -> bytes:
        if command != 'gifsicle':
            raise AssertionError(f'unexpected command: {command}')
        if quality == 2:
            return b'gifsicle-o2'
        if quality == 3:
            return b'gifsicle-o3'
        if quality == (3, 20):
            return b'gifsicle-o3-lossy20'
        raise AssertionError(f'unexpected quality: {quality!r}')

    monkeypatch.setattr(service, '_compress_with_command', fake_compress)
    monkeypatch.setattr(service, '_encode_gif', lambda payload: b'gif-pillow')

    candidates = service._build_gif_candidates(payload, ['gifsicle'])
    algorithms = [candidate.algorithm for candidate in candidates]

    assert algorithms == ['gifsicle-o2', 'gifsicle-o3', 'gifsicle-o3-lossy20', 'gif-optimized']


def test_compress_png_keeps_png_extension_and_mime(monkeypatch, tmp_path):
    service = CompressionService()
    payload = build_image_bytes('PNG')

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        service,
        '_compress_png',
        lambda file_name, payload, *_args, **_kwargs: service.assessment_type(
            candidate=service.candidate_type(algorithm='png-pillow-optimize', payload=b'png-result'),
            ssim=1.0,
            psnr=99.0,
        ),
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


def test_build_gif_candidates_adds_lossy_gifsicle_variant(monkeypatch):
    service = CompressionService()
    payload = build_image_bytes('GIF')

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'aggressive', raising=False)

    def fake_compress(command: str, payload: bytes, quality, source_suffix: str) -> bytes:
        if command != 'gifsicle':
            raise AssertionError(f'unexpected command: {command}')
        if quality == 3:
            return b'gifsicle-o3'
        if quality == (3, 30):
            return b'gifsicle-o3-lossy30'
        if quality == (3, 60):
            return b'gifsicle-o3-lossy60'
        raise AssertionError(f'unexpected quality: {quality!r}')

    monkeypatch.setattr(service, '_compress_with_command', fake_compress)
    monkeypatch.setattr(service, '_encode_gif', lambda payload: b'gif-pillow')

    candidates = service._build_gif_candidates(payload, ['gifsicle'])
    algorithms = [candidate.algorithm for candidate in candidates]

    assert algorithms == ['gifsicle-o3', 'gifsicle-o3-lossy30', 'gifsicle-o3-lossy60', 'gif-optimized']


def test_build_command_line_supports_gifsicle_lossy_tuple():
    service = CompressionService()

    command = service._build_command_line(
        'gifsicle',
        Path('/tmp/input.gif'),
        Path('/tmp/output.gif'),
        (3, 30),
    )

    assert command == ['gifsicle', '-O3', '--lossy=30', '/tmp/input.gif', '-o', '/tmp/output.gif']


def test_choose_candidate_accepts_gifsicle_lossy_variant_with_relaxed_gif_thresholds(monkeypatch):
    service = CompressionService()
    original = b'original-bytes' * 20
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    normal = service.candidate_type(algorithm='gifsicle-o3', payload=b'a' * 220)
    lossy = service.candidate_type(algorithm='gifsicle-o3-lossy30', payload=b'b' * 140)

    metrics_map = {
        normal.payload: (1.0, 99.0),
        lossy.payload: (0.9569, 36.7),
    }

    monkeypatch.setattr('app.services.compress.settings.compression_profile', 'aggressive', raising=False)
    monkeypatch.setattr('app.services.compress.compute_metrics', lambda _orig, payload: metrics_map[payload])
    monkeypatch.setattr('app.services.compress.settings.ssim_threshold', 0.985)
    monkeypatch.setattr('app.services.compress.settings.psnr_threshold', 40.0)
    monkeypatch.setattr('app.services.compress.settings.gif_lossy_ssim_threshold', 0.95, raising=False)
    monkeypatch.setattr('app.services.compress.settings.gif_lossy_psnr_threshold', 36.0, raising=False)

    chosen = service._choose_candidate(original, [passthrough, normal, lossy])

    assert chosen.candidate.algorithm == 'gifsicle-o3-lossy30'


def test_choose_candidate_skips_metrics_for_candidates_not_smaller(monkeypatch):
    service = CompressionService()
    original = b'original-bytes'
    passthrough = service.candidate_type(algorithm='passthrough', payload=original)
    smaller = service.candidate_type(algorithm='jpeg-pillow-q92', payload=b'a' * 8)
    larger = service.candidate_type(algorithm='jpeg-webp-lossless', payload=b'b' * 200)

    calls: list[bytes] = []

    def fake_metrics(_orig: bytes, payload: bytes) -> tuple[float, float]:
        calls.append(payload)
        return (0.99, 42.0)

    monkeypatch.setattr('app.services.compress.compute_metrics', fake_metrics)

    chosen = service._choose_candidate(original, [passthrough, larger, smaller])

    assert chosen.candidate.algorithm == 'jpeg-pillow-q92'
    assert calls == [smaller.payload]


def test_compress_bytes_sanitizes_uploaded_filename(monkeypatch, tmp_path):
    service = CompressionService()
    payload = b'gif-data'

    monkeypatch.setattr('app.services.compress.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        service,
        '_compress_gif',
        lambda file_name, payload, *_args, **_kwargs: service.assessment_type(
            candidate=service.candidate_type(algorithm='passthrough', payload=payload),
            ssim=1.0,
            psnr=99.0,
        ),
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

    def fake_ssim(left: np.ndarray, right: np.ndarray, channel_axis: int) -> float:
        observed_shapes.append((left.shape, right.shape))
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
