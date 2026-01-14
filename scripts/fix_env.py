from __future__ import annotations

import re
from pathlib import Path


def main() -> int:
    env_path = Path(".env")
    if not env_path.exists():
        print("No .env found")
        return 1

    raw_lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()

    key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

    entries: list[tuple[str, list[str]]] = []
    current_key: str | None = None
    current_vals: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_vals
        if current_key is not None:
            entries.append((current_key, current_vals))
        current_key = None
        current_vals = []

    for line in raw_lines:
        if not line.strip() or line.lstrip().startswith("#"):
            flush()
            continue

        m = key_re.match(line)
        if m:
            flush()
            current_key = m.group(1)
            current_vals = [m.group(2)]
            continue

        # Continuation line caused by wrapped values in .env
        if current_key is not None:
            current_vals.append(line)

    flush()

    # Keys whose values should be concatenated without whitespace/newlines
    concat_keys = {
        "BOT_TOKEN",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEYS",
        "NEWS_RSS_URLS",
        "TARGET_CHANNEL_ID",
    }

    values: dict[str, str] = {}
    system_prompt_lines: list[str] = []

    for key, value_lines in entries:
        if key == "SYSTEM_PROMPT":
            system_prompt_lines = value_lines
            continue

        if key in concat_keys:
            values[key] = "".join(part.strip() for part in value_lines)
        else:
            # Default behavior: keep it single-line, stripping whitespace
            values[key] = "".join(part.strip() for part in value_lines)

    # Remove 3dnews from RSS list
    rss = values.get("NEWS_RSS_URLS", "")
    if rss:
        parts = [p.strip() for p in rss.split(",") if p.strip()]
        filtered = [p for p in parts if "3dnews.ru" not in p.lower()]
        values["NEWS_RSS_URLS"] = ",".join(filtered)

    # Move SYSTEM_PROMPT into a file (compose env_file does not support multiline values)
    prompt_file = Path("prompts/system_prompt.txt")
    wrote_prompt = False
    if system_prompt_lines:
        prompt_file.parent.mkdir(parents=True, exist_ok=True)

        text = "\n".join(system_prompt_lines)
        if text.startswith('"'):
            text = text[1:]
        if text.endswith('"'):
            text = text[:-1]

        prompt_file.write_text(text, encoding="utf-8")
        values.pop("SYSTEM_PROMPT", None)
        values["SYSTEM_PROMPT_FILE"] = str(prompt_file)
        wrote_prompt = True

    # Write backup
    backup_path = Path(".env.bak")
    backup_path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

    # Write normalized .env (compose-friendly, single-line values)
    preferred_order = [
        "BOT_TOKEN",
        "GOOGLE_API_KEY",
        "GOOGLE_API_KEYS",
        "OPENAI_API_KEY",
        "TARGET_CHANNEL_ID",
        "NEWS_RSS_URLS",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "ADMIN_USER_IDS",
        "AUTO_PUBLISH",
        "MODERATION",
        "MAX_ITEMS_PER_RUN",
        "CANDIDATES_FOR_LLM",
        "WEB_SEARCH_CALLS_LIMIT",
        "NO_REPEAT_HOURS",
        "POST_MAX_CHARS",
        "POST_MAX_EMOJIS",
        "IMAGE_SOURCE",
        "IMAGE_SEARCH_MAX_RESULTS",
        "IMAGE_SEARCH_SAFESEARCH",
        "IMAGE_SEARCH_RETRIES",
        "IMAGE_SEARCH_RETRY_DELAY_SEC",
        "CANDIDATE_POOL_MULTIPLIER",
        "MAX_CANDIDATES_PER_SOURCE",
        "SOURCE_COOLDOWN_HOURS",
        "SOURCE_COOLDOWN_CAP_PENALTY",
        "SYSTEM_PROMPT_FILE",
    ]

    out_lines: list[str] = []
    seen: set[str] = set()

    for k in preferred_order:
        if k in values and values[k] != "":
            out_lines.append(f"{k}={values[k]}")
            seen.add(k)

    for k in sorted(values.keys()):
        if k in seen:
            continue
        out_lines.append(f"{k}={values[k]}")

    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print("OK: rewrote .env (backup: .env.bak)")
    print("OK: removed 3dnews from NEWS_RSS_URLS:", "3dnews.ru" not in values.get("NEWS_RSS_URLS", "").lower())
    print("OK: wrote SYSTEM_PROMPT_FILE:", str(prompt_file) if wrote_prompt else "(no SYSTEM_PROMPT found)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
