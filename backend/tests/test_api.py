from io import BytesIO
import json
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes
from app.main import app
import app.main as app_main
import app.services.evaluation_images as evaluation_images_service
from app.schemas import CompressionItem, CompressionMetrics


client = TestClient(app)


def build_png_bytes() -> bytes:
    image = Image.new('RGB', (32, 32), color=(20, 160, 120))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def build_unoptimized_png_bytes(size: tuple[int, int] = (256, 256)) -> bytes:
    image = Image.new('RGB', size, color=(20, 160, 120))
    buffer = BytesIO()
    image.save(buffer, format='PNG', compress_level=0)
    return buffer.getvalue()


def test_health_endpoint_returns_ok():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_spa_serves_root_level_static_files_from_frontend_dist(tmp_path, monkeypatch):
    static_file = tmp_path / 'bbduck-logo.png'
    static_file.write_bytes(b'fake-png-bytes')
    (tmp_path / 'index.html').write_text('<!doctype html><html><body>fallback</body></html>', encoding='utf-8')

    monkeypatch.setattr(app_main, 'frontend_dist', tmp_path)

    response = client.get('/bbduck-logo.png')

    assert response.status_code == 200
    assert response.content == b'fake-png-bytes'
    assert response.headers['content-type'] == 'image/png'


def test_config_endpoint_exposes_supported_formats():
    response = client.get('/api/config')
    assert response.status_code == 200
    payload = response.json()
    assert 'png' in payload['allowed_formats']
    assert payload['max_files'] >= 1
    assert payload['compression_profile'] in {'safe', 'visual-lossless', 'aggressive'}
    assert payload['min_compression_saving_percent'] >= 0


def test_evaluation_images_endpoint_lists_supported_files_in_natural_order(tmp_path, monkeypatch):
    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_dir.mkdir()
    evaluation_compressed_dir.mkdir()
    (tmp_path / 'output').mkdir()

    files = {
        '10.png': 'PNG',
        '2.jpg': 'JPEG',
        '003.webp': 'WEBP',
        '0004.gif': 'GIF',
        '11.jpeg': 'JPEG',
    }
    for name, image_format in files.items():
        image = Image.new('RGB', (24, 24), color=(20, 160, 120))
        image.save(evaluation_dir / name, format=image_format)

    Image.new('RGB', (24, 24), color=(180, 180, 180)).save(evaluation_compressed_dir / '2.webp', format='WEBP')
    Image.new('RGB', (24, 24), color=(180, 180, 180)).save(evaluation_compressed_dir / '003.compressed.png', format='PNG')
    Image.new('RGB', (24, 24), color=(180, 180, 180)).save(evaluation_compressed_dir / '0004.gif', format='GIF')
    Image.new('RGB', (24, 24), color=(180, 180, 180)).save(evaluation_compressed_dir / '10.png', format='PNG')
    Image.new('RGB', (24, 24), color=(180, 180, 180)).save(evaluation_compressed_dir / '11.jpeg', format='JPEG')

    (evaluation_dir / '.hidden.png').write_bytes(build_png_bytes())
    (evaluation_dir / 'notes.txt').write_text('ignore me', encoding='utf-8')
    (evaluation_dir / 'nested').mkdir()
    Image.new('RGB', (24, 24), color=(255, 0, 0)).save(evaluation_dir / 'nested' / '1.png', format='PNG')

    monkeypatch.setattr(routes.settings, 'data_dir', tmp_path)
    monkeypatch.setattr(routes.settings, 'evaluation_images_dir', evaluation_dir)
    monkeypatch.setattr(routes.settings, 'evaluation_compressed_dir', evaluation_compressed_dir, raising=False)
    monkeypatch.setattr(evaluation_images_service, '_build_compression_item', lambda _path: (_ for _ in ()).throw(AssertionError('runtime compression should not run when packaged assets exist')))
    original_read_bytes = Path.read_bytes

    def fail_evaluation_read_bytes(path: Path):
        if evaluation_dir in path.parents or evaluation_compressed_dir in path.parents:
            raise AssertionError('prebuilt evaluation metadata should use stat only during API listing')
        return original_read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', fail_evaluation_read_bytes)

    response = client.get('/api/evaluation-images')

    assert response.status_code == 200
    payload = response.json()
    assert [item['file_name'] for item in payload['items']] == ['2.jpg', '003.webp', '0004.gif', '10.png', '11.jpeg']
    assert all(item['status'] in {'completed', 'skipped'} for item in payload['items'])
    assert [urlparse(item['compressed_url']).path for item in payload['items']] == [
        '/api/evaluation-compressed/2.webp',
        '/api/evaluation-compressed/003.compressed.png',
        '/api/evaluation-compressed/0004.gif',
        '/api/evaluation-compressed/10.png',
        '/api/evaluation-compressed/11.jpeg',
    ]
    assert all(parse_qs(urlparse(item['compressed_url']).query).get('v') for item in payload['items'])
    assert all(item['compressed_url'] != item['original_url'] for item in payload['items'])
    assert all(item['algorithm'] == 'evaluation-prebuilt' for item in payload['items'])
    assert all(item['metrics']['compression_ratio'] >= 0 for item in payload['items'])
    assert all(item['metrics']['ssim'] >= 0 for item in payload['items'])
    assert all(item['metrics']['psnr'] >= 0 for item in payload['items'])
    assert urlparse(payload['items'][0]['original_url']).path == '/api/evaluation-images/2.jpg'


