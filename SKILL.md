---
name: codex-image
description: Generate or edit images with OpenAI's newest gpt-image model (gpt-image-2.5 or later) through the user's local CLIProxyAPI, using their ChatGPT/Codex subscription instead of paid API credits. Use when the user asks to generate, create, draw, design or edit an image, icon, logo, illustration, banner, hero image, mockup, sprite or texture, or to change or restyle an existing image.
---

# Codex image generation

Images come from `gpt-image-*` models through CLIProxyAPI on `http://127.0.0.1:8317`. The proxy forwards each request to Codex using the user's ChatGPT Plus login. The script is `scripts/codex_image.py` in this skill's folder, and it needs only the Python standard library.

## First run

If the script says the proxy is not set up, run `python ~/.claude/skills/codex-image/scripts/codex_image.py setup` in a terminal the user can see. It installs the proxy, then waits for the user to enter a device code at OpenAI, which you cannot do for them. It is safe to re-run.

## Generate

```bash
python ~/.claude/skills/codex-image/scripts/codex_image.py generate "PROMPT" --out <folder> [options]
```

Run it with a Bash timeout of 600000. One image usually takes 20 to 90 seconds, and high quality or large sizes take longer.

Options:
- `--size 1024x1024 | 1536x1024 | 1024x1536 | auto` (default `auto`)
- `--quality low | medium | high | auto` (default `high`). Use `low` for quick drafts.
- `--background transparent` for icons, logos and sprites. Pair it with `--output-format png`.
- `--output-format png | jpeg | webp`
- `--n 2` to get several variants
- `--name hero-banner` sets the file name stem. The default is a slug of the prompt plus a timestamp.
- `--model gpt-image-2.5-flare` picks a model by name. By default the script picks the newest plain `gpt-image-N`.
- `--no-update` skips the proxy update check.

## Edit or use reference images

Add one or more `--image path` flags to switch to edit mode. Add `--mask mask.png` to limit the edit to one area; transparent pixels in the mask mark the area to change.

```bash
python ~/.claude/skills/codex-image/scripts/codex_image.py generate "make the sky a sunset" --image photo.png --out generated-images
```

## Workflow

1. Write a concrete prompt covering the subject, style, composition, colours, any text to render and the aspect ratio. For project assets, match the project's existing look.
2. Choose `--out`. Use the project's asset folder when one exists, such as `public/images` or `assets/`, and `generated-images/` otherwise.
3. Run the script. It prints JSON with `files`, `model`, `seconds`, `proxy_version` and `proxy_update`.
4. **Read every output file** so you see the image yourself before reporting. If it misses the brief, refine the prompt and regenerate, or edit it with `--image`.
5. Tell the user the file paths and the model used. If `proxy_update.status` is `updated`, also tell them the proxy was upgraded (from X to Y). If it is `rolled-back` or `failed`, say the upgrade was attempted, failed, and the old version is still running.

## Proxy auto-update (automatic, no confirmation needed)

The user asked for this to happen without being asked. At most once a day, before generating, the script does the following:

1. It compares the local proxy with the latest GitHub release of `router-for-me/CLIProxyAPI`.
2. If a newer release exists, it downloads the build for this OS and CPU and verifies its SHA256 against the release's `checksums.txt`.
3. It backs up `cli-proxy-api.exe` and `config.yaml` as `*.bak-<oldversion>`, swaps in the new binary and restarts the proxy hidden in the background.
4. It then runs a health check. If the check fails, it rolls back to the backups automatically.

Commands:
- `python .../codex_image.py update` forces the check now.
- `python .../codex_image.py models` lists the image models the proxy offers and shows which one is the default.

Settings are environment variables:
- `CPA_DIR` (default `~/.codex-image/CLIProxyAPI`, or `D:\Trading Bot\tools\CLIProxyAPI` where that folder exists)
- `CPA_URL` (default `http://127.0.0.1:8317`)
- `CPA_API_KEY` (default is the first client key in the proxy's `config.yaml`)

## Rules

- Never print, echo or paste the proxy API key. The script reads it itself.
- Never change the proxy's `host` away from `127.0.0.1`. Exposing it would share the user's ChatGPT account.
- Each image uses the user's ChatGPT Plus quota. Don't generate large batches unless asked.
- On `HTTP 429` or a quota error, tell the user the Codex image quota is used up and when to retry. Don't loop retries.
