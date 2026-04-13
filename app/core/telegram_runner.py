from __future__ import annotations

import asyncio
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
        self._lock = threading.Lock()
        self._tasks: set[asyncio.Task[object]] = set()
        self._last_poll_at: str | None = None
        self._offset: int | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="telegram-polling", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def status(self) -> dict[str, object]:
        with self._lock:
            in_flight = len(self._tasks)
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "offset": self._offset,
            "mode": self.telegram_service.settings.telegram_mode,
            "worker_count": self.worker_count,
            "in_flight": in_flight,
            "last_poll_at": self._last_poll_at,
        }

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        try:
            # Polling and webhook cannot be used together for the same bot.
            await self.telegram_service.delete_webhook_async(drop_pending_updates=False)
        except Exception as exc:
            self.telegram_service.debug_log.record(
                service="telegram",
                direction="internal",
                status="error",
                summary="Failed to delete webhook before polling.",
                metadata={"error": str(exc)},
            )

        timeout_seconds = self.telegram_service.settings.telegram_poll_timeout_seconds
        semaphore = asyncio.Semaphore(self.worker_count)
        while not self._stop_event.is_set():
            try:
                self._prune_tasks()
                response = await self.telegram_service.get_updates_async(
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
                    self._submit_update(update, update_id, semaphore)
            except Exception as exc:
                self.telegram_service.debug_log.record(
                    service="telegram",
                    direction="internal",
                    status="error",
                    summary="Polling loop failed.",
                    metadata={"error": str(exc)},
                )
                await asyncio.sleep(2)

        await self._drain_tasks()

    def _submit_update(self, update: dict[str, object], update_id: object, semaphore: asyncio.Semaphore) -> None:
        task = asyncio.create_task(self._process_update_safely(update, update_id, semaphore))
        with self._lock:
            self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    async def _process_update_safely(
        self,
        update: dict[str, object],
        update_id: object,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            try:
                await self.telegram_service.process_update_async(update)
            except Exception as exc:
                self.telegram_service.debug_log.record(
                    service="telegram",
                    direction="internal",
                    status="error",
                    summary="Failed to process polled update.",
                    metadata={"update_id": update_id, "error": str(exc)},
                )

    def _on_task_done(self, task: asyncio.Task[object]) -> None:
        with self._lock:
            self._tasks.discard(task)

    def _prune_tasks(self) -> None:
        with self._lock:
            tasks = list(self._tasks)
        for task in tasks:
            if task.done():
                try:
                    task.result()
                except Exception:
                    pass

    async def _drain_tasks(self) -> None:
        with self._lock:
            tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
