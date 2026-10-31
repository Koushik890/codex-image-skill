"""Generate or edit images with OpenAI gpt-image models through the local
CLIProxyAPI (Codex / ChatGPT OAuth). Stdlib only.

  python codex_image.py generate "prompt" [--image ref.png ...] [options]
  python codex_image.py setup
  python codex_image.py models
  python codex_image.py update

Before each generate the proxy is auto-updated (at most once a day) and started
if it is not running. Prints one JSON object on stdout; progress goes to stderr.
"""

import argparse
import base64
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cpa  # noqa: E402

FALLBACK_MODEL = "gpt-image-2.5"
IMAGE_MODEL_RE = re.compile(r"^gpt-image-(\d+(?:\.\d+)*)$")


def pick_model(models):
    """Newest plain gpt-image-N[.M] the proxy offers; variants like -flare
    are only used when asked for by name."""
    best = None
    for m in models:
        match = IMAGE_MODEL_RE.match(m)
        if match and (best is None or cpa.vtuple(match.group(1)) > cpa.vtuple(best[1])):
            best = (m, match.group(1))
    return best[0] if best else FALLBACK_MODEL


def data_url(path):
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def slug(text, words=6):
    s = re.sub(r"[^a-z0-9]+", "-", " ".join(text.lower().split()[:words])).strip("-")
    return s[:50] or "image"


def generate(args):
    update = None if args.no_update else cpa.auto_update()
    cpa.ensure_running()
    models = cpa.proxy_models()
    model = args.model or pick_model(models)

    body = {"model": model, "prompt": args.prompt, "n": args.n}
    for key in ("size", "quality", "background", "output_format"):
        val = getattr(args, key)
        if val:
            body[key] = val
    if args.image:
        endpoint = "/v1/images/edits"
        body["images"] = [{"image_url": data_url(p)} for p in args.image]
        if args.mask:
            body["mask"] = {"image_url": data_url(args.mask)}
    else:
        endpoint = "/v1/images/generations"

    cpa.log(f"{model} via {endpoint} (usually 20-90s)")
    t0 = time.time()
    try:
        resp = cpa.http_json(cpa.CPA_URL + endpoint, data=body,
                             headers={"Authorization": f"Bearer {cpa.api_key()}"},
                             timeout=args.timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise SystemExit(json.dumps({"error": f"HTTP {e.code}", "detail": detail, "model": model}))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = resp.get("output_format") or args.output_format or "png"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = args.name or slug(args.prompt)
    files = []
    for i, item in enumerate(resp.get("data", []), 1):
        suffix = f"-{i}" if len(resp["data"]) > 1 else ""
        path = out_dir / f"{name}-{stamp}{suffix}.{ext}"
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            urllib.request.urlretrieve(item["url"], path)
        else:
            continue
        files.append(str(path.resolve()))

    result = {
        "model": model,
        "files": files,
        "size": resp.get("size"),
        "seconds": round(time.time() - t0, 1),
        "proxy_version": cpa.exe_version(cpa.CPA_DIR / cpa.EXE_NAME),
        "proxy_update": update,
        "revised_prompt": next((d.get("revised_prompt") for d in resp.get("data", [])
                                if d.get("revised_prompt")), None),
    }
    if not files:
        result["error"] = "no image in response"
        result["raw"] = {k: v for k, v in resp.items() if k != "data"}
    print(json.dumps(result, indent=2))
    return 0 if files else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate, or edit when --image is given")
    g.add_argument("prompt")
    g.add_argument("--image", action="append", help="reference/input image (repeatable) -> edit mode")
    g.add_argument("--mask", help="PNG mask for edit mode (transparent = area to change)")
    g.add_argument("--model", help="default: newest gpt-image-N the proxy offers")
    g.add_argument("--size", default="auto", help="1024x1024 | 1536x1024 | 1024x1536 | auto")
    g.add_argument("--quality", default="high", help="low | medium | high | auto")
    g.add_argument("--background", help="transparent | opaque | auto")
    g.add_argument("--output-format", dest="output_format", help="png | jpeg | webp")
    g.add_argument("--n", type=int, default=1)
    g.add_argument("--out", default="generated-images", help="output folder")
    g.add_argument("--name", help="file name stem (default: slug of prompt)")
    g.add_argument("--timeout", type=int, default=600)
    g.add_argument("--no-update", action="store_true", help="skip the proxy update check")

    sub.add_parser("setup", help="install the proxy, write its config and log in to Codex")
    sub.add_parser("models", help="list image models the proxy offers and the default pick")
    u = sub.add_parser("update", help="check GitHub now and update the proxy if newer")
    u.add_argument("--force", action="store_true", default=True, help=argparse.SUPPRESS)

    args = ap.parse_args()
    if args.cmd == "generate":
        return generate(args)
    if args.cmd == "setup":
        print(json.dumps(cpa.setup(), indent=2))
        return 0
    if args.cmd == "models":
        cpa.ensure_running()
        models = cpa.proxy_models()
        print(json.dumps({"image_models": [m for m in models if "image" in m],
                          "default": pick_model(models)}, indent=2))
        return 0
    if args.cmd == "update":
        res = cpa.auto_update(force=True)
        print(json.dumps(res, indent=2))
        return 0 if res and res.get("status") in ("up-to-date", "updated") else 1


if __name__ == "__main__":
    sys.exit(main())
