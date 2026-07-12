# Hermes Agent ARM64

Production ARM64 builds of
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) for
the two Hermes Telegram agents hosted on the Hetzner ARM64 server.

## Image

```text
ghcr.io/5queezer/hermes-agent-arm64:<upstream-git-sha>
```

`latest` is also published, but deployments use the immutable 40-character
upstream Git SHA tag.

## Build and deployment

`.github/workflows/build.yml` runs daily at 06:00 UTC, on pushes to `master`,
and on manual dispatch. It:

1. resolves the exact `NousResearch/hermes-agent` HEAD commit;
2. checks out that commit and builds its upstream Dockerfile on a native ARM64
   runner;
3. passes `HERMES_GIT_SHA` so the image reports its source revision;
4. pushes both the SHA tag and `latest` to GHCR;
5. updates the Coolify applications for `@hermes_chee6Law_bot` and
   `@raspi_kerf_bot` to the exact SHA tag;
6. waits for both asynchronous deployments and their in-container verification
   commands to finish;
7. rolls back already-updated applications if a later deployment fails; and
8. records `.last-upstream-sha` only after both agents are verified.

A green workflow therefore means both gateway deployments completed. An
accepted Coolify API request alone is not considered success.

Manual forced deployment:

```bash
gh workflow run build.yml -f force=true
```

## Required GitHub Actions secrets

| Secret | Purpose |
|---|---|
| `COOLIFY_URL` | Public Coolify base URL |
| `COOLIFY_TOKEN` | API token with read, write, and deploy permissions |
| `COOLIFY_APP_UUID` | Coolify application for `@hermes_chee6Law_bot` |
| `COOLIFY_CAREER_APP_UUID` | Coolify application for `@raspi_kerf_bot` |

`GITHUB_TOKEN` is supplied automatically and is used to publish to GHCR and
record the successfully deployed upstream SHA.

## Deployment verification

`scripts/verify_hermes_deployment.py` runs inside each newly deployed gateway
and checks:

- the baked source SHA matches the requested deployment;
- the Hermes gateway process is running;
- OpenAI Codex authentication is logged in; and
- Telegram `getMe` returns the expected bot username.

The verifier never prints tokens. `scripts/deploy_coolify.py` also avoids
printing Coolify response bodies because deployment logs can contain secret
environment values.

## Persistent data

Each Coolify application mounts its existing persistent data at `/opt/data`.
This preserves configuration, sessions, skills, and OAuth credentials across
immutable image deployments and rollbacks.
