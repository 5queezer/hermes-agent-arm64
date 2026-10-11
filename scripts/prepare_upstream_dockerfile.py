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
    path.write_text(prepared, encoding="utf-8")


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
