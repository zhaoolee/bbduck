from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from PIL import UnidentifiedImageError

from app.core.config import settings
from app.services.compress import compression_service
from app.services.evaluation_images import _is_supported_evaluation_image


@dataclass(frozen=True)
class BuildSummary:
    built: int
    failed: int
    output_dir: Path


def iter_evaluation_images() -> list[Path]:
    base_dir = settings.evaluation_images_dir
    if not base_dir.exists():
        return []

    return sorted(
        (path for path in base_dir.iterdir() if _is_supported_evaluation_image(path)),
        key=lambda path: path.name,
    )


def clear_previous_outputs(stem: str) -> None:
    for pattern in (f'{stem}.*', f'{stem}.compressed.*'):
        for path in settings.evaluation_compressed_dir.glob(pattern):
            if _is_supported_evaluation_image(path):
                path.unlink()


def _source_stem_for_compressed(path: Path) -> str:
    return path.stem.removesuffix('.compressed')


def clear_stale_outputs(source_stems: set[str]) -> None:
    if not settings.evaluation_compressed_dir.exists():
        return

    for path in settings.evaluation_compressed_dir.iterdir():
        if not _is_supported_evaluation_image(path):
            continue
        if _source_stem_for_compressed(path) not in source_stems:
            path.unlink()


def build_one(path: Path) -> tuple[Path, str]:
    payload = path.read_bytes()
    suffix = path.suffix.lower().lstrip('.')
    chosen = compression_service._compress_by_suffix(
        file_name=path.name,
        payload=payload,
        suffix=suffix,
        toolchain=compression_service.get_available_toolchain(),
    )
    output_suffix = compression_service._target_extension(suffix, chosen.candidate.algorithm)
    output_path = settings.evaluation_compressed_dir / f'{path.stem}.{output_suffix}'
    clear_previous_outputs(path.stem)
    output_path.write_bytes(chosen.candidate.payload)
    return output_path, chosen.candidate.algorithm


def build_all() -> BuildSummary:
    settings.evaluation_compressed_dir.mkdir(parents=True, exist_ok=True)
    images = iter_evaluation_images()
    clear_stale_outputs({path.stem for path in images})
    if not images:
        print(f'No evaluation images found in {settings.evaluation_images_dir}')
        return BuildSummary(built=0, failed=0, output_dir=settings.evaluation_compressed_dir)

    built = 0
    failed = 0
    for path in images:
        try:
            output_path, algorithm = build_one(path)
        except (OSError, UnidentifiedImageError, ValueError) as error:
            failed += 1
            print(f'FAILED {path.name}: {error}')
            continue

        built += 1
        print(f'BUILT {path.name} -> {output_path.name} ({algorithm})')

    summary = BuildSummary(built=built, failed=failed, output_dir=settings.evaluation_compressed_dir)
    print(f'Finished. built={summary.built} failed={summary.failed} output_dir={summary.output_dir}')
    return summary


def main() -> int:
    summary = build_all()
    return 0 if summary.failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
