from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "deploy_coolify.py"
SPEC = importlib.util.spec_from_file_location("deploy_coolify", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load deployment module from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self, deployment_statuses: list[str]) -> None:
        self.apps: dict[str, dict[str, object]] = {
            "main-app": {
                "build_pack": "dockerimage",
                "docker_registry_image_name": "old/main",
                "docker_registry_image_tag": "old-main-tag",
                "post_deployment_command": "echo main",
                "status": "running:unknown",
            },
            "career-app": {
                "build_pack": "dockerimage",
                "docker_registry_image_name": "old/career",
                "docker_registry_image_tag": "old-career-tag",
                "post_deployment_command": "",
                "status": "running:unknown",
            },
        }
        self.deployment_statuses = iter(deployment_statuses)
        self.deployments: dict[str, dict[str, object]] = {}
        self.started: list[str] = []

    def get_application(self, uuid: str) -> dict[str, object]:
        return dict(self.apps[uuid])

    def update_application(self, uuid: str, payload: dict[str, object]) -> None:
        self.apps[uuid].update(payload)

    def start_application(self, uuid: str) -> str:
        deployment_uuid = f"deployment-{len(self.started) + 1}"
        self.started.append(uuid)
        requested_status = next(self.deployment_statuses)
        usernames = {
            "main-app": "hermes_chee6Law_bot",
            "career-app": "raspi_kerf_bot",
        }
        image_tag = str(self.apps[uuid]["docker_registry_image_tag"])
        logs = (
            f"verified @{usernames[uuid]}: gateway running, Codex logged in, "
            f"revision {image_tag[:12]}"
        )
        status = requested_status
        if requested_status == "post_failed":
            status = "finished"
            logs = "Post-deployment command failed."
        self.deployments[deployment_uuid] = {"status": status, "logs": logs}
        if status == "finished" and self.apps[uuid].get("health_check_enabled"):
            self.apps[uuid]["status"] = "running:healthy"
        return deployment_uuid

    def get_deployment(self, deployment_uuid: str) -> dict[str, object]:
        return self.deployments[deployment_uuid]


def args() -> argparse.Namespace:
    return argparse.Namespace(
        app=[
            MODULE.AppTarget("main-app", "hermes_chee6Law_bot"),
            MODULE.AppTarget("career-app", "raspi_kerf_bot"),
        ],
        image="ghcr.io/5queezer/hermes-agent-arm64",
        tag="a" * 40,
        timeout=1,
        poll_interval=0,
        http_timeout=1,
    )


class DeployTests(unittest.TestCase):
    def test_managed_verifier_is_replaced_not_duplicated(self) -> None:
        target = MODULE.AppTarget("main-app", "hermes_chee6Law_bot")
        first = MODULE.managed_post_command("echo before", target, "a" * 40)
        second = MODULE.managed_post_command(first, target, "b" * 40)
        self.assertEqual(second.count(MODULE.MANAGED_MARKER), 1)
        self.assertIn("echo before", second)
        self.assertIn("b" * 40, second)
        self.assertNotIn("a" * 40, second)

    def test_success_requires_both_terminal_deployments(self) -> None:
        client = FakeClient(["finished", "finished"])
        with (
            patch.dict(os.environ, {"COOLIFY_URL": "https://coolify.example", "COOLIFY_TOKEN": "token"}),
            patch.object(MODULE, "CoolifyClient", return_value=client),
        ):
            MODULE.deploy(args())
        self.assertEqual(client.started, ["main-app", "career-app"])
        for app in client.apps.values():
            self.assertEqual(
                app["docker_registry_image_name"],
                "ghcr.io/5queezer/hermes-agent-arm64",
            )
            self.assertEqual(app["docker_registry_image_tag"], "a" * 40)

    def test_post_deploy_failure_restores_both_applications(self) -> None:
        client = FakeClient(
            ["finished", "post_failed", "finished", "finished"]
        )
        with (
            patch.dict(os.environ, {"COOLIFY_URL": "https://coolify.example", "COOLIFY_TOKEN": "token"}),
            patch.object(MODULE, "CoolifyClient", return_value=client),
            self.assertRaises(MODULE.DeployError),
        ):
            MODULE.deploy(args())
        self.assertEqual(
            client.started,
            ["main-app", "career-app", "career-app", "main-app"],
        )
        self.assertEqual(client.apps["main-app"]["docker_registry_image_name"], "old/main")
        self.assertEqual(
            client.apps["career-app"]["docker_registry_image_name"], "old/career"
        )


if __name__ == "__main__":
    unittest.main()
