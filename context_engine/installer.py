"""Auto-installer — detects installed AI coding tools and configures the hook.

Supported platforms
-------------------
Claude Code  — .claude/settings.json   (UserPromptSubmit hook)
Cursor       — .cursor/rules/context-engine.mdc
Windsurf     — .windsurf/rules/context-engine.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform definitions
# ---------------------------------------------------------------------------

@dataclass
class Platform:
    name: str
    detection_dir: str          # relative to repo root — presence means installed
    config_path: str            # file to write
    write_fn: str               # name of the writer function below


_PLATFORMS: list[Platform] = [
    Platform(
        name="Claude Code",
        detection_dir=".claude",
        config_path=".claude/settings.json",
        write_fn="claude",
    ),
    Platform(
        name="Cursor",
        detection_dir=".cursor",
        config_path=".cursor/rules/context-engine.mdc",
        write_fn="cursor",
    ),
    Platform(
        name="Windsurf",
        detection_dir=".windsurf",
        config_path=".windsurf/rules/context-engine.md",
        write_fn="windsurf",
    ),
]


# ---------------------------------------------------------------------------
# Per-platform writers
# ---------------------------------------------------------------------------

def _write_claude(dest: Path) -> str:
    """Merge or create .claude/settings.json with the UserPromptSubmit hook."""
    existing: dict = {}
    if dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    hook_entry = {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": "python context_engine/hooks/user_prompt_submit.py",
            }
        ],
    }

    hooks = existing.setdefault("hooks", {})
    ups_list: list[dict] = hooks.setdefault("UserPromptSubmit", [])

    # Avoid duplicate: check if our command is already present.
    already = any(
        any(
            h.get("command") == hook_entry["hooks"][0]["command"]
            for h in entry.get("hooks", [])
        )
        for entry in ups_list
    )
    if not already:
        ups_list.append(hook_entry)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return "UserPromptSubmit hook registered"


_RULES_CONTENT = """\
# context-engine

Use context from `additionalContext` if provided — it contains the most
relevant functions from this codebase for the current query, pre-selected
by context-engine (AST + call graph analysis). Prefer these over searching
the codebase yourself when the provided context is sufficient.
"""


def _write_rules(dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_RULES_CONTENT, encoding="utf-8")
    return f"Rules file written"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install(root: Path) -> list[tuple[str, str, str]]:
    """Detect platforms under *root* and write configs.

    Returns list of (platform_name, config_path, status_message).
    """
    results: list[tuple[str, str, str]] = []

    for platform in _PLATFORMS:
        detection = root / platform.detection_dir
        if not detection.exists():
            continue

        dest = root / platform.config_path

        try:
            if platform.write_fn == "claude":
                status = _write_claude(dest)
            else:
                status = _write_rules(dest)
        except OSError as exc:
            status = f"ERROR: {exc}"

        results.append((platform.name, platform.config_path, status))

    return results