def test_evaluation_images_endpoint_busts_browser_cache_when_same_name_file_changes(tmp_path, monkeypatch):
    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_dir.mkdir()
    evaluation_compressed_dir.mkdir()
    (tmp_path / 'output').mkdir()

    image_path = evaluation_dir / '00001.png'
    compressed_path = evaluation_compressed_dir / '00001.png'
    image_path.write_bytes(build_png_bytes())
    compressed_path.write_bytes(build_png_bytes())

    monkeypatch.setattr(routes.settings, 'data_dir', tmp_path)
    monkeypatch.setattr(routes.settings, 'evaluation_images_dir', evaluation_dir)
    monkeypatch.setattr(routes.settings, 'evaluation_compressed_dir', evaluation_compressed_dir, raising=False)

    first_item = client.get('/api/evaluation-images').json()['items'][0]

    compressed_path.write_bytes(build_unoptimized_png_bytes(size=(48, 48)))
    # Ensure mtime changes even on filesystems with coarse timestamp behavior.
    next_mtime = compressed_path.stat().st_mtime_ns + 10_000_000
    compressed_path.touch()
    import os
    os.utime(compressed_path, ns=(next_mtime, next_mtime))

    second_item = client.get('/api/evaluation-images').json()['items'][0]

    assert urlparse(first_item['compressed_url']).path == '/api/evaluation-compressed/00001.png'
    assert urlparse(second_item['compressed_url']).path == '/api/evaluation-compressed/00001.png'
    assert first_item['compressed_url'] != second_item['compressed_url']
    assert parse_qs(urlparse(first_item['compressed_url']).query).get('v')
    assert parse_qs(urlparse(second_item['compressed_url']).query).get('v')


def test_evaluation_images_endpoint_falls_back_to_runtime_compression_when_packaged_asset_missing(tmp_path, monkeypatch):
    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_dir.mkdir()
    evaluation_compressed_dir.mkdir()
    (tmp_path / 'output').mkdir()

    image_path = evaluation_dir / 'sample.png'
    image_path.write_bytes(build_unoptimized_png_bytes())

    monkeypatch.setattr(routes.settings, 'data_dir', tmp_path)
    monkeypatch.setattr(routes.settings, 'evaluation_images_dir', evaluation_dir)
    monkeypatch.setattr(routes.settings, 'evaluation_compressed_dir', evaluation_compressed_dir, raising=False)

    response = client.get('/api/evaluation-images')

    assert response.status_code == 200
    payload = response.json()
    assert len(payload['items']) == 1

    item = payload['items'][0]
    assert item['file_name'] == 'sample.png'
    assert urlparse(item['original_url']).path == '/api/evaluation-images/sample.png'
    assert item['compressed_url'] != item['original_url']
    assert item['compressed_url'].startswith('/api/files/')
    assert item['status'] == 'completed'
    assert item['compressed_size'] < item['original_size']
    assert item['algorithm'] != 'evaluation-demo'
    assert item['metrics']['compression_ratio'] > 0

    original_response = client.get(item['original_url'])
    compressed_response = client.get(item['compressed_url'])

    assert original_response.status_code == 200
    assert compressed_response.status_code == 200
    assert len(original_response.content) == item['original_size']
    assert len(compressed_response.content) == item['compressed_size']
    assert compressed_response.content != original_response.content


