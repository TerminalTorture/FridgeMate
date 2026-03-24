from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
import time

from app.core.telegram_service import TelegramService
from app.core.time_utils import utc_now


class TelegramPollingRunner:
    def __init__(self, telegram_service: TelegramService, worker_count: int = 4) -> None:
        self.telegram_service = telegram_service
        self.worker_count = max(worker_count, 1)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._futures: set[Future[object]] = set()
        self._last_poll_at: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self.worker_count,
            thread_name_prefix="telegram-worker",
        )
        self._thread = threading.Thread(target=self._run, name="telegram-polling", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None

    def status(self) -> dict[str, object]:
        with self._lock:
            in_flight = len(self._futures)
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "offset": self._offset,
            "mode": self.telegram_service.settings.telegram_mode,
            "worker_count": self.worker_count,
            "in_flight": in_flight,
            "last_poll_at": self._last_poll_at,
        }

    def _run(self) -> None:
        try:
            # Polling and webhook cannot be used together for the same bot.
            self.telegram_service.delete_webhook(drop_pending_updates=False)
        except Exception as exc:
            self.telegram_service.debug_log.record(
                service="telegram",
                direction="internal",
                status="error",
                summary="Failed to delete webhook before polling.",
                metadata={"error": str(exc)},
            )

        timeout_seconds = self.telegram_service.settings.telegram_poll_timeout_seconds
        while not self._stop_event.is_set():
            try:
                self._prune_futures()
                response = self.telegram_service.get_updates(
                    offset=self._offset,
                    timeout_seconds=timeout_seconds,
                )
                self._last_poll_at = utc_now().isoformat()
                updates = response.get("result", []) if isinstance(response, dict) else []
                if not isinstance(updates, list):
                    time.sleep(1)
                    continue

                for update in updates:
                    if self._stop_event.is_set():
                        break
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    self._submit_update(update, update_id)
            except Exception as exc:
                self.telegram_service.debug_log.record(
                    service="telegram",
                    direction="internal",
                    status="error",
                    summary="Polling loop failed.",
                    metadata={"error": str(exc)},
                )
                time.sleep(2)

        self._prune_futures(wait=True)

    def _submit_update(self, update: dict[str, object], update_id: object) -> None:
        if self._executor is None:
            return

        future = self._executor.submit(self._process_update_safely, update, update_id)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._on_future_done)

    def _process_update_safely(self, update: dict[str, object], update_id: object) -> None:
        try:
            self.telegram_service.process_update(update)
        except Exception as exc:
            self.telegram_service.debug_log.record(
                service="telegram",
                direction="internal",
                status="error",
                summary="Failed to process polled update.",
                metadata={"update_id": update_id, "error": str(exc)},
            )

    def _on_future_done(self, future: Future[object]) -> None:
        with self._lock:
            self._futures.discard(future)

    def _prune_futures(self, wait: bool = False) -> None:
        with self._lock:
            futures = list(self._futures)
        for future in futures:
            if wait:
                try:
                    future.result(timeout=1)
                except Exception:
                    pass
            elif not future.done():
                continue
