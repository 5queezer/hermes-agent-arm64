#!/usr/bin/env python3
"""Prepare the upstream Hermes Dockerfile for this deployment environment."""

import argparse
from pathlib import Path

PACKAGE_ANCHOR = "ca-certificates curl iputils-ping"
PREPARED_PACKAGES = "ca-certificates curl sudo iputils-ping"


def prepare(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(PACKAGE_ANCHOR) != 1:
        raise RuntimeError("unable to locate the upstream runtime package list")
    path.write_text(
        source.replace(PACKAGE_ANCHOR, PREPARED_PACKAGES, 1),
        encoding="utf-8",
    )


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