def test_evaluation_images_endpoint_reuses_cache_until_source_changes(tmp_path, monkeypatch):
    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_dir.mkdir()
    evaluation_compressed_dir.mkdir()
    (tmp_path / 'output').mkdir()

    image_path = evaluation_dir / 'sample.png'
    image_path.write_bytes(build_unoptimized_png_bytes())

    monkeypatch.setattr(routes.settings, 'data_dir', tmp_path)
    monkeypatch.setattr(routes.settings, 'evaluation_images_dir', evaluation_dir)
    monkeypatch.setattr(routes.settings, 'evaluation_compressed_dir', evaluation_compressed_dir, raising=False)

    original_builder = evaluation_images_service._build_compression_item
    build_calls = 0

    def tracking_builder(path):
        nonlocal build_calls
        build_calls += 1
        return original_builder(path)

    monkeypatch.setattr(evaluation_images_service, '_build_compression_item', tracking_builder)

    first_response = client.get('/api/evaluation-images')
    second_response = client.get('/api/evaluation-images')

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert build_calls == 1

    first_item = first_response.json()['items'][0]
    second_item = second_response.json()['items'][0]
    assert second_item['compressed_url'] == first_item['compressed_url']

    image_path.write_bytes(build_unoptimized_png_bytes(size=(300, 300)))
    third_response = client.get('/api/evaluation-images')

    assert third_response.status_code == 200
    assert build_calls == 2
    third_item = third_response.json()['items'][0]
    assert third_item['compressed_url'] != first_item['compressed_url']


def test_evaluation_images_endpoint_falls_back_per_image_when_compression_fails(tmp_path, monkeypatch):
    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_dir.mkdir()
    evaluation_compressed_dir.mkdir()
    (tmp_path / 'output').mkdir()

    image_path = evaluation_dir / 'sample.png'
    image_path.write_bytes(build_unoptimized_png_bytes())

    monkeypatch.setattr(routes.settings, 'data_dir', tmp_path)
    monkeypatch.setattr(routes.settings, 'evaluation_images_dir', evaluation_dir)
    monkeypatch.setattr(routes.settings, 'evaluation_compressed_dir', evaluation_compressed_dir, raising=False)

    def fail_build(_path):
        raise ValueError('boom')

    monkeypatch.setattr(evaluation_images_service, '_build_compression_item', fail_build)

    response = client.get('/api/evaluation-images')

    assert response.status_code == 200
    item = response.json()['items'][0]
    assert item['status'] == 'skipped'
    assert item['algorithm'] == 'evaluation-fallback'
    assert urlparse(item['original_url']).path == '/api/evaluation-images/sample.png'
    assert item['compressed_url'] == item['original_url']


def test_evaluation_images_file_endpoint_serves_file_and_blocks_traversal(tmp_path, monkeypatch):
    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_dir.mkdir()
    image_path = evaluation_dir / '00001.png'
    image_path.write_bytes(build_png_bytes())

    outside_path = tmp_path / 'outside.png'
    outside_path.write_bytes(build_png_bytes())

    monkeypatch.setattr(routes.settings, 'evaluation_images_dir', evaluation_dir)

    ok_response = client.get('/api/evaluation-images/00001.png')
    traversal_response = client.get('/api/evaluation-images/%2E%2E/outside.png')
    encoded_slash_traversal_response = client.get('/api/evaluation-images/..%2Foutside.png')

    assert ok_response.status_code == 200
    assert ok_response.content == image_path.read_bytes()
    assert ok_response.headers['content-type'] == 'image/png'
    assert traversal_response.status_code == 404
    assert encoded_slash_traversal_response.status_code == 404


