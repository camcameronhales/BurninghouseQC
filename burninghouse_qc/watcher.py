"""The unattended service: watch the input folder, QC, route, repeat.

Detection and processing are deliberately decoupled. watchdog only ever adds a
path to a queue; a single worker thread drains it. That means a burst of six
renders landing at once cannot spawn six concurrent FFmpeg jobs on a machine
that is also being used to edit.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .pipeline import cleanup_workdir, run_qc
from .router import route
from .stability import is_candidate, wait_until_stable
from .status import StatusFile, setup_logging


class _InputHandler(FileSystemEventHandler):
    """Turns filesystem events into queue entries. Does no work itself."""

    def __init__(self, work_queue: "queue.Queue[Path]", cfg: Config, logger):
        self.queue = work_queue
        self.cfg = cfg
        self.logger = logger

    def _offer(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not is_candidate(path, self.cfg.watcher):
            return
        self.logger.info("Noticed %s", path.name)
        self.queue.put(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._offer(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Renaming foo.mov.tmp -> foo.mov is how several NLEs finish a write.
        if not event.is_directory:
            self._offer(event.dest_path)


class QCService:
    def __init__(self, cfg: Config, keep_work: bool = False, verbose: bool = False):
        self.cfg = cfg
        self.keep_work = keep_work
        self.logger = setup_logging(cfg.paths.log_file, verbose=verbose)
        self.status = StatusFile(cfg.paths.status_file)
        self.queue: "queue.Queue[Path]" = queue.Queue()
        self._stop = threading.Event()
        self._seen: set[Path] = set()
        self._lock = threading.Lock()

    # -- queueing ---------------------------------------------------------
    def enqueue_existing(self) -> None:
        """Catch up on anything already sitting in /input at start-up."""
        for path in sorted(self.cfg.paths.input.iterdir()):
            if path.is_file() and is_candidate(path, self.cfg.watcher):
                self.logger.info("Queueing pre-existing file %s", path.name)
                self.queue.put(path)

    # -- processing -------------------------------------------------------
    def process(self, path: Path) -> None:
        with self._lock:
            resolved = path.resolve()
            if resolved in self._seen:
                return
            self._seen.add(resolved)
        try:
            self.status.update(state="waiting_for_write", current_file=path.name)
            if not wait_until_stable(path, self.cfg.watcher):
                self.logger.warning("%s never settled (or disappeared) — skipping", path.name)
                self.status.update(state="idle", current_file=None)
                return

            self.logger.info("QC started: %s", path.name)
            self.status.update(state="processing", current_file=path.name)
            result = run_qc(path, self.cfg)
            outcome = route(result, self.cfg)
            counts = result.counts()
            self.logger.info(
                "QC finished: %s -> %s (%d fail, %d review) in %.1fs | report: %s",
                path.name,
                outcome.verdict.value.upper(),
                counts["fail"],
                counts["review"],
                result.elapsed,
                outcome.report,
            )
            self.status.record_result(
                filename=path.name,
                verdict=outcome.verdict.value,
                destination=str(outcome.destination),
                report=str(outcome.report),
            )
            cleanup_workdir(result.workdir, keep=self.keep_work)
        except Exception:  # noqa: BLE001 - the service must outlive one bad file
            self.logger.exception("Unhandled error while processing %s", path.name)
            self.status.update(state="idle", current_file=None, last_error=path.name)
        finally:
            with self._lock:
                self._seen.discard(path.resolve())

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                path = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.process(path)
            finally:
                self.queue.task_done()
                self.status.update(queued=self.queue.qsize())

    # -- lifecycle --------------------------------------------------------
    def run(self) -> None:
        self.cfg.paths.ensure()
        self.logger.info("Burninghouse QC watching %s", self.cfg.paths.input)
        self.status.update(state="idle", current_file=None, queued=0)

        worker = threading.Thread(target=self._worker, name="qc-worker", daemon=True)
        worker.start()

        observer = Observer()
        observer.schedule(
            _InputHandler(self.queue, self.cfg, self.logger),
            str(self.cfg.paths.input),
            recursive=False,
        )
        observer.start()
        self.enqueue_existing()

        try:
            while not self._stop.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.logger.info("Stopping on keyboard interrupt")
        finally:
            self._stop.set()
            observer.stop()
            observer.join(timeout=5)
            worker.join(timeout=5)
            self.status.update(state="stopped", current_file=None)

    def stop(self) -> None:
        self._stop.set()
