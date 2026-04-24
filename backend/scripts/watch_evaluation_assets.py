from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings
from app.services.evaluation_images import _is_supported_evaluation_image
from scripts.build_evaluation_assets import build_all


Snapshot = dict[str, tuple[int, int]]


def snapshot_directory(directory: Path) -> Snapshot:
    if not directory.exists():
        return {}

    snapshot: Snapshot = {}
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not _is_supported_evaluation_image(path):
            continue

        stat = path.stat()
        snapshot[path.name] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


class EvaluationAssetsWatcher:
    def __init__(
        self,
        *,
        images_dir: Path,
        build_func: Callable[[], object] = build_all,
        scan_func: Callable[[Path], Snapshot] = snapshot_directory,
        sleep_func: Callable[[float], None] = time.sleep,
        monotonic_func: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.5,
        debounce_seconds: float = 0.75,
    ) -> None:
        self.images_dir = images_dir
        self.build_func = build_func
        self.scan_func = scan_func
        self.sleep_func = sleep_func
        self.monotonic_func = monotonic_func
        self.poll_interval = poll_interval
        self.debounce_seconds = debounce_seconds
        self._last_snapshot: Snapshot = {}
        self._pending_since: float | None = None

    def run(self) -> int:
        self._last_snapshot = self.scan_func(self.images_dir)
        self.build_func()
        print(
            'Watching evaluation images: '
            f'dir={self.images_dir} poll_interval={self.poll_interval}s debounce={self.debounce_seconds}s'
        )

        try:
            while True:
                self.sleep_func(self.poll_interval)
                self._tick()
        except KeyboardInterrupt:
            print('Evaluation asset watcher stopped.')
            return 0

    def _tick(self) -> None:
        current_snapshot = self.scan_func(self.images_dir)
        now = self.monotonic_func()
        if current_snapshot != self._last_snapshot:
            self._last_snapshot = current_snapshot
            self._pending_since = now
            return

        if self._pending_since is None:
            return

        if (now - self._pending_since) < self.debounce_seconds:
            return

        self.build_func()
        self._pending_since = None


def main() -> int:
    watcher = EvaluationAssetsWatcher(images_dir=settings.evaluation_images_dir)
    return watcher.run()


if __name__ == '__main__':
    raise SystemExit(main())
