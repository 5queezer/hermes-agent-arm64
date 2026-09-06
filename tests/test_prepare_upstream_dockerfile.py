import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "prepare_upstream_dockerfile.py"
SPEC = importlib.util.spec_from_file_location("prepare_upstream_dockerfile", MODULE_PATH)
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
