#!/usr/bin/env python3
"""Verify a freshly deployed Hermes gateway without exposing credentials."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

HERMES_BIN = Path("/opt/hermes/.venv/bin/hermes")
BUILD_SHA = Path("/opt/hermes/.hermes_build_sha")
HERMES_ENV = Path("/opt/data/.env")


class VerificationError(RuntimeError):
    """A safe-to-print verification failure."""


def read_dotenv_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    prefix = f"{key}="
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return ""


def gateway_running() -> bool:
    for cmdline_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = cmdline_path.read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except (OSError, PermissionError):
            continue
        if re.search(
            r"(?:^|\s)(?:\S*/)?hermes\s+gateway\s+run(?:\s|$)", command
        ):
            return True
    return False


def codex_logged_in() -> bool:
    if not HERMES_BIN.is_file():
        raise VerificationError(f"Hermes executable is missing at {HERMES_BIN}")
    try:
        result = subprocess.run(
            [str(HERMES_BIN), "auth", "status", "openai-codex"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("Codex authentication check timed out") from exc
    return result.returncode == 0 and "logged in" in result.stdout.lower()


def telegram_username() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "") or read_dotenv_value(
        HERMES_ENV, "TELEGRAM_BOT_TOKEN"
    )
    if not token:
        raise VerificationError("Telegram bot token is unavailable")
    connection = http.client.HTTPSConnection(  # nosemgrep: httpsconnection-detected
        "api.telegram.org",
        timeout=15,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "GET",
            f"/bot{quote(token, safe=':')}/getMe",
            headers={
                "Accept": "application/json",
                "User-Agent": "hermes-deploy-verifier/1.0",
            },
        )
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            raise VerificationError(
                f"Telegram getMe returned HTTP {response.status}"
            )
        payload = json.loads(raw)
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        raise VerificationError("Telegram getMe request failed") from exc
    finally:
        connection.close()
    username = payload.get("result", {}).get("username")
    if not payload.get("ok") or not isinstance(username, str):
        raise VerificationError("Telegram getMe returned an invalid response")
    return username


def verify(expected_username: str, expected_sha: str, timeout: int) -> None:
    if not BUILD_SHA.is_file():
        raise VerificationError("baked Hermes build SHA is missing")
    actual_sha = BUILD_SHA.read_text(encoding="utf-8").strip()
    if actual_sha != expected_sha:
        raise VerificationError(
            f"image revision mismatch: expected {expected_sha[:12]}, got {actual_sha[:12]}"
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gateway_running():
            break
        threading.Event().wait(3)
    else:
        raise VerificationError("Hermes gateway process did not become ready")

    if not codex_logged_in():
        raise VerificationError("OpenAI Codex authentication is not logged in")

    actual_username = telegram_username()
    if actual_username.lower() != expected_username.lstrip("@").lower():
        raise VerificationError(
            f"Telegram identity mismatch: expected @{expected_username.lstrip('@')}, "
            f"got @{actual_username}"
        )

    print(
        f"verified @{actual_username}: gateway running, Codex logged in, "
        f"revision {actual_sha[:12]}",
        file=sys.stderr,
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-username", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha):
        print("verification failed: expected SHA must be 40 lowercase hex characters", file=sys.stderr)
        return 2
    try:
        verify(args.expected_username, args.expected_sha, args.timeout)
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
