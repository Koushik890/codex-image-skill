# codex-image

A Claude Code skill that generates and edits images with OpenAI `gpt-image-*` models, using your ChatGPT subscription instead of paid API credits. Requests go through a local [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) that runs on `127.0.0.1`.

## Requirements

- [Claude Code](https://claude.com/claude-code), git and Python 3.9 or newer
- A ChatGPT plan that includes Codex
- Windows, macOS or Linux, on x86-64 or ARM64

## Install

The repo is private, so sign in first with `gh auth login` on the new machine.

```bash
git clone https://github.com/Koushik890/codex-image-skill ~/.claude/skills/codex-image
```

```bash
python ~/.claude/skills/codex-image/scripts/codex_image.py setup
```

On macOS and Linux, use `python3` if `python` isn't available.

`setup` downloads the latest proxy for your system and checks its SHA256 against the release's `checksums.txt`. It then writes a `config.yaml` that listens on `127.0.0.1` only, with a new random client key, and starts a device-code login. Open the URL it prints, sign in and enter the code. Steps that are already done are skipped, so you can run it again safely.

Restart Claude Code, then ask for an image or run `/codex-image`.

## Update

```bash
git -C ~/.claude/skills/codex-image pull
```

The proxy updates itself at most once a day, and rolls back if the new version fails a health check.

## Files kept outside this repo

Nothing here contains credentials. Each machine keeps its own:

| Path | Contents |
| --- | --- |
| `~/.codex-image/CLIProxyAPI/` | Proxy binary and `config.yaml`, which holds the client key |
| `~/.cli-proxy-api/` | ChatGPT login token |

Treat both as secrets. Set `CPA_DIR` to keep the proxy somewhere else.

## Tests

```bash
python ~/.claude/skills/codex-image/scripts/test_cpa.py
```

Command options and model selection are covered in [SKILL.md](SKILL.md).
