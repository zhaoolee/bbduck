import asyncio
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import UnidentifiedImageError

from app.core.config import settings
from app.schemas import (
    AppConfigResponse,
    BatchDownloadRequest,
    CompressionBatchResponse,
    CompressionItem,
    HealthResponse,
)
from app.services.compress import compression_service

router = APIRouter()


class CompressionFailure(Exception):
    def __init__(self, file_name: str):
        self.file_name = file_name
        super().__init__(file_name)


def _sanitize_archive_name(name: str) -> str:
    cleaned = Path(name).name.strip().replace('\\', '_').replace('/', '_')
    return cleaned or 'download.bin'


def _dedupe_archive_name(name: str, used_names: set[str]) -> str:
    candidate = _sanitize_archive_name(name)
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    path = Path(candidate)
    stem = path.stem or 'download'
    suffix = path.suffix
    index = 2
    while True:
        next_candidate = f'{stem}-{index}{suffix}'
        if next_candidate not in used_names:
            used_names.add(next_candidate)
            return next_candidate
        index += 1


async def _compress_prepared_uploads(prepared_uploads: list[tuple[str, bytes]], parallelism: int) -> list[CompressionItem]:
    semaphore = asyncio.Semaphore(parallelism)

    async def run_one(index: int, file_name: str, payload: bytes) -> tuple[int, CompressionItem]:
        async with semaphore:
            try:
                item = await asyncio.to_thread(compression_service.compress_bytes, file_name, payload)
            except (OSError, UnidentifiedImageError, ValueError) as error:
                raise CompressionFailure(file_name) from error
            return index, item

    tasks = [run_one(index, file_name, payload) for index, (file_name, payload) in enumerate(prepared_uploads)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda item: item[0])
    return [item for _, item in results]


@router.get('/health', response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status='ok', app=settings.app_name)


@router.get('/config', response_model=AppConfigResponse)
def config() -> AppConfigResponse:
    return AppConfigResponse(
        app_name=settings.app_name,
        allowed_formats=settings.allowed_suffixes,
        max_files=settings.max_files,
        max_file_size_mb=settings.max_file_size_mb,
        default_parallel_uploads=settings.default_parallel_uploads,
        max_parallel_uploads=settings.max_parallel_uploads,
        compression_profile=settings.compression_profile,
        min_compression_saving_percent=settings.min_compression_saving_percent,
        ssim_threshold=settings.ssim_threshold,
        psnr_threshold=settings.psnr_threshold,
    )


@router.post('/compress', response_model=CompressionBatchResponse)
async def compress(
    files: list[UploadFile] = File(...),
    parallelism: int = Form(default=settings.default_parallel_uploads),
) -> CompressionBatchResponse:
    if len(files) > settings.max_files:
        raise HTTPException(status_code=400, detail=f'Too many files. Max is {settings.max_files}.')
    if parallelism < 1 or parallelism > settings.max_parallel_uploads:
        raise HTTPException(status_code=400, detail=f'Parallelism must be between 1 and {settings.max_parallel_uploads}.')

    prepared_uploads: list[tuple[str, bytes]] = []
    for upload in files:
        suffix = Path(upload.filename or '').suffix.lower().lstrip('.')
        if suffix not in settings.allowed_suffixes:
            raise HTTPException(status_code=400, detail=f'Unsupported format: {suffix}')

        payload = await upload.read()
        max_bytes = settings.max_file_size_mb * 1024 * 1024
        if len(payload) > max_bytes:
            raise HTTPException(status_code=400, detail=f'File too large: {upload.filename}')
        prepared_uploads.append((upload.filename or 'upload.bin', payload))

    try:
        items = await _compress_prepared_uploads(prepared_uploads, parallelism=parallelism)
    except CompressionFailure as error:
        raise HTTPException(status_code=400, detail=f'Invalid or unsupported image data: {error.file_name}') from error

    return CompressionBatchResponse(items=items)


@router.post('/download/outputs.zip')
def download_outputs_zip(payload: BatchDownloadRequest) -> Response:
    if not payload.files:
        raise HTTPException(status_code=400, detail='No files selected for download')

    archive_buffer = BytesIO()
    used_names: set[str] = set()
    with ZipFile(archive_buffer, mode='w', compression=ZIP_DEFLATED) as archive:
        for file in payload.files:
            base_dir = settings.output_dir if file.kind == 'output' else settings.upload_dir
            target = base_dir / Path(file.stored_name).name
            if not target.exists() or not target.is_file():
                raise HTTPException(status_code=404, detail=f'Compressed file not found: {file.stored_name}')
            archive_name = _dedupe_archive_name(file.download_name, used_names)
            archive.writestr(archive_name, target.read_bytes())

    return Response(
        content=archive_buffer.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="bbduck-compressed-images.zip"'},
    )


@router.get('/files/{file_name}')
def read_output_file(file_name: str, kind: str = Query(default='output')) -> FileResponse:
    base_dir = settings.output_dir if kind == 'output' else settings.upload_dir
    target = base_dir / file_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail='File not found')
    return FileResponse(target)
