from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from pathlib import Path, PurePosixPath


class LLMGatewayService:
    _WRITE_TOKENS = (
        ">",
        ">>",
        "set-content",
        "add-content",
        "out-file",
        "remove-item",
        "move-item",
        "copy-item",
        "new-item",
        "git add",
        "git commit",
        "git restore",
        "git reset",
        " del ",
        " rm ",
        " mv ",
        " cp ",
        " touch ",
    )
    _ESCAPE_TOKENS = (
        "cmd /c",
        "bash -c",
        "sh -c",
        "powershell -c",
        "pwsh -c",
    )
    _ABSOLUTE_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s\"']+")

    def __init__(self, *, repo_root: Path, policy_path: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.policy_path = policy_path.resolve(strict=False)
        self._configured = False
        self._read_only_patterns: list[str] = []
        self._read_write_patterns: list[str] = []
        self._terminal_enabled = False
        self._terminal_default_cwd = "."
        self._terminal_program = ""
        self._terminal_args: list[str] = []
        self.reload_policy()

    def reload_policy(self) -> None:
        if not self.policy_path.exists():
            self._configured = False
            self._read_only_patterns = []
            self._read_write_patterns = []
            self._terminal_enabled = False
            self._terminal_default_cwd = "."
            return

        payload = json.loads(self.policy_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("LLM gateway policy must be a JSON object.")

        read_only = payload.get("read_only")
        read_write = payload.get("read_write")
        terminal = payload.get("terminal")
        if not isinstance(read_only, list) or not all(isinstance(item, str) for item in read_only):
            raise ValueError("LLM gateway policy read_only must be a list of strings.")
        if not isinstance(read_write, list) or not all(isinstance(item, str) for item in read_write):
            raise ValueError("LLM gateway policy read_write must be a list of strings.")
        if terminal is not None and not isinstance(terminal, dict):
            raise ValueError("LLM gateway terminal config must be an object.")

        self._configured = True
        self._read_only_patterns = [self._normalize_pattern(item) for item in read_only]
        self._read_write_patterns = [self._normalize_pattern(item) for item in read_write]
        self._terminal_enabled = bool((terminal or {}).get("enabled"))
        self._terminal_default_cwd = str((terminal or {}).get("default_cwd") or ".")
        self._terminal_program, self._terminal_args = self._detect_terminal_runner()

    def is_configured(self) -> bool:
        return self._configured

    def prompt_summary(self) -> str:
        if not self.is_configured():
            return "Gateway policy is not configured. File and terminal tools are unavailable."
        terminal_status = "enabled" if self._terminal_enabled else "disabled"
        return (
            f"Repo root: {self.repo_root}\n"
            f"Read-only paths: {', '.join(self._read_only_patterns) or 'none'}\n"
            f"Read-write paths: {', '.join(self._read_write_patterns) or 'none'}\n"
            f"Terminal: {terminal_status} via {self.terminal_shell_name}. Writes require read_write access."
        )

    def access_snapshot(self, path: str | None = None) -> dict[str, object]:
        relative_path = None
        access = None
        resolved = None
        if path is not None:
            resolved_path = self.resolve_path(path)
            resolved = str(resolved_path)
            relative_path = self._relative_posix(resolved_path)
            access = self.access_level(path) if self.is_configured() else "none"
        return {
            "configured": self.is_configured(),
            "repo_root": str(self.repo_root),
            "policy_path": str(self.policy_path),
            "read_only": list(self._read_only_patterns),
            "read_write": list(self._read_write_patterns),
            "terminal": {
                "enabled": self._terminal_enabled,
                "default_cwd": self._terminal_default_cwd,
                "shell": self.terminal_shell_name,
            },
            "path": path,
            "resolved_path": resolved,
            "relative_path": relative_path,
            "access": access,
        }

    def access_level(self, path: str) -> str:
        self._require_configured()
        resolved = self.resolve_path(path)
        relative = self._relative_posix(resolved)
        if self._matches_any(relative, self._read_write_patterns, is_dir=resolved.is_dir()):
            return "read_write"
        if self._matches_any(relative, self._read_only_patterns, is_dir=resolved.is_dir()):
            return "read_only"
        return "none"

    def list_dir(self, path: str) -> dict[str, object]:
        directory = self.resolve_path(path)
        if not directory.exists() or not directory.is_dir():
            raise ValueError("path must be an existing directory.")
        self._require_read_access(directory)
        entries: list[dict[str, object]] = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            access = self._safe_access_for_child(child)
            if access == "none":
                continue
            entries.append(
                {
                    "name": child.name,
                    "path": self._relative_posix(child),
                    "kind": "directory" if child.is_dir() else "file",
                    "access": access,
                }
            )
        return {"path": self._relative_posix(directory), "entries": entries}

    def tree(self, path: str, *, max_depth: int = 3) -> dict[str, object]:
        root = self.resolve_path(path)
        if not root.exists() or not root.is_dir():
            raise ValueError("path must be an existing directory.")
        self._require_read_access(root)
        depth = max(0, min(max_depth, 8))
        return self._tree_node(root, depth)

    def read_text(self, path: str) -> dict[str, object]:
        file_path = self.resolve_path(path)
        if not file_path.exists() or not file_path.is_file():
            raise ValueError("path must be an existing file.")
        self._require_read_access(file_path)
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("File is not valid UTF-8 text.") from exc
        return {"path": self._relative_posix(file_path), "content": content}

    def search_text(self, query: str, *, path: str = ".", max_results: int = 20) -> dict[str, object]:
        if not query.strip():
            raise ValueError("query is required.")
        root = self.resolve_path(path)
        if not root.exists():
            raise ValueError("path must exist.")
        self._require_read_access(root)
        limit = max(1, min(max_results, 100))
        matches: list[dict[str, object]] = []
        for file_path in self._iter_accessible_files(root):
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query.lower() not in line.lower():
                    continue
                matches.append(
                    {
                        "path": self._relative_posix(file_path),
                        "line_number": line_number,
                        "line": line,
                    }
                )
                if len(matches) >= limit:
                    return {"query": query, "path": self._relative_posix(root), "matches": matches}
        return {"query": query, "path": self._relative_posix(root), "matches": matches}

    def write_text(self, path: str, content: str) -> dict[str, object]:
        file_path = self.resolve_path(path)
        self._require_write_access(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return {"path": self._relative_posix(file_path), "bytes_written": len(content.encode("utf-8"))}

    def append_text(self, path: str, content: str) -> dict[str, object]:
        file_path = self.resolve_path(path)
        self._require_write_access(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return {"path": self._relative_posix(file_path), "bytes_appended": len(content.encode("utf-8"))}

    def terminal_exec(self, command: str, *, cwd: str | None = None, mode: str = "read_only") -> dict[str, object]:
        self._require_configured()
        if not self._terminal_enabled:
            raise RuntimeError("Terminal access is disabled by policy.")
        if mode not in {"read_only", "read_write"}:
            raise ValueError("mode must be read_only or read_write.")

        cwd_path = self.resolve_path(cwd or self._terminal_default_cwd)
        if not cwd_path.exists() or not cwd_path.is_dir():
            raise ValueError("cwd must be an existing directory.")

        cwd_access = self.access_level(self._relative_posix(cwd_path))
        if cwd_access == "none":
            raise PermissionError("cwd is outside the configured gateway access scope.")
        if mode == "read_write" and not self._is_within_read_write_scope(cwd_path):
            raise PermissionError("read_write terminal mode requires a read_write cwd.")

        self._validate_terminal_command(command, mode=mode)
        completed = subprocess.run(
            [self._terminal_program, *self._terminal_args, command],
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        return {
            "command": command,
            "cwd": self._relative_posix(cwd_path),
            "mode": mode,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    @property
    def terminal_shell_name(self) -> str:
        return Path(self._terminal_program).name if self._terminal_program else "unavailable"

    def resolve_path(self, path: str) -> Path:
        candidate = Path(path or ".")
        resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (self.repo_root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.repo_root)
        except ValueError as exc:
            raise PermissionError("Path is outside the repo root.") from exc
        return resolved

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise RuntimeError(f"LLM gateway policy is not configured at {self.policy_path}.")

    def _require_read_access(self, path: Path) -> None:
        self._require_configured()
        if self.access_level(self._relative_posix(path)) == "none":
            raise PermissionError("Path is not readable under the gateway policy.")

    def _require_write_access(self, path: Path) -> None:
        self._require_configured()
        if self.access_level(self._relative_posix(path)) != "read_write":
            raise PermissionError("Path is not writable under the gateway policy.")

    def _safe_access_for_child(self, path: Path) -> str:
        try:
            return self.access_level(self._relative_posix(path))
        except PermissionError:
            return "none"

    def _tree_node(self, path: Path, depth: int) -> dict[str, object]:
        node = {
            "name": path.name or ".",
            "path": self._relative_posix(path),
            "kind": "directory",
            "access": self.access_level(self._relative_posix(path)),
            "children": [],
        }
        if depth <= 0:
            return node
        children: list[dict[str, object]] = []
        for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            access = self._safe_access_for_child(child)
            if access == "none":
                continue
            if child.is_dir():
                children.append(self._tree_node(child, depth - 1))
            else:
                children.append(
                    {
                        "name": child.name,
                        "path": self._relative_posix(child),
                        "kind": "file",
                        "access": access,
                    }
                )
        node["children"] = children
        return node

    def _iter_accessible_files(self, root: Path):
        if root.is_file():
            if self.access_level(self._relative_posix(root)) != "none":
                yield root
            return
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if self.access_level(self._relative_posix(file_path)) == "none":
                continue
            yield file_path

    def _validate_terminal_command(self, command: str, *, mode: str) -> None:
        lowered = f" {command.lower()} "
        if any(token in lowered for token in self._ESCAPE_TOKENS):
            raise PermissionError("Nested shell escapes are blocked.")
        for match in self._ABSOLUTE_PATH_PATTERN.finditer(command):
            path = Path(match.group(0))
            try:
                path.resolve(strict=False).relative_to(self.repo_root)
            except ValueError as exc:
                raise PermissionError("Command references an absolute path outside the repo root.") from exc
        if mode == "read_only" and any(token in lowered for token in self._WRITE_TOKENS):
            raise PermissionError("Write or destructive terminal commands are blocked in read_only mode.")

    @staticmethod
    def _detect_terminal_runner() -> tuple[str, list[str]]:
        system = platform.system().lower()
        if system == "windows":
            return ("powershell", ["-NoProfile", "-Command"])

        shell = os.getenv("SHELL", "").strip()
        shell_name = Path(shell).name.lower() if shell else ""
        if shell and shell_name in {"bash", "zsh", "sh", "ksh"}:
            return (shell, ["-lc"])
        return ("/bin/sh", ["-lc"])

    def _matches_any(self, relative_path: str, patterns: list[str], *, is_dir: bool) -> bool:
        relative = PurePosixPath(relative_path)
        for pattern in patterns:
            if relative.match(pattern):
                return True
            if is_dir and self._directory_matches_pattern(relative_path, pattern):
                return True
        return False

    @staticmethod
    def _normalize_pattern(pattern: str) -> str:
        normalized = pattern.replace("\\", "/").lstrip("./")
        return normalized or "."

    @staticmethod
    def _directory_matches_pattern(relative_path: str, pattern: str) -> bool:
        if relative_path == ".":
            return True
        if PurePosixPath(f"{relative_path}/__probe__").match(pattern):
            return True
        static_prefix = pattern.split("*", 1)[0].rstrip("/")
        return bool(static_prefix) and static_prefix.startswith(f"{relative_path}/")

    def _relative_posix(self, path: Path) -> str:
        relative = path.resolve(strict=False).relative_to(self.repo_root)
        value = relative.as_posix()
        return value or "."

    def _is_within_read_write_scope(self, path: Path) -> bool:
        relative_path = self._relative_posix(path)
        if relative_path == ".":
            return False
        relative = PurePosixPath(relative_path)
        for pattern in self._read_write_patterns:
            if relative.match(pattern):
                return True
            static_prefix = pattern.split("*", 1)[0].rstrip("/")
            if static_prefix and (relative_path == static_prefix or relative_path.startswith(f"{static_prefix}/")):
                return True
        return False
