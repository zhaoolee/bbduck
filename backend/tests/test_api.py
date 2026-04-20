from io import BytesIO
import time
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes
from app.main import app
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


def test_config_endpoint_exposes_supported_formats():
    response = client.get('/api/config')
    assert response.status_code == 200
    payload = response.json()
    assert 'png' in payload['allowed_formats']
    assert payload['max_files'] >= 1
    assert payload['compression_profile'] in {'fidelity', 'balanced', 'smallest'}
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
