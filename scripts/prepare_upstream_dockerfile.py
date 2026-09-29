#!/usr/bin/env python3
"""Prepare the upstream Hermes Dockerfile for this deployment environment."""

import argparse
import shlex
from pathlib import Path

PACKAGE_ANCHOR = "ca-certificates curl iputils-ping"
PREPARED_PACKAGES = "ca-certificates curl sudo iputils-ping"

# Keep the persistent deployment verifiers compatible with upstream's new
# metadata format, and reject unversioned images before they can be published.
REVISION_COMMAND = (
    "import json, os, re; from pathlib import Path; "
    'sha = os.environ["HERMES_GIT_SHA"]; '
    'assert re.fullmatch(r"[0-9a-f]{40}", sha) and sha != "0" * 40, "invalid build SHA"; '
    'assert json.loads(Path("/opt/hermes/install-stamp.json").read_text())["commit"] == sha, "install stamp mismatch"; '
    'assert json.loads(Path("/etc/hermes/image-provenance.json").read_text())["revision"] == sha, "image provenance mismatch"; '
    'Path("/opt/hermes/.hermes_build_sha").write_text(sha + "\\n")'
)


def prepare(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(PACKAGE_ANCHOR) != 1:
        raise RuntimeError("unable to locate the upstream runtime package list")
    path.write_text(
        source.replace(PACKAGE_ANCHOR, PREPARED_PACKAGES, 1).rstrip()
        + "\n\n# Preserve the production deployment verifier contract.\n"
        + "ARG HERMES_GIT_SHA\n"
        + f"RUN python3 -I -c {shlex.quote(REVISION_COMMAND)}\n",
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
