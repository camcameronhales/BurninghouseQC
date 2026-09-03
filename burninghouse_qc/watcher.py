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
from contextlib import nullcontext
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from .config import Config
from .ledger import Ledger
from .mounts import device_for, should_poll
from .pipeline import cleanup_workdir, run_qc
from .power import keep_awake
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
        # In report_only mode nothing about the input folder changes when a
        # file is done, so the ledger is what stops it being re-checked forever.
        self.ledger = Ledger(cfg.paths.ledger_file)
        self.queue: "queue.Queue[Path]" = queue.Queue()
        self._stop = threading.Event()
        self._seen: set[Path] = set()
        self._lock = threading.Lock()

    # -- queueing ---------------------------------------------------------
    def enqueue_existing(self) -> None:
        """Catch up on anything already sitting in /input at start-up.

        Also says plainly what it found. An idle watcher looks identical
        whether the folder is empty, full of files in a format it ignores, or
        full of files it has already checked — and guessing which is not the
        operator's job.
        """
        files = [path for path in sorted(self.cfg.paths.input.iterdir()) if path.is_file()]
        visible = [
            path
            for path in files
            if not any(path.name.startswith(prefix) for prefix in self.cfg.watcher.ignore_prefixes)
        ]
        candidates = [path for path in visible if is_candidate(path, self.cfg.watcher)]
        wrong_format = [path for path in visible if path not in candidates]

        queued = 0
        already_done = 0
        for path in candidates:
            if self.ledger.seen(path):
                already_done += 1
                continue
            self.logger.info("Queueing pre-existing file %s", path.name)
            self.queue.put(path)
            queued += 1

        if not visible:
            self.logger.info(
                "Input folder is empty — drop %s files in and they will be picked up.",
                " or ".join(self.cfg.watcher.video_extensions),
            )
        elif wrong_format and not candidates:
            extensions = sorted({path.suffix.lower() or "(no extension)" for path in wrong_format})
            self.logger.warning(
                "%d file(s) in the input folder, but none are %s — found %s. "
                "Add the extension to watcher.video_extensions to include them.",
                len(wrong_format),
                " or ".join(self.cfg.watcher.video_extensions),
                ", ".join(extensions),
            )
        else:
            if queued:
                self.logger.info("Queued %d file(s) already in the input folder.", queued)
            if already_done:
                self.logger.info(
                    "Skipped %d file(s) already checked — 'bhqc forget FILE' to re-check.",
                    already_done,
                )
            if wrong_format:
                extensions = sorted({path.suffix.lower() or "(none)" for path in wrong_format})
                self.logger.info(
                    "Ignoring %d file(s) that are not %s (%s).",
                    len(wrong_format),
                    " or ".join(self.cfg.watcher.video_extensions),
                    ", ".join(extensions),
                )
            if not queued and not already_done:
                self.logger.info("Nothing to do yet — waiting for new files.")

    # -- processing -------------------------------------------------------
    def process(self, path: Path) -> None:
        with self._lock:
            resolved = path.resolve()
            if resolved in self._seen:
                return
            self._seen.add(resolved)
        if self.ledger.seen(path):
            self.logger.debug("Already checked, skipping %s", path.name)
            with self._lock:
                self._seen.discard(path.resolve())
            return
        try:
            self.status.update(state="waiting_for_write", current_file=path.name)
            if not wait_until_stable(path, self.cfg.watcher):
                self.logger.warning("%s never settled (or disappeared) — skipping", path.name)
                self.status.update(state="idle", current_file=None)
                return

            self.logger.info("QC started: %s", path.name)
            self.status.update(state="processing", current_file=path.name)
            # Hold the machine awake only while the job actually runs.
            awake = keep_awake(self.logger) if self.cfg.watcher.prevent_sleep else nullcontext()
            with awake:
                result = run_qc(path, self.cfg)
                outcome = route(result, self.cfg, source_snapshot=result.snapshot)
            counts = result.counts()
            self.logger.info(
                "QC finished: %s -> %s (%d fail, %d review) in %.1fs | source %s | report: %s",
                path.name,
                outcome.verdict.value.upper(),
                counts["fail"],
                counts["review"],
                result.elapsed,
                outcome.action.replace("_", " "),
                outcome.report,
            )
            if outcome.warning:
                self.logger.warning("%s: %s", path.name, outcome.warning)
            # Recorded against the file wherever it ended up, so a file left in
            # place is not picked up again on the next restart.
            self.ledger.record(
                outcome.destination, outcome.verdict.value, str(outcome.report)
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
        self.cfg.ensure_paths()
        self.logger.info("Burninghouse QC watching %s", self.cfg.paths.input)
        self.status.update(state="idle", current_file=None, queued=0)

        worker = threading.Thread(target=self._worker, name="qc-worker", daemon=True)
        worker.start()

        observer = self._build_observer()
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

    def _build_observer(self):
        """FSEvents where it works, polling where it does not.

        A network-mounted input folder gets no FSEvents on macOS, so a render
        dropped on a NAS share would never be noticed. Polling is slower to
        react but always fires.
        """
        if should_poll(self.cfg.paths.input, self.cfg.watcher.use_polling):
            self.logger.info(
                "Input folder is on %s — using the polling watcher (every %.0fs)",
                device_for(self.cfg.paths.input) or "a network mount",
                self.cfg.watcher.polling_interval,
            )
            return PollingObserver(timeout=self.cfg.watcher.polling_interval)
        return Observer()

    def stop(self) -> None:
        self._stop.set()
