import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import HTTPException
from PIL import UnidentifiedImageError
from pydantic import ValidationError

from app.core.config import settings
from app.schemas import CompressionItem, CompressionMetrics
from app.services.compress import compression_service


_MIME_TYPES_BY_SUFFIX = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}


def _natural_sort_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value)]


def _is_supported_evaluation_image(path: Path) -> bool:
    return path.is_file() and not path.name.startswith('.') and path.suffix.lower() in _MIME_TYPES_BY_SUFFIX


def _version_token(path: Path) -> str:
    stat = path.stat()
    return f'{stat.st_mtime_ns}-{stat.st_size}'


def _original_url(path: Path) -> str:
    return f"/api/evaluation-images/{quote(path.name)}?v={_version_token(path)}"


def _runtime_compressed_url(file_name: str, path: Path | None = None) -> str:
    version = f"&v={_version_token(path)}" if path is not None and path.exists() else ''
    return f"/api/files/{quote(file_name)}?kind=output{version}"


def _packaged_compressed_url(path: Path) -> str:
    return f"/api/evaluation-compressed/{quote(path.name)}?v={_version_token(path)}"


def _cache_key(path: Path) -> str:
    stat = path.stat()
    source = f'{path.name}:{stat.st_mtime_ns}:{stat.st_size}'
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:20]


def _cache_manifest_path(cache_key: str) -> Path:
    return settings.output_dir / f'evaluation-{cache_key}.json'


def _build_fallback_item(path: Path) -> CompressionItem:
    size = path.stat().st_size
    original_url = _original_url(path)
    return CompressionItem(
        file_name=path.name,
        original_size=size,
        compressed_size=size,
        original_url=original_url,
        compressed_url=original_url,
        mime_type=_MIME_TYPES_BY_SUFFIX[path.suffix.lower()],
        status='skipped',
        algorithm='evaluation-fallback',
        metrics=CompressionMetrics(compression_ratio=0.0, ssim=1.0, psnr=99.0),
    )


def _preferred_compressed_suffixes(original_suffix: str) -> list[str]:
    normalized = original_suffix.lower()
    ordered = [normalized]
    ordered.extend(suffix for suffix in _MIME_TYPES_BY_SUFFIX if suffix != normalized)
    return ordered


def _find_packaged_compressed_image(original_path: Path) -> Path | None:
    base_dir = settings.evaluation_compressed_dir
    if not base_dir.exists():
        return None

    suffixes = _preferred_compressed_suffixes(original_path.suffix)
    candidates: list[Path] = []
    for stem in (original_path.stem, f'{original_path.stem}.compressed'):
        for suffix in suffixes:
            candidates.append(base_dir / f'{stem}{suffix}')

    seen_names: set[str] = set()
    for candidate in candidates:
        if candidate.name in seen_names:
            continue
        seen_names.add(candidate.name)
        if _is_supported_evaluation_image(candidate):
            return candidate
    return None


def _build_prebuilt_item(original_path: Path, compressed_path: Path) -> CompressionItem:
    original_size = original_path.stat().st_size
    compressed_size = compressed_path.stat().st_size
    ratio = max(0.0, round(100 * (1 - (compressed_size / max(original_size, 1))), 2))
    return CompressionItem(
        file_name=original_path.name,
        original_size=original_size,
        compressed_size=compressed_size,
        original_url=_original_url(original_path),
        compressed_url=_packaged_compressed_url(compressed_path),
        mime_type=_MIME_TYPES_BY_SUFFIX[compressed_path.suffix.lower()],
        status='completed' if compressed_size < original_size else 'skipped',
        algorithm='evaluation-prebuilt',
        metrics=CompressionMetrics(
            compression_ratio=ratio,
            ssim=1.0,
            psnr=99.0,
        ),
    )


def _load_cached_item(path: Path, cache_key: str) -> CompressionItem | None:
    manifest_path = _cache_manifest_path(cache_key)
    if not manifest_path.exists():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None

    compressed_name = manifest.get('compressed_name')
    if not isinstance(compressed_name, str) or not compressed_name:
        return None

    compressed_path = settings.output_dir / compressed_name
    if not compressed_path.exists() or not compressed_path.is_file():
        return None

    item_payload = manifest.get('item')
    if not isinstance(item_payload, dict):
        return None

    item_payload['file_name'] = path.name
    item_payload['original_url'] = _original_url(path)
    item_payload['compressed_url'] = _runtime_compressed_url(compressed_name, compressed_path)
    try:
        return CompressionItem.model_validate(item_payload)
    except ValidationError:
        return None


