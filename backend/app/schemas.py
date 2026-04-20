from typing import Literal
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal['ok']
    app: str


class AppConfigResponse(BaseModel):
    app_name: str
    allowed_formats: list[str]
    max_files: int
    max_file_size_mb: int
    default_parallel_uploads: int
    max_parallel_uploads: int
    ssim_threshold: float
    psnr_threshold: float


class CompressionMetrics(BaseModel):
    compression_ratio: float
    ssim: float
    psnr: float


class CompressionItem(BaseModel):
    file_name: str
    original_size: int
    compressed_size: int
    original_url: str
    compressed_url: str
    mime_type: str
    status: Literal['completed', 'skipped']
    algorithm: str
    metrics: CompressionMetrics


class CompressionBatchResponse(BaseModel):
    items: list[CompressionItem]


class BatchDownloadFile(BaseModel):
    stored_name: str
    download_name: str


class BatchDownloadRequest(BaseModel):
    files: list[BatchDownloadFile]
