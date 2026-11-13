import ast
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch


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

MCP_TOOL_SOURCE = '''\
# True while every strike in the current streak was the tool's own error payload (server reachable,
# call rejected); picks the open-breaker wording, since "unreachable" was false for that case (#11113).
_server_errors_all_application: dict[Any, bool] = {}
_CIRCUIT_BREAKER_THRESHOLD, _CIRCUIT_BREAKER_COOLDOWN_SEC = 3, 60.0


def _bump_server_error(server_name: str, *, application: bool = False) -> None:
    """Count a failure; at the threshold (re)stamp the breaker-open time. Keyed by the calling
    scope's connection so one profile's failing server never opens another profile's breaker.
    *application*: the call completed and the payload was an error (transport is fine)."""
    from tools.mcp_tool_scope import _resolve_server_key
    key = _resolve_server_key(server_name)
    n = _server_error_counts.get(key, 0) + 1
    _server_error_counts[key] = n
    _server_errors_all_application[key] = application and (n == 1 or _server_errors_all_application.get(key, False))
    if n >= _CIRCUIT_BREAKER_THRESHOLD:
        _server_breaker_opened_at[key] = time.monotonic()


def _reset_server_error(server_name: str) -> None:
    """Close the breaker on any unambiguous success signal."""
    from tools.mcp_tool_scope import _resolve_server_key
    key = _resolve_server_key(server_name)
    _server_error_counts[key] = 0
    _server_breaker_opened_at.pop(key, None)
    _server_errors_all_application.pop(key, None)
'''

MCP_HANDLERS_SOURCE = '''\
def _check_circuit_breaker(server_name: str) -> Optional[str]:
    """Open-breaker error, or None when calls may proceed. After the cooldown the breaker is
    half-open: the next call probes; success resets, failure re-bumps and re-arms the cooldown."""
    from tools.mcp_tool_scope import _resolve_server_key
    key = _resolve_server_key(server_name)
    failures = _core._server_error_counts.get(key, 0)
    age = time.monotonic() - _core._server_breaker_opened_at.get(key, 0.0)
    if failures < _core._CIRCUIT_BREAKER_THRESHOLD or age >= _core._CIRCUIT_BREAKER_COOLDOWN_SEC:
        return None
    retry_in = max(1, int(_core._CIRCUIT_BREAKER_COOLDOWN_SEC - age))
    if _core._server_errors_all_application.get(key):
        # The server answered every time; the calls were rejected. Calling it "unreachable" sent the
        # model to the user instead of to its own arguments (#11113).
        return tool_error(f"MCP server '{server_name}' rejected the last {failures} calls (it is reachable; see the "
                          f"error text those calls returned). Paused for ~{retry_in}s. Do NOT repeat the same call — "
                          f"fix the arguments/URL/target or use a different approach.")
    return tool_error(f"MCP server '{server_name}' is unreachable after {failures} consecutive failures. "
                      f"Auto-retry available in ~{retry_in}s. Do NOT retry "
                      f"this tool yet — use alternative approaches or ask the user to check the MCP server.")
'''


def write_upstream(root: Path) -> Path:
    tools = root / "tools"
    tools.mkdir()
    (tools / "mcp_tool.py").write_text(MCP_TOOL_SOURCE, encoding="utf-8")
    (tools / "mcp_tool_handlers.py").write_text(MCP_HANDLERS_SOURCE, encoding="utf-8")
    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        f"FROM debian:13.4\nRUN apt-get install -y \\\n{RUNTIME_PACKAGES} && true\n"
        "FROM python_deps AS runtime\n",
        encoding="utf-8",
    )
    return dockerfile


def load_function(source: str, name: str, namespace: dict) -> None:
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<prepared>", "exec"), namespace)


