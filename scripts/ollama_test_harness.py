"""
Standalone read-only harness for validating the Ollama OpenAI-compatible path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import cfg


PROMPT = "Respond with exactly the word: OK"
DEFAULT_TIMEOUT_SECONDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Ollama /v1/chat/completions path"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the full JSON response body",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    return parser.parse_args()


def trim_text(value: str, limit: int = 300) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def extract_assistant_message(data: Any) -> str | None:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, str):
        return None
    content = content.strip()
    return content or None


def main() -> int:
    args = parse_args()
    url = f"{cfg.ollama_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": cfg.ollama_model,
        "messages": [
            {"role": "user", "content": PROMPT},
        ],
    }

    try:
        response = requests.post(url, json=payload, timeout=args.timeout)
    except requests.RequestException as exc:
        print("[FAIL] Ollama test failed")
        print("status: request_error")
        print(f"error: {trim_text(str(exc))}")
        return 1

    body_text = response.text or ""
    parsed_json = None
    parsed_message = None
    try:
        parsed_json = response.json()
    except ValueError:
        parsed_json = None
    if parsed_json is not None:
        parsed_message = extract_assistant_message(parsed_json)

    if response.status_code == 200 and parsed_message and "OK" in parsed_message:
        print("[OK] Ollama test succeeded")
        print(f"model: {cfg.ollama_model}")
        print(f"status: {response.status_code}")
        print(f"response: {parsed_message}")
        print(f"body: {trim_text(body_text)}")
        if args.raw and parsed_json is not None:
            print(json.dumps(parsed_json, indent=2))
        return 0

    print("[FAIL] Ollama test failed")
    print(f"model: {cfg.ollama_model}")
    print(f"status: {response.status_code}")
    print(f"error: {trim_text(body_text)}")
    if parsed_message:
        print(f"response: {parsed_message}")
    else:
        print("response: <missing assistant message>")
    if args.raw and parsed_json is not None:
        print(json.dumps(parsed_json, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
