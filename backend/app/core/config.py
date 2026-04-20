from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = BACKEND_ROOT / 'data'
DEFAULT_FRONTEND_DIST = BACKEND_ROOT.parent / 'frontend' / 'dist'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_prefix='BBDUCK_', extra='ignore')

    app_name: str = 'BBDuck Server'
    api_prefix: str = '/api'
    frontend_dist: str = str(DEFAULT_FRONTEND_DIST)
    allowed_formats: str = 'jpg,jpeg,png,webp,gif'
    max_files: int = 30
    max_file_size_mb: int = 20
    default_parallel_uploads: int = 6
    max_parallel_uploads: int = 10
    ssim_threshold: float = 0.985
    psnr_threshold: float = 40.0
    gif_lossy_ssim_threshold: float = 0.95
    gif_lossy_psnr_threshold: float = 36.0
    metrics_max_dimension: int = 768
    data_dir: Path = Field(default=DEFAULT_DATA_DIR)

    @property
    def allowed_suffixes(self) -> list[str]:
        return [item.strip().lower() for item in self.allowed_formats.split(',') if item.strip()]

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / 'uploads'

    @property
    def output_dir(self) -> Path:
        return self.data_dir / 'output'

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / 'tmp'


settings = Settings()
for directory in (settings.data_dir, settings.upload_dir, settings.output_dir, settings.tmp_dir):
    directory.mkdir(parents=True, exist_ok=True)
