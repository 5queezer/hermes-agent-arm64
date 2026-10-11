from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "verify_hermes_deployment.py"
SPEC = importlib.util.spec_from_file_location("verify_hermes_deployment", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load verifier module from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeCmdline:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def read_bytes(self) -> bytes:
        return self.value


class GatewayProcessTests(unittest.TestCase):
    def check_command(self, command: bytes) -> bool:
        with patch.object(MODULE.Path, "glob", return_value=[FakeCmdline(command)]):
            return MODULE.gateway_running()

    def test_recognizes_direct_hermes_gateway_command(self) -> None:
        self.assertTrue(self.check_command(b"/opt/hermes/bin/hermes\0gateway\0run\0"))

    def test_recognizes_current_python_launcher_command(self) -> None:
        command = (
            b"/opt/hermes/.venv/bin/python\0-P\0-c\0"
            b"import os; sys.argv[0]='hermes'; main()\0gateway\0run\0--replace\0"
        )
        self.assertTrue(self.check_command(command))

    def test_ignores_unrelated_process_with_gateway_arguments(self) -> None:
        self.assertFalse(self.check_command(b"python\0unrelated.py\0gateway run\0"))


if __name__ == "__main__":
    unittest.main()