def test_evaluation_compressed_file_endpoint_serves_file_and_blocks_unsafe_paths(tmp_path, monkeypatch):
    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_compressed_dir.mkdir()
    image_path = evaluation_compressed_dir / '00001.webp'
    image_path.write_bytes(build_png_bytes())

    (evaluation_compressed_dir / '.hidden.png').write_bytes(build_png_bytes())
    (evaluation_compressed_dir / 'nested').mkdir()
    (evaluation_compressed_dir / 'notes.txt').write_text('ignore me', encoding='utf-8')
    outside_path = tmp_path / 'outside.webp'
    outside_path.write_bytes(build_png_bytes())

    monkeypatch.setattr(routes.settings, 'evaluation_compressed_dir', evaluation_compressed_dir, raising=False)

    ok_response = client.get('/api/evaluation-compressed/00001.webp')
    hidden_response = client.get('/api/evaluation-compressed/.hidden.png')
    traversal_response = client.get('/api/evaluation-compressed/%2E%2E/outside.webp')
    encoded_slash_traversal_response = client.get('/api/evaluation-compressed/..%2Foutside.webp')
    unsupported_response = client.get('/api/evaluation-compressed/notes.txt')
    directory_response = client.get('/api/evaluation-compressed/nested')

    assert ok_response.status_code == 200
    assert ok_response.content == image_path.read_bytes()
    assert ok_response.headers['content-type'] == 'image/webp'
    assert hidden_response.status_code == 404
    assert traversal_response.status_code == 404
    assert encoded_slash_traversal_response.status_code == 404
    assert unsupported_response.status_code == 404
    assert directory_response.status_code == 404


def test_compress_endpoint_accepts_batch_upload():
    png_bytes = build_png_bytes()
    response = client.post(
        '/api/compress',
        files=[('files', ('demo.png', png_bytes, 'image/png'))],
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload['items']) == 1
    assert payload['items'][0]['metrics']['ssim'] >= 0


