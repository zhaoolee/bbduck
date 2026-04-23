from io import BytesIO
import json
import time
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes
from app.main import app
import app.main as app_main
from app.schemas import CompressionItem, CompressionMetrics


client = TestClient(app)


def build_png_bytes() -> bytes:
    image = Image.new('RGB', (32, 32), color=(20, 160, 120))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def test_health_endpoint_returns_ok():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_spa_serves_root_level_static_files_from_frontend_dist(tmp_path, monkeypatch):
    static_file = tmp_path / 'bbduck-logo.jpg'
    static_file.write_bytes(b'fake-jpeg-bytes')
    (tmp_path / 'index.html').write_text('<!doctype html><html><body>fallback</body></html>', encoding='utf-8')

    monkeypatch.setattr(app_main, 'frontend_dist', tmp_path)

    response = client.get('/bbduck-logo.jpg')

    assert response.status_code == 200
    assert response.content == b'fake-jpeg-bytes'
    assert response.headers['content-type'] in {'image/jpeg', 'image/jpg'}


def test_config_endpoint_exposes_supported_formats():
    response = client.get('/api/config')
    assert response.status_code == 200
    payload = response.json()
    assert 'png' in payload['allowed_formats']
    assert payload['max_files'] >= 1
    assert payload['compression_profile'] in {'safe', 'visual-lossless', 'aggressive'}
    assert payload['min_compression_saving_percent'] >= 0


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


def test_download_zip_endpoint_accepts_skipped_items_from_upload_dir():
    webp_image = Image.new('RGB', (32, 32), color=(255, 0, 0))
    webp_buffer = BytesIO()
    webp_image.save(webp_buffer, format='WEBP', lossless=True, quality=100)

    jpeg_image = Image.new('RGB', (64, 64), color=(20, 160, 120))
    jpeg_buffer = BytesIO()
    jpeg_image.save(jpeg_buffer, format='JPEG', quality=95)

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
