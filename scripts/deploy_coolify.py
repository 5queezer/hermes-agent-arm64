#!/usr/bin/env python3
"""Deploy an exact Hermes image to multiple Coolify applications.

The script updates every application to the same immutable image tag, starts
and polls each deployment, and rolls already-updated applications back if any
application fails. It deliberately prints no application environment values or
Coolify response logs because those can contain credentials.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

TERMINAL_SUCCESS = {"finished"}
TERMINAL_FAILURE = {"failed", "cancelled", "canceled"}
MANAGED_MARKER = "# hermes-arm64 managed verifier"


class DeployError(RuntimeError):
    """A safe-to-print deployment error."""


@dataclass(frozen=True)
class AppTarget:
    uuid: str
    expected_username: str


@dataclass
class PreviousConfig:
    image_name: str
    image_tag: str
    post_deployment_command: str


class CoolifyClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise DeployError("COOLIFY_URL must be an HTTPS origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise DeployError("COOLIFY_URL must not contain a path, query, or fragment")
        self.host = parsed.hostname
        self.port = parsed.port
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "hermes-agent-arm64-deployer/1.0",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        connection = http.client.HTTPSConnection(
            self.host, self.port, timeout=self.timeout
        )
        try:
            connection.request(method, f"/api/v1{path}", body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DeployError(f"Coolify {method} {path} request failed") from exc
        finally:
            connection.close()

        if response.status >= 400:
            # Do not echo the response body: deployment logs and validation
            # responses can contain environment values.
            raise DeployError(
                f"Coolify {method} {path} returned HTTP {response.status}"
            )
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeployError(
                f"Coolify {method} {path} returned a non-JSON response"
            ) from exc
        if not isinstance(decoded, dict):
            raise DeployError(
                f"Coolify {method} {path} returned an unexpected response"
            )
        return decoded

    def get_application(self, uuid: str) -> dict[str, Any]:
        return self.request("GET", f"/applications/{uuid}")

    def update_application(self, uuid: str, payload: dict[str, Any]) -> None:
        response = self.request("PATCH", f"/applications/{uuid}", payload)
        if response.get("uuid") != uuid:
            raise DeployError(f"Coolify did not confirm application {uuid} update")

    def start_application(self, uuid: str) -> str:
        response = self.request("POST", f"/applications/{uuid}/start?force=true")
        deployment_uuid = response.get("deployment_uuid")
        if not isinstance(deployment_uuid, str) or not deployment_uuid:
            raise DeployError(f"Coolify did not queue a deployment for {uuid}")
        return deployment_uuid

    def get_deployment(self, deployment_uuid: str) -> dict[str, Any]:
        return self.request("GET", f"/deployments/{deployment_uuid}")


def parse_target(value: str) -> AppTarget:
    try:
        uuid, username = value.split(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("target must be UUID:@telegram_username") from exc
    uuid = uuid.strip()
    username = username.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", uuid):
        raise argparse.ArgumentTypeError("invalid Coolify application UUID")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,64}", username):
        raise argparse.ArgumentTypeError("invalid Telegram username")
    return AppTarget(uuid=uuid, expected_username=username)


def managed_post_command(existing: str, target: AppTarget, expected_sha: str) -> str:
    base = existing.split(MANAGED_MARKER, 1)[0].rstrip()
    verifier = (
        f"{MANAGED_MARKER}\n"
        "python3 /opt/data/scripts/verify_hermes_deployment.py "
        f"--expected-username {target.expected_username} "
        f"--expected-sha {expected_sha}"
    )
    if not base:
        return verifier
    return f"set -e\n{base}\n{verifier}"


def collect_log_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(collect_log_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(collect_log_text(item) for item in value)
    return ""


def wait_for_deployment(
    client: CoolifyClient,
    deployment_uuid: str,
    app_uuid: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    while time.monotonic() < deadline:
        deployment = client.get_deployment(deployment_uuid)
        status = str(deployment.get("status", "unknown")).lower()
        if status != last_status:
            print(f"{app_uuid}: deployment {deployment_uuid} is {status}", flush=True)
            last_status = status
        if status in TERMINAL_SUCCESS:
            log_text = collect_log_text(deployment.get("logs")).lower()
            if "post-deployment command failed." in log_text:
                raise DeployError(
                    f"application {app_uuid} post-deployment command failed"
                )
            return
        if status in TERMINAL_FAILURE:
            raise DeployError(
                f"application {app_uuid} deployment {deployment_uuid} ended as {status}"
            )
        time.sleep(poll_seconds)
    raise DeployError(
        f"application {app_uuid} deployment {deployment_uuid} timed out after "
        f"{timeout_seconds}s"
    )


def wait_for_application_health(
    client: CoolifyClient,
    target: AppTarget,
    image_name: str,
    image_tag: str,
    timeout_seconds: int = 180,
    poll_seconds: int = 5,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    while time.monotonic() < deadline:
        application = client.get_application(target.uuid)
        status = str(application.get("status", "unknown")).lower()
        if status != last_status:
            print(f"{target.uuid}: application is {status}", flush=True)
            last_status = status
        if application.get("docker_registry_image_name") != image_name:
            raise DeployError(f"application {target.uuid} image name changed unexpectedly")
        if application.get("docker_registry_image_tag") != image_tag:
            raise DeployError(f"application {target.uuid} image tag changed unexpectedly")
        if status in {"healthy", "running:healthy"}:
            return
        if status.startswith(("exited", "stopped", "error", "failed")):
            raise DeployError(f"application {target.uuid} became {status}")
        time.sleep(poll_seconds)
    raise DeployError(
        f"application {target.uuid} did not become healthy after {timeout_seconds}s"
    )


def previous_config(application: dict[str, Any], uuid: str) -> PreviousConfig:
    image_name = application.get("docker_registry_image_name")
    image_tag = application.get("docker_registry_image_tag")
    post_command = application.get("post_deployment_command") or ""
    if not isinstance(image_name, str) or not image_name:
        raise DeployError(f"application {uuid} has no Docker image name")
    if not isinstance(image_tag, str) or not image_tag:
        raise DeployError(f"application {uuid} has no Docker image tag")
    if not isinstance(post_command, str):
        raise DeployError(f"application {uuid} has an invalid post-deployment command")
    return PreviousConfig(image_name, image_tag, post_command)


def update_payload(
    image_name: str,
    image_tag: str,
    post_command: str,
) -> dict[str, Any]:
    return {
        "docker_registry_image_name": image_name,
        "docker_registry_image_tag": image_tag,
        "post_deployment_command": post_command,
    }


def health_payload(target: AppTarget, expected_sha: str) -> dict[str, Any]:
    command = (
        "python3 /opt/data/scripts/verify_hermes_deployment.py "
        f"--expected-username {target.expected_username} "
        f"--expected-sha {expected_sha} --timeout 20"
    )
    return {
        "health_check_enabled": True,
        "health_check_type": "command",
        "health_check_command": command,
        "health_check_interval": 30,
        "health_check_timeout": 45,
        "health_check_retries": 3,
        "health_check_start_period": 20,
    }


def recovery_payload(old: PreviousConfig, target: AppTarget) -> dict[str, Any]:
    payload = update_payload(
        old.image_name,
        old.image_tag,
        old.post_deployment_command,
    )
    old_sha = re.search(
        r"--expected-sha ([0-9a-f]{40})",
        old.post_deployment_command,
    )
    if MANAGED_MARKER in old.post_deployment_command and old_sha:
        payload.update(health_payload(target, old_sha.group(1)))
    else:
        payload["health_check_enabled"] = False
    return payload


def rollback(
    client: CoolifyClient,
    updated: list[AppTarget],
    previous: dict[str, PreviousConfig],
    timeout_seconds: int,
    poll_seconds: int,
) -> list[str]:
    failures: list[str] = []
    for target in reversed(updated):
        old = previous[target.uuid]
        try:
            print(f"{target.uuid}: rolling back to previous image", flush=True)
            client.update_application(
                target.uuid,
                recovery_payload(old, target),
            )
            deployment_uuid = client.start_application(target.uuid)
            wait_for_deployment(
                client,
                deployment_uuid,
                target.uuid,
                timeout_seconds,
                poll_seconds,
            )
            old_sha = re.search(
                r"--expected-sha ([0-9a-f]{40})",
                old.post_deployment_command,
            )
            if MANAGED_MARKER in old.post_deployment_command and old_sha:
                wait_for_application_health(
                    client,
                    target,
                    old.image_name,
                    old.image_tag,
                )
        except DeployError as exc:
            failures.append(f"{target.uuid}: {exc}")
    return failures


def deploy(args: argparse.Namespace) -> None:
    token = os.environ.get("COOLIFY_TOKEN", "")
    base_url = os.environ.get("COOLIFY_URL", "")
    if not token:
        raise DeployError("COOLIFY_TOKEN is not set")
    if not base_url:
        raise DeployError("COOLIFY_URL is not set")
    if len(args.app) < 2:
        raise DeployError("at least two --app targets are required")
    if len({target.uuid for target in args.app}) != len(args.app):
        raise DeployError("duplicate Coolify application UUID")
    if not re.fullmatch(r"[0-9a-f]{40}", args.tag):
        raise DeployError("--tag must be a full 40-character Git SHA")

    client = CoolifyClient(base_url, token, timeout=args.http_timeout)
    previous: dict[str, PreviousConfig] = {}
    for target in args.app:
        application = client.get_application(target.uuid)
        if application.get("build_pack") != "dockerimage":
            raise DeployError(f"application {target.uuid} is not a Docker image app")
        previous[target.uuid] = previous_config(application, target.uuid)

    configured: list[AppTarget] = []
    attempted: list[AppTarget] = []
    verified: list[AppTarget] = []
    try:
        for target in args.app:
            old = previous[target.uuid]
            post_command = managed_post_command(
                old.post_deployment_command, target, args.tag
            )
            payload = update_payload(args.image, args.tag, post_command)
            payload.update(health_payload(target, args.tag))
            client.update_application(target.uuid, payload)
            configured.append(target)

        for target in args.app:
            print(
                f"{target.uuid}: deploying {args.image}:{args.tag}", flush=True
            )
            deployment_uuid = client.start_application(target.uuid)
            attempted.append(target)
            wait_for_deployment(
                client,
                deployment_uuid,
                target.uuid,
                args.timeout,
                args.poll_interval,
            )
            wait_for_application_health(
                client,
                target,
                args.image,
                args.tag,
            )
            verified.append(target)
    except DeployError as original:
        # A queued deployment can activate the new image even when Coolify
        # reports a post-deployment verification failure. Compensate every
        # attempted deployment, not only the ones that verified successfully.
        restore_failures: list[str] = []
        for target in configured:
            if target in attempted:
                continue
            old = previous[target.uuid]
            try:
                client.update_application(
                    target.uuid,
                    recovery_payload(old, target),
                )
            except DeployError as exc:
                restore_failures.append(f"{target.uuid}: {exc}")
        rollback_failures = rollback(
            client,
            attempted,
            previous,
            args.timeout,
            args.poll_interval,
        )
        recovery_failures = restore_failures + rollback_failures
        if recovery_failures:
            raise DeployError(
                f"{original}; recovery also failed for: "
                f"{'; '.join(recovery_failures)}"
            ) from original
        raise

    print(f"Successfully deployed {len(verified)} Hermes applications.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", action="append", type=parse_target, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--poll-interval", type=int, default=10)
    parser.add_argument("--http-timeout", type=int, default=30)
    return parser


def main() -> int:
    try:
        deploy(build_parser().parse_args())
    except DeployError as exc:
        print(f"deployment failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