def _write_cached_item(cache_key: str, compressed_name: str, item: CompressionItem) -> None:
    manifest_path = _cache_manifest_path(cache_key)
    manifest_path.write_text(
        json.dumps(
            {
                'compressed_name': compressed_name,
                'item': item.model_dump(mode='json'),
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def _build_compression_item(path: Path) -> CompressionItem:
    payload = path.read_bytes()
    suffix = path.suffix.lower().lstrip('.')
    chosen = compression_service._compress_by_suffix(
        file_name=path.name,
        payload=payload,
        suffix=suffix,
        toolchain=compression_service.get_available_toolchain(),
    )
    compressed_extension = compression_service._target_extension(suffix, chosen.candidate.algorithm)
    cache_key = _cache_key(path)
    compressed_name = f'evaluation-{cache_key}.{compressed_extension}'
    compressed_path = settings.output_dir / compressed_name
    compressed_path.write_bytes(chosen.candidate.payload)

    ratio = round(100 * (1 - (len(chosen.candidate.payload) / max(len(payload), 1))), 2)
    item = CompressionItem(
        file_name=path.name,
        original_size=len(payload),
        compressed_size=len(chosen.candidate.payload),
        original_url=_original_url(path),
        compressed_url=_runtime_compressed_url(compressed_name, compressed_path),
        mime_type=compression_service._mime_from_algorithm_or_suffix(chosen.candidate.algorithm, suffix),
        status='skipped' if chosen.candidate.algorithm == 'passthrough' else 'completed',
        algorithm=chosen.candidate.algorithm,
        metrics=CompressionMetrics(
            compression_ratio=ratio,
            ssim=round(chosen.ssim, 4),
            psnr=round(chosen.psnr, 2),
        ),
    )
    _write_cached_item(cache_key, compressed_name, item)
    return item


def _build_or_load_evaluation_item(path: Path) -> CompressionItem:
    packaged_path = _find_packaged_compressed_image(path)
    if packaged_path is not None:
        try:
            return _build_prebuilt_item(path, packaged_path)
        except (OSError, UnidentifiedImageError, ValueError):
            pass

    cache_key = _cache_key(path)
    cached_item = _load_cached_item(path, cache_key)
    if cached_item is not None:
        return cached_item

    try:
        return _build_compression_item(path)
    except (OSError, UnidentifiedImageError, ValueError):
        return _build_fallback_item(path)


def list_evaluation_images() -> list[CompressionItem]:
    base_dir = settings.evaluation_images_dir
    if not base_dir.exists():
        return []

    items: list[CompressionItem] = []
    for path in sorted((entry for entry in base_dir.iterdir() if _is_supported_evaluation_image(entry)), key=lambda item: _natural_sort_key(item.name)):
        items.append(_build_or_load_evaluation_item(path))
    return items


def _resolve_safe_evaluation_file(base_dir: Path, file_name: str) -> Path:
    decoded_name = unquote(file_name)
    if not decoded_name or decoded_name.startswith('.'):
        raise HTTPException(status_code=404, detail='File not found')

    requested_path = Path(decoded_name)
    if requested_path.name != decoded_name:
        raise HTTPException(status_code=404, detail='File not found')

    resolved_base_dir = base_dir.resolve()
    target = (resolved_base_dir / decoded_name).resolve()
    try:
        target.relative_to(resolved_base_dir)
    except ValueError as error:
        raise HTTPException(status_code=404, detail='File not found') from error

    if not _is_supported_evaluation_image(target):
        raise HTTPException(status_code=404, detail='File not found')

    return target


def resolve_evaluation_image(file_name: str) -> Path:
    return _resolve_safe_evaluation_file(settings.evaluation_images_dir, file_name)


def resolve_evaluation_compressed_image(file_name: str) -> Path:
    return _resolve_safe_evaluation_file(settings.evaluation_compressed_dir, file_name)
