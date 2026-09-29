import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "prepare_upstream_dockerfile.py"
SPEC = importlib.util.spec_from_file_location(
    "prepare_upstream_dockerfile", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RUNTIME_PACKAGES = (
    "    ca-certificates curl iputils-ping python3 python-is-python3 "
    "ripgrep ffmpeg gcc g++ make cmake python3-dev python3-venv "
    "libffi-dev libolm-dev libatomic1 procps git openssh-client "
    "docker-cli xz-utils"
)


class PrepareUpstreamDockerfileTests(unittest.TestCase):
    def run_revision_step(self, expected_sha, stamp_sha, provenance_sha):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                f"FROM debian:13.4\nRUN apt-get install -y {RUNTIME_PACKAGES}\n"
                "FROM runtime AS final\n",
                encoding="utf-8",
            )
            (root / "install-stamp.json").write_text(
                json.dumps({"commit": stamp_sha}), encoding="utf-8"
            )
            (root / "image-provenance.json").write_text(
                json.dumps({"revision": provenance_sha}), encoding="utf-8"
            )
            MODULE.prepare(dockerfile)
            final_stage = dockerfile.read_text().split("FROM runtime AS final\n")[1]
            self.assertIn("ARG HERMES_GIT_SHA", final_stage)
            command = next(
                line[4:] for line in final_stage.splitlines() if line.startswith("RUN ")
            )
            argv = shlex.split(command)
            argv[0] = sys.executable
            argv[-1] = (
                argv[-1]
                .replace("/opt/hermes/", f"{root}/")
                .replace("/etc/hermes/", f"{root}/")
            )
            result = subprocess.run(
                argv,
                env={**os.environ, "HERMES_GIT_SHA": expected_sha},
                capture_output=True,
                text=True,
            )
            marker = root / ".hermes_build_sha"
            return result.returncode, marker.read_text() if marker.exists() else None

    def test_final_stage_preserves_revision_for_existing_health_checks(self):
        sha = "a" * 40
        self.assertEqual(self.run_revision_step(sha, sha, sha), (0, sha + "\n"))

    def test_build_rejects_missing_or_wrong_upstream_provenance(self):
        for revision in (None, "0" * 40, "b" * 40):
            with self.subTest(revision=revision):
                code, marker = self.run_revision_step("a" * 40, "a" * 40, revision)
                self.assertNotEqual(code, 0)
                self.assertIsNone(marker)

    def test_build_rejects_wrong_install_stamp(self):
        code, marker = self.run_revision_step("a" * 40, "b" * 40, "a" * 40)
        self.assertNotEqual(code, 0)
        self.assertIsNone(marker)

    def test_build_rejects_invalid_build_argument(self):
        for sha in ("", "a" * 7, "0" * 40, "G" * 40):
            with self.subTest(sha=sha):
                code, marker = self.run_revision_step(sha, sha, sha)
                self.assertNotEqual(code, 0)
                self.assertIsNone(marker)

    def test_prepare_bakes_sudo_into_runtime_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                f"FROM debian:13.4\nRUN apt-get install -y \\\n+{RUNTIME_PACKAGES} && true\n",
                encoding="utf-8",
            )

            MODULE.prepare(dockerfile)

            self.assertIn(
                "ca-certificates curl sudo iputils-ping",
                dockerfile.read_text(encoding="utf-8"),
            )

    def test_prepare_fails_closed_when_upstream_layout_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text("FROM debian:13.4\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "runtime package list"):
                MODULE.prepare(dockerfile)
