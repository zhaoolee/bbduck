from __future__ import annotations

import os
from pathlib import Path

from PIL import Image


def _build_image(path: Path, image_format: str = 'PNG', color: tuple[int, int, int] = (20, 160, 120)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (24, 24), color=color)
    image.save(path, format=image_format)


def test_build_all_aggregates_success_and_failures(monkeypatch, tmp_path):
    from scripts import build_evaluation_assets

    first = tmp_path / '0001.png'
    second = tmp_path / '0002.png'
    built_paths: list[Path] = []

    monkeypatch.setattr(build_evaluation_assets, 'iter_evaluation_images', lambda: [first, second])

    def fake_build_one(path: Path):
        if path == first:
            built_paths.append(path)
            return tmp_path / '0001.webp', 'cwebp'
        raise ValueError('boom')

    monkeypatch.setattr(build_evaluation_assets, 'build_one', fake_build_one)

    summary = build_evaluation_assets.build_all()

    assert summary.built == 1
    assert summary.failed == 1
    assert summary.output_dir == build_evaluation_assets.settings.evaluation_compressed_dir
    assert built_paths == [first]


def test_build_all_removes_stale_compressed_outputs(monkeypatch, tmp_path):
    from scripts import build_evaluation_assets

    evaluation_compressed_dir = tmp_path / 'evaluation-compressed'
    evaluation_compressed_dir.mkdir()
    stale = evaluation_compressed_dir / 'deleted.png'
    stale.write_bytes(b'stale')
    stale_compressed = evaluation_compressed_dir / 'deleted.compressed.webp'
    stale_compressed.write_bytes(b'stale')
    keep = evaluation_compressed_dir / 'current.png'
    keep.write_bytes(b'keep')

    source = tmp_path / 'current.png'
    source.write_bytes(b'current')

    monkeypatch.setattr(build_evaluation_assets.settings, 'evaluation_compressed_dir', evaluation_compressed_dir)
    monkeypatch.setattr(build_evaluation_assets, 'iter_evaluation_images', lambda: [source])
    monkeypatch.setattr(build_evaluation_assets, 'build_one', lambda path: (keep, 'prebuilt-test'))

    summary = build_evaluation_assets.build_all()

    assert summary.built == 1
    assert not stale.exists()
    assert not stale_compressed.exists()
    assert keep.exists()


def test_snapshot_directory_tracks_supported_files_and_changes(tmp_path):
    from scripts import watch_evaluation_assets

    evaluation_dir = tmp_path / 'evaluation-images'
    evaluation_dir.mkdir()

    image_path = evaluation_dir / 'sample.png'
    _build_image(image_path)
    _build_image(evaluation_dir / '.hidden.png')
    (evaluation_dir / 'notes.txt').write_text('ignore me', encoding='utf-8')
    nested_dir = evaluation_dir / 'nested'
    nested_dir.mkdir()
    _build_image(nested_dir / 'nested.png')

    first_snapshot = watch_evaluation_assets.snapshot_directory(evaluation_dir)

    assert list(first_snapshot) == ['sample.png']

    updated_payload = b'changed-payload'
    image_path.write_bytes(updated_payload)
    os.utime(image_path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    second_snapshot = watch_evaluation_assets.snapshot_directory(evaluation_dir)

    assert second_snapshot['sample.png'][1] == len(updated_payload)
    assert second_snapshot != first_snapshot

    image_path.unlink()

    assert watch_evaluation_assets.snapshot_directory(evaluation_dir) == {}


def test_watcher_builds_on_start_and_after_debounced_change(tmp_path):
    from scripts import watch_evaluation_assets

    snapshots = [
        {},
        {'sample.png': (1, 10)},
        {'sample.png': (1, 10)},
        {'sample.png': (1, 10)},
    ]
    clock = {'now': 0.0, 'sleeps': 0}
    build_calls: list[float] = []

    def fake_scan(_directory: Path):
        if snapshots:
            return snapshots.pop(0)
        return {'sample.png': (1, 10)}

    def fake_sleep(seconds: float):
        clock['now'] += seconds
        clock['sleeps'] += 1
        if clock['sleeps'] >= 4:
            raise KeyboardInterrupt

    def fake_build():
        build_calls.append(clock['now'])

    watcher = watch_evaluation_assets.EvaluationAssetsWatcher(
        images_dir=tmp_path,
        build_func=fake_build,
        scan_func=fake_scan,
        sleep_func=fake_sleep,
        monotonic_func=lambda: clock['now'],
        poll_interval=0.25,
        debounce_seconds=0.5,
    )

    exit_code = watcher.run()

    assert exit_code == 0
    assert build_calls == [0.0, 0.75]


def test_watcher_exits_cleanly_on_keyboard_interrupt(tmp_path):
    from scripts import watch_evaluation_assets

    build_calls = 0

    def fake_build():
        nonlocal build_calls
        build_calls += 1

    def interrupting_sleep(_seconds: float):
        raise KeyboardInterrupt

    watcher = watch_evaluation_assets.EvaluationAssetsWatcher(
        images_dir=tmp_path,
        build_func=fake_build,
        scan_func=lambda _directory: {},
        sleep_func=interrupting_sleep,
        monotonic_func=lambda: 0.0,
    )

    assert watcher.run() == 0
    assert build_calls == 1
