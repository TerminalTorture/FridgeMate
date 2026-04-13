from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core.time_utils import get_timezone, singapore_now, utc_now
from app.core.tracing import record_json_consult, record_memory_file


class MemoryManager:
    def __init__(self, root_path: str | Path = ".") -> None:
        self.root_path = Path(root_path)
        self.memory_dir = self.root_path / "memory"
        self.daily_dir = self.memory_dir / "daily"
        self.recall_index_path = self.memory_dir / "recall_index.json"

    def ensure_bootstrap_files(self) -> None:
        defaults = {
            "identity.md": (
                "Name: FridgeMate\n"
                "Role: Inventory-aware household assistant\n"
                "Surface: Telegram + local dashboard\n"
                "Style: calm, useful, lightly playful\n"
                "Core job: help user know what exists, what is low, what is expiring, and what to buy\n"
            ),
            "soul.md": (
                "You are practical, warm, and slightly cheeky.\n"
                "You do not pretend to know things you cannot verify.\n"
                "You prioritise clarity over cleverness.\n"
                "You are not a passive chatbot, you are an active household assistant.\n"
                "You notice stock issues, expiry risks, missing staples, and odd usage patterns.\n"
                "You ask for confirmation before any irreversible action.\n"
            ),
            "user.md": (
                "Name: unset\n"
                "Timezone: Asia/Singapore\n"
                "Diet notes: to be learned\n"
                "Shopping pattern: to be learned\n"
                "Preferred tone: to be learned\n"
                "Typical goals: to be learned\n"
            ),
            "bootstrap.md": (
                "On startup:\n"
                "1. Read soul.md\n"
                "2. Read identity.md\n"
                "3. Read user.md\n"
                "4. Read long-term memory\n"
                "5. Read today and yesterday daily memory\n"
                "6. Read latest runtime state\n"
                "7. Read pending actions\n"
                "8. Before answering, decide if verification is needed\n"
            ),
            "heartbeat.md": (
                "Every 30 minutes:\n"
                "- Check if camera is online\n"
                "- Check if last fridge scan is stale\n"
                "- Check for expiring items within 24h\n"
                "- Check for confidence mismatches in item counts\n"
                "- Check whether shopping list has unresolved urgent items\n"
                "- If something is wrong, summarise it in one alert\n"
                "- Do not spam repeated alerts unless status changed\n"
            ),
        }

        for relative_path, content in defaults.items():
            self._write_if_missing(self.root_path / relative_path, content)

        self._write_if_missing(
            self.memory_dir / "long_term.md",
            "- Staple items are to be learned.\n"
            "- Preferred brands are to be learned.\n"
            "- Recurring shopping habits are to be learned.\n"
            "- Usual meal ingredients are to be learned.\n"
            "- Restock thresholds are tracked in inventory memory when available.\n"
            "- User dislikes are to be learned.\n",
        )
        self._write_if_missing(self.daily_path(self.today()), f"# {self.today().isoformat()}\n\n- Daily fridge memory starts here.\n")
        self._write_if_missing(self.daily_dir / "2026-04-09.md", "# 2026-04-09\n\n- Daily fridge memory starts here.\n")
        self._write_if_missing(self.recall_index_path, '{\n  "entries": []\n}\n')

    def prompt_sections(self, *, long_term_limit: int = 4000, recent_limit: int = 6000) -> dict[str, str]:
        self.ensure_bootstrap_files()
        today = self.today()
        yesterday = today - timedelta(days=1)
        yesterday_text = self._read_text(self.daily_path(yesterday), section="recent_memory_yesterday")
        today_text = self._read_text(self.daily_path(today), section="recent_memory_today")
        daily_text = "\n\n".join(chunk for chunk in (yesterday_text, today_text) if chunk)
        return {
            "identity": self._read_text(self.root_path / "identity.md", section="identity"),
            "soul": self._read_text(self.root_path / "soul.md", section="soul"),
            "user": self._read_text(self.root_path / "user.md", section="user"),
            "bootstrap": self._read_text(self.root_path / "bootstrap.md", section="bootstrap"),
            "heartbeat": self._read_text(self.root_path / "heartbeat.md", section="heartbeat"),
            "long_term_memory": self._cap(
                self._read_text(self.memory_dir / "long_term.md", section="long_term_memory"),
                long_term_limit,
            ),
            "recent_memory": self._cap(daily_text, recent_limit) or "No recent daily memory.",
        }

    def append_daily_event(self, summary: str, *, category: str = "event") -> None:
        self.ensure_bootstrap_files()
        clean_summary = summary.strip()
        if not clean_summary:
            return
        path = self.daily_path(self.today())
        timestamp = singapore_now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {timestamp} [{category}] {clean_summary}\n")
        self._append_recall_index(clean_summary, category=category, timestamp=utc_now().isoformat())

    def metadata(self) -> dict[str, object]:
        self.ensure_bootstrap_files()
        today = self.today()
        daily_files = sorted(self.daily_dir.glob("*.md"))
        return {
            "bootstrap_files": {
                name: (self.root_path / name).exists()
                for name in ("identity.md", "soul.md", "user.md", "bootstrap.md", "heartbeat.md")
            },
            "long_term_memory_exists": (self.memory_dir / "long_term.md").exists(),
            "today_daily_memory": str(self.daily_path(today)),
            "yesterday_daily_memory": str(self.daily_path(today - timedelta(days=1))),
            "daily_memory_count": len(daily_files),
            "latest_daily_memory_files": [path.name for path in daily_files[-5:]],
            "recall_index_exists": self.recall_index_path.exists(),
        }

    def daily_path(self, value: date) -> Path:
        return self.daily_dir / f"{value.isoformat()}.md"

    @staticmethod
    def today() -> date:
        return singapore_now().date()

    def _append_recall_index(self, text: str, *, category: str, timestamp: str) -> None:
        raw_text = ""
        try:
            raw_text = self.recall_index_path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
        except Exception:
            payload = {"entries": []}
        record_json_consult(
            name="recall_index",
            path=str(self.recall_index_path),
            operation="read_for_append",
            chars=len(raw_text),
        )
        entries = payload.get("entries")
        if not isinstance(entries, list):
            entries = []
        keywords = sorted({word.strip(".,:;!?()[]").lower() for word in text.split() if len(word.strip(".,:;!?()[]")) > 3})
        entries.append(
            {
                "timestamp": timestamp,
                "category": category,
                "keywords": keywords[:12],
                "summary": text,
            }
        )
        payload["entries"] = entries[-200:]
        serialized = json.dumps(payload, indent=2)
        self.recall_index_path.write_text(serialized, encoding="utf-8")
        record_json_consult(
            name="recall_index",
            path=str(self.recall_index_path),
            operation="write_append",
            records=len(payload["entries"]),
            chars=len(serialized),
        )

    @staticmethod
    def _cap(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[-limit:]

    @staticmethod
    def _read_text(path: Path, *, section: str = "unknown") -> str:
        if not path.exists():
            record_memory_file(
                path=str(path),
                section=section,
                source="disk",
                chars=0,
                injected=False,
                exists=False,
            )
            return ""
        text = path.read_text(encoding="utf-8").strip()
        record_memory_file(
            path=str(path),
            section=section,
            source="disk",
            chars=len(text),
            injected=True,
            exists=True,
        )
        return text

    @staticmethod
    def _write_if_missing(path: Path, content: str) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
