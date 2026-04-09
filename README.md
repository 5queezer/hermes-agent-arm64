# Hermes Agent ARM64

ARM64 Docker build of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) for deployment on ARM64 servers (e.g. Hetzner CAX, Raspberry Pi, AWS Graviton).

The upstream image on Docker Hub is amd64-only. This repo builds a native ARM64 image via GitHub Actions.

## Image

```
ghcr.io/5queezer/hermes-agent-arm64:latest
```

## Auto-update

A GitHub Actions workflow runs daily at 06:00 UTC:

1. Checks upstream `NousResearch/hermes-agent` HEAD for changes
2. Builds ARM64 image on a native `ubuntu-24.04-arm` runner (~6 min)
3. Pushes to GHCR
4. Triggers Coolify redeploy via API

Manual trigger: `gh workflow run build.yml`

## Included extras

Only the extras needed for a Telegram gateway deployment:

`messaging`, `cron`, `cli`, `pty`, `mcp`, `voice`

The full `[all]` extras hit pip's `resolution-too-deep` limit on ARM64. Install additional extras manually inside the container if needed.

## Configuration

The image starts in **gateway mode** (`CMD ["gateway"]`). Set these environment variables:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ALLOWED_USERS` | yes | Comma-separated numeric Telegram user IDs |
| `HERMES_HOME` | no | Data directory (default: `/opt/data`) |
| `HERMES_INFERENCE_PROVIDER` | no | LLM provider (e.g. `openai-codex`) |

For OpenAI Codex, run the OAuth login interactively after first deploy:

```bash
docker exec -it <container> hermes model --no-browser
```

## Persistent data

Mount a volume at `/opt/data` for config, sessions, skills, and credentials.
