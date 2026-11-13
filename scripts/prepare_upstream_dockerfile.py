#!/usr/bin/env python3
"""Prepare the upstream Hermes Dockerfile for this deployment environment."""

import argparse
from pathlib import Path

PACKAGE_ANCHOR = "ca-certificates curl iputils-ping"
PREPARED_PACKAGES = "ca-certificates curl sudo iputils-ping"
RUNTIME_STAGE_ANCHOR = "FROM python_deps AS runtime\n"
BUILD_SHA_INSTRUCTIONS = (
    "ARG HERMES_GIT_SHA\n"
    "RUN python3 -c 'import os, re; from pathlib import Path; "
    "sha = os.environ[\"HERMES_GIT_SHA\"]; "
    "assert re.fullmatch(r\"[0-9a-f]{40}\", sha), \"invalid HERMES_GIT_SHA\"; "
    "Path(\"/opt/hermes/.hermes_build_sha\").write_text(sha + \"\\n\")'\n"
)

MCP_TOOL_EDITS = (
    (
        '# True while every strike in the current streak was the tool\'s own error payload (server reachable,\n'
        '# call rejected); picks the open-breaker wording, since "unreachable" was false for that case (#11113).\n'
        '_server_errors_all_application: dict[Any, bool] = {}\n',
        '',
    ),
    (
        '    """Count a failure; at the threshold (re)stamp the breaker-open time. Keyed by the calling\n'
        "    scope's connection so one profile's failing server never opens another profile's breaker.\n"
        '    *application*: the call completed and the payload was an error (transport is fine)."""\n'
        '    from tools.mcp_tool_scope import _resolve_server_key\n',
        '    """Count a transport failure; application responses reset stale transport failures.\n'
        "    Keyed by the calling scope's connection so one profile's failing server never opens\n"
        '    another profile\'s breaker."""\n'
        '    if application:\n'
        '        _reset_server_error(server_name)\n'
        '        return\n'
        '    from tools.mcp_tool_scope import _resolve_server_key\n',
    ),
    (
        '    _server_errors_all_application[key] = application and (n == 1 or _server_errors_all_application.get(key, False))\n',
        '',
    ),
    (
        '    _server_errors_all_application.pop(key, None)\n',
        '',
    ),
)

MCP_HANDLER_EDITS = (
    (
        '    if _core._server_errors_all_application.get(key):\n'
        '        # The server answered every time; the calls were rejected. Calling it "unreachable" sent the\n'
        '        # model to the user instead of to its own arguments (#11113).\n'
        '        return tool_error(f"MCP server \'{server_name}\' rejected the last {failures} calls (it is reachable; see the "\n'
        '                          f"error text those calls returned). Paused for ~{retry_in}s. Do NOT repeat the same call — "\n'
        '                          f"fix the arguments/URL/target or use a different approach.")\n',
        '',
    ),
)


def replace_once(source: str, old: str, new: str, description: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"unable to locate the upstream {description}")
    return source.replace(old, new, 1)


def prepare(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(PACKAGE_ANCHOR) != 1:
        raise RuntimeError("unable to locate the upstream runtime package list")
    if source.count(RUNTIME_STAGE_ANCHOR) != 1:
        raise RuntimeError("unable to locate the upstream runtime stage")

    prepared = source.replace(PACKAGE_ANCHOR, PREPARED_PACKAGES, 1)
    prepared = prepared.replace(
        RUNTIME_STAGE_ANCHOR,
        RUNTIME_STAGE_ANCHOR + BUILD_SHA_INSTRUCTIONS,
        1,
    )
    source_files = (
        (path.parent / "tools" / "mcp_tool.py", MCP_TOOL_EDITS, "MCP circuit breaker"),
        (path.parent / "tools" / "mcp_tool_handlers.py", MCP_HANDLER_EDITS, "MCP breaker handler"),
    )
    prepared_sources = []
    for source_path, edits, description in source_files:
        prepared_source = source_path.read_text(encoding="utf-8")
        for old, new in edits:
            prepared_source = replace_once(prepared_source, old, new, description)
        prepared_sources.append((source_path, prepared_source))

    path.write_text(prepared, encoding="utf-8")
    for source_path, prepared_source in prepared_sources:
        source_path.write_text(prepared_source, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfile", type=Path)
    args = parser.parse_args()
    try:
        prepare(args.dockerfile)
    except (OSError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