def test_compress_stream_endpoint_allows_larger_gif_than_generic_limit(monkeypatch):
    big_gif = b'0' * (25 * 1024 * 1024)

    monkeypatch.setattr(routes.settings, 'max_file_size_mb', 20)
    monkeypatch.setattr(routes.settings, 'max_gif_file_size_mb', 80)

    def fake_compress_bytes(file_name: str, payload: bytes, progress_callback=None):
        return CompressionItem(
            file_name=file_name,
            original_size=len(payload),
            compressed_size=len(payload) - 1024,
            original_url=f'/api/files/{file_name}?kind=upload',
            compressed_url=f'/api/files/{file_name}?kind=output',
            mime_type='image/gif',
            status='completed',
            algorithm='gifsicle-o3-lossy20',
            metrics=CompressionMetrics(compression_ratio=1.0, ssim=0.99, psnr=40.0),
        )

    monkeypatch.setattr(routes.compression_service, 'compress_bytes', fake_compress_bytes)

    with client.stream(
        'POST',
        '/api/compress/stream',
        files=[('files', ('oversized.gif', big_gif, 'image/gif'))],
        data={'parallelism': '1'},
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    assert events[-1]['type'] == 'result'
    assert events[-1]['item']['file_name'] == 'oversized.gif'



def test_compress_stream_endpoint_keeps_generic_limit_for_non_gif(monkeypatch):
    big_png = b'0' * (25 * 1024 * 1024)

    monkeypatch.setattr(routes.settings, 'max_file_size_mb', 20)
    monkeypatch.setattr(routes.settings, 'max_gif_file_size_mb', 80)

    response = client.post(
        '/api/compress/stream',
        files=[('files', ('oversized.png', big_png, 'image/png'))],
        data={'parallelism': '1'},
    )

    assert response.status_code == 400
    assert response.json()['detail'] == 'File too large: oversized.png'


def test_compress_stream_endpoint_emits_logs_and_final_result(monkeypatch):
    png_bytes = build_png_bytes()

    def fake_compress_bytes(file_name: str, payload: bytes, progress_callback=None):
        if progress_callback is not None:
            progress_callback({'stage': 'start', 'message': f'开始压缩 {file_name}'})
            progress_callback({'stage': 'candidate', 'message': '生成候选 pngquant-85-98：12.3 KB'})
        return CompressionItem(
            file_name=file_name,
            original_size=len(payload),
            compressed_size=max(1, len(payload) // 2),
            original_url=f'/api/files/{file_name}?kind=upload',
            compressed_url=f'/api/files/{file_name}?kind=output',
            mime_type='image/png',
            status='completed',
            algorithm='pngquant-85-98',
            metrics=CompressionMetrics(compression_ratio=50.0, ssim=0.999, psnr=48.0),
        )

    monkeypatch.setattr(routes.compression_service, 'compress_bytes', fake_compress_bytes)

    with client.stream(
        'POST',
        '/api/compress/stream',
        files=[('files', ('demo.png', png_bytes, 'image/png'))],
        data={'parallelism': '1'},
    ) as response:
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('application/x-ndjson')
        events = [json.loads(line) for line in response.iter_lines() if line]

    assert [event['type'] for event in events[:2]] == ['log', 'log']
    assert events[0]['message'].startswith('开始压缩 demo.png')
    assert events[0]['spend_time_ms'] == 0
    assert events[1]['message'].startswith('生成候选 pngquant-85-98')
    assert isinstance(events[1]['spend_time_ms'], int)
    assert events[1]['spend_time_ms'] >= 0
    assert events[-1]['type'] == 'result'
    assert events[-1]['item']['algorithm'] == 'pngquant-85-98'



def test_compress_endpoint_allows_larger_gif_than_generic_limit(monkeypatch):
    big_gif = b'0' * (25 * 1024 * 1024)

    monkeypatch.setattr(routes.settings, 'max_file_size_mb', 20)
    monkeypatch.setattr(routes.settings, 'max_gif_file_size_mb', 80)

    def fake_compress_bytes(file_name: str, payload: bytes, progress_callback=None):
        return CompressionItem(
            file_name=file_name,
            original_size=len(payload),
            compressed_size=len(payload) - 1024,
            original_url=f'/api/files/{file_name}?kind=upload',
            compressed_url=f'/api/files/{file_name}?kind=output',
            mime_type='image/gif',
            status='completed',
            algorithm='gifsicle-o3-lossy20',
            metrics=CompressionMetrics(compression_ratio=1.0, ssim=0.99, psnr=40.0),
        )

    monkeypatch.setattr(routes.compression_service, 'compress_bytes', fake_compress_bytes)

    response = client.post(
        '/api/compress',
        files=[('files', ('oversized.gif', big_gif, 'image/gif'))],
        data={'parallelism': '1'},
    )

    assert response.status_code == 200
    assert response.json()['items'][0]['file_name'] == 'oversized.gif'


def test_download_zip_endpoint_returns_compressed_images_archive():
    png_bytes = build_png_bytes()
    compress_response = client.post(
        '/api/compress',
        files=[
            ('files', ('alpha.png', png_bytes, 'image/png')),
            ('files', ('beta.png', png_bytes, 'image/png')),
        ],
    )
    assert compress_response.status_code == 200
    items = compress_response.json()['items']

    files = []
    expected_names = []
    for item in items:
        parsed = urlparse(item['compressed_url'])
        stored_name = parsed.path.rsplit('/', 1)[-1]
        kind = parse_qs(parsed.query)['kind'][0]
        assert kind == 'output'
        download_name = f"{item['file_name'].rsplit('.', 1)[0]}-compressed.{stored_name.rsplit('.', 1)[-1]}"
        files.append({
            'stored_name': stored_name,
            'download_name': download_name,
        })
        expected_names.append(download_name)

    response = client.post('/api/download/outputs.zip', json={'files': files})

    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    assert 'bbduck-compressed-images.zip' in response.headers['content-disposition']

    archive = zipfile.ZipFile(BytesIO(response.content))
    assert sorted(archive.namelist()) == sorted(expected_names)
    assert all(archive.read(name) for name in archive.namelist())


def test_download_zip_endpoint_accepts_skipped_items_from_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(routes.settings, 'data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    webp_image = Image.new('RGB', (32, 32), color=(255, 0, 0))
    webp_buffer = BytesIO()
    webp_image.save(webp_buffer, format='WEBP', lossless=True, quality=100)

    jpeg_image = Image.new('RGB', (64, 64), color=(20, 160, 120))
    jpeg_buffer = BytesIO()
    jpeg_image.save(jpeg_buffer, format='JPEG', quality=95)

    async def fake_compress_prepared_uploads(prepared_uploads, parallelism):
        assert parallelism == routes.settings.default_parallel_uploads
        assert [file_name for file_name, _ in prepared_uploads] == ['keep.webp', 'photo.jpg']

        upload_name = 'stub-keep.webp'
        output_name = 'stub-photo.compressed.jpg'
        (routes.settings.upload_dir / upload_name).write_bytes(webp_buffer.getvalue())
        (routes.settings.output_dir / output_name).write_bytes(jpeg_buffer.getvalue())

        return [
            CompressionItem(
                file_name='keep.webp',
                original_size=len(webp_buffer.getvalue()),
                compressed_size=len(webp_buffer.getvalue()),
                original_url=f'/api/files/{upload_name}?kind=upload',
                compressed_url=f'/api/files/{upload_name}?kind=upload',
                mime_type='image/webp',
                status='skipped',
                algorithm='skip-existing',
                metrics=CompressionMetrics(compression_ratio=0.0, ssim=1.0, psnr=99.0),
            ),
            CompressionItem(
                file_name='photo.jpg',
                original_size=len(jpeg_buffer.getvalue()),
                compressed_size=len(jpeg_buffer.getvalue()),
                original_url='/api/files/stub-photo-original.jpg?kind=upload',
                compressed_url=f'/api/files/{output_name}?kind=output',
                mime_type='image/jpeg',
                status='completed',
                algorithm='stub-jpeg',
                metrics=CompressionMetrics(compression_ratio=0.0, ssim=1.0, psnr=99.0),
            ),
        ]

    monkeypatch.setattr(routes, '_compress_prepared_uploads', fake_compress_prepared_uploads)

    compress_response = client.post(
        '/api/compress',
        files=[
            ('files', ('keep.webp', webp_buffer.getvalue(), 'image/webp')),
            ('files', ('photo.jpg', jpeg_buffer.getvalue(), 'image/jpeg')),
        ],
    )
    assert compress_response.status_code == 200
    items = compress_response.json()['items']
    assert {item['status'] for item in items} == {'skipped', 'completed'}

    files = []
    expected_names = []
    for item in items:
        file_url = item['original_url'] if item['status'] == 'skipped' else item['compressed_url']
        parsed = urlparse(file_url)
        stored_name = parsed.path.rsplit('/', 1)[-1]
        kind = parse_qs(parsed.query)['kind'][0]
        download_name = item['file_name']
        files.append({
            'stored_name': stored_name,
            'download_name': download_name,
            'kind': kind,
        })
        expected_names.append(download_name)

    response = client.post('/api/download/outputs.zip', json={'files': files})

    assert response.status_code == 200
    archive = zipfile.ZipFile(BytesIO(response.content))
    assert sorted(archive.namelist()) == sorted(expected_names)
    assert all(archive.read(name) for name in archive.namelist())


def test_download_zip_endpoint_accepts_urlencoded_stored_names(tmp_path, monkeypatch):
    monkeypatch.setattr('app.api.routes.settings.data_dir', tmp_path)
    for directory in ('uploads', 'output', 'tmp'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)

    stored_name = '4ad754e30ff34b3ba5f6a144e110967b-Peek 2025-06-07 16-35.compressed.gif'
    output_file = tmp_path / 'output' / stored_name
    output_file.write_bytes(b'gif89a')

    response = client.post(
        '/api/download/outputs.zip',
        json={
            'files': [
                {
                    'stored_name': '4ad754e30ff34b3ba5f6a144e110967b-Peek%202025-06-07%2016-35.compressed.gif',
                    'download_name': 'Peek-compressed.gif',
                    'kind': 'output',
                }
            ]
        },
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(BytesIO(response.content))
    assert archive.namelist() == ['Peek-compressed.gif']
    assert archive.read('Peek-compressed.gif') == b'gif89a'


def test_compress_endpoint_returns_400_for_broken_image_stream():
    broken_png_bytes = b'not-a-real-png-stream'
    response = client.post(
        '/api/compress',
        files=[('files', ('broken.png', broken_png_bytes, 'image/png'))],
    )
    assert response.status_code == 400
    assert 'broken.png' in response.json()['detail']


@pytest.mark.anyio
async def test_compress_prepared_uploads_respects_parallelism_limit(monkeypatch):
    active_calls = 0
    max_active_calls = 0

    def fake_compress_bytes(file_name: str, payload: bytes) -> CompressionItem:
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        time.sleep(0.05)
        active_calls -= 1
        return CompressionItem(
            file_name=file_name,
            original_size=len(payload),
            compressed_size=len(payload),
            original_url=f'/api/files/{file_name}?kind=upload',
            compressed_url=f'/api/files/{file_name}?kind=output',
            mime_type='image/png',
            status='completed',
            algorithm='passthrough',
            metrics=CompressionMetrics(compression_ratio=0.0, ssim=1.0, psnr=99.0),
        )

    monkeypatch.setattr(routes.compression_service, 'compress_bytes', fake_compress_bytes)

    uploads = [(f'image-{index}.png', build_png_bytes()) for index in range(6)]
    items = await routes._compress_prepared_uploads(uploads, parallelism=3)

    assert len(items) == 6
    assert max_active_calls <= 3