class PrepareUpstreamDockerfileTests(unittest.TestCase):
    def test_prepare_bakes_sudo_into_runtime_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = write_upstream(Path(temp_dir))

            MODULE.prepare(dockerfile)

            prepared = dockerfile.read_text(encoding="utf-8")
            self.assertIn("ca-certificates curl sudo iputils-ping", prepared)
            self.assertIn("ARG HERMES_GIT_SHA", prepared)
            self.assertIn("/opt/hermes/.hermes_build_sha", prepared)
            self.assertIn('"[0-9a-f]{40}"', prepared)

    def test_application_errors_do_not_open_transport_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dockerfile = write_upstream(root)
            MODULE.prepare(dockerfile)

            core = types.SimpleNamespace(
                _server_error_counts={},
                _server_breaker_opened_at={},
                _server_errors_all_application={},
                _CIRCUIT_BREAKER_THRESHOLD=3,
                _CIRCUIT_BREAKER_COOLDOWN_SEC=60.0,
            )
            namespace = {
                "Optional": Optional,
                "_core": core,
                "_server_error_counts": core._server_error_counts,
                "_server_breaker_opened_at": core._server_breaker_opened_at,
                "_server_errors_all_application": core._server_errors_all_application,
                "_CIRCUIT_BREAKER_THRESHOLD": 3,
                "time": types.SimpleNamespace(monotonic=lambda: 100.0),
                "tool_error": lambda message: {"error": message},
            }
            load_function((root / "tools" / "mcp_tool.py").read_text(encoding="utf-8"),
                          "_bump_server_error", namespace)
            load_function((root / "tools" / "mcp_tool.py").read_text(encoding="utf-8"),
                          "_reset_server_error", namespace)
            load_function((root / "tools" / "mcp_tool_handlers.py").read_text(encoding="utf-8"),
                          "_check_circuit_breaker", namespace)
            scope = types.ModuleType("tools.mcp_tool_scope")
            scope._resolve_server_key = lambda server_name: server_name
            with patch.dict(sys.modules, {"tools.mcp_tool_scope": scope}):
                for _ in range(3):
                    namespace["_bump_server_error"]("srv", application=True)
                self.assertIsNone(namespace["_check_circuit_breaker"]("srv"))

                for _ in range(2):
                    namespace["_bump_server_error"]("srv")
                namespace["_bump_server_error"]("srv", application=True)
                namespace["_bump_server_error"]("srv")
                self.assertIsNone(namespace["_check_circuit_breaker"]("srv"))

                for _ in range(3):
                    namespace["_bump_server_error"]("srv")
                blocked = namespace["_check_circuit_breaker"]("srv")
            self.assertIn("unreachable", blocked["error"])
            self.assertNotIn("_server_errors_all_application",
                             (root / "tools" / "mcp_tool.py").read_text(encoding="utf-8"))
            self.assertNotIn("rejected the last",
                             (root / "tools" / "mcp_tool_handlers.py").read_text(encoding="utf-8"))

    def test_prepare_fails_closed_when_mcp_anchors_drift(self) -> None:
        for filename, drifted in (
            ("mcp_tool.py", MCP_TOOL_SOURCE.replace("Count a failure", "Record a failure")),
            ("mcp_tool_handlers.py", MCP_HANDLERS_SOURCE.replace("rejected the last", "rejected recent")),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                dockerfile = write_upstream(root)
                original_dockerfile = dockerfile.read_text(encoding="utf-8")
                (root / "tools" / filename).write_text(drifted, encoding="utf-8")

                with self.assertRaisesRegex(RuntimeError, "upstream MCP"):
                    MODULE.prepare(dockerfile)
                self.assertEqual(drifted, (root / "tools" / filename).read_text(encoding="utf-8"))
                self.assertEqual(original_dockerfile, dockerfile.read_text(encoding="utf-8"))

    def test_prepare_fails_closed_when_upstream_layout_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text("FROM debian:13.4\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "runtime package list"):
                MODULE.prepare(dockerfile)

    def test_prepare_fails_closed_when_runtime_stage_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                f"RUN apt-get install -y \\\n{RUNTIME_PACKAGES} && true\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "runtime stage"):
                MODULE.prepare(dockerfile)
