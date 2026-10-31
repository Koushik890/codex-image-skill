"""CLIProxyAPI helpers: read config, talk to the local proxy, start/stop it,
install it on a new machine, and auto-update it from GitHub releases with
checksum verification and rollback.

Settings (env overrides):
  CPA_DIR      proxy folder holding cli-proxy-api.exe + config.yaml
  CPA_URL      proxy base URL
  CPA_API_KEY  client key for the proxy (default: first key in config.yaml)
"""

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

WINDOWS = os.name == "nt"
# Installs made before `setup` existed live here; keep using them in place.
LEGACY_DIR = Path(r"D:\Trading Bot\tools\CLIProxyAPI")
DEFAULT_DIR = Path.home() / ".codex-image" / "CLIProxyAPI"
CPA_DIR = Path(os.environ.get("CPA_DIR") or (LEGACY_DIR if LEGACY_DIR.exists() else DEFAULT_DIR))
CPA_URL = os.environ.get("CPA_URL", "http://127.0.0.1:8317").rstrip("/")
EXE_NAME = "cli-proxy-api.exe" if WINDOWS else "cli-proxy-api"
REPO = "router-for-me/CLIProxyAPI"
CHECK_INTERVAL_S = 24 * 3600
KEEP_EXE_BACKUPS = 2
AUTH_DIR = Path.home() / ".cli-proxy-api"

SKILL_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = SKILL_DIR / ".state.json"
LOCK_FILE = SKILL_DIR / ".update.lock"


def log(msg):
    print(f"[codex-image] {msg}", file=sys.stderr, flush=True)


# ---------- config / versions ----------

def api_key():
    if os.environ.get("CPA_API_KEY"):
        return os.environ["CPA_API_KEY"]
    lines = (CPA_DIR / "config.yaml").read_text(encoding="utf-8-sig").splitlines()
    # Legacy layout: root `api-keys:` list. v8 layout: `access.api-keys:` list.
    # v8 also has a root `api-keys:` mapping of upstream providers; skip any
    # `api-keys:` block whose first entry is not a scalar list item.
    for i, line in enumerate(lines):
        if not re.match(r"^\s*api-keys:\s*$", line):
            continue
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r"^-\s*[\"']?([^\"'#\s]+)[\"']?\s*(#.*)?$", s)
            if m:
                return m.group(1)
            break
    raise RuntimeError(f"no client api-keys entry found in {CPA_DIR / 'config.yaml'}")


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def exe_version(exe):
    out = subprocess.run([str(exe), "-h"], capture_output=True, text=True, timeout=30)
    m = re.search(r"CLIProxyAPI Version:\s*([\d.]+)", out.stdout + out.stderr)
    return m.group(1) if m else None


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------- HTTP ----------

def http_json(url, data=None, headers=None, timeout=30):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def proxy_models(timeout=10):
    data = http_json(f"{CPA_URL}/v1/models",
                     headers={"Authorization": f"Bearer {api_key()}"}, timeout=timeout)
    return [m.get("id", "") for m in data.get("data", [])]


def healthy(timeout=10):
    try:
        return any(m.startswith("gpt-image") for m in proxy_models(timeout))
    except Exception:
        return False


def wait_healthy(seconds=45):
    end = time.time() + seconds
    while time.time() < end:
        if healthy(5):
            return True
        time.sleep(1.5)
    return False


# ---------- process control ----------

def _ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=60)


def stop_proxy():
    if not WINDOWS:
        exe = str(CPA_DIR / EXE_NAME)
        subprocess.run(["pkill", "-f", exe])
        for _ in range(20):
            if subprocess.run(["pgrep", "-f", exe], capture_output=True).returncode:
                return
            time.sleep(0.5)
        raise RuntimeError("proxy process did not stop")
    exe = str(CPA_DIR / EXE_NAME).replace("'", "''")
    _ps("Get-Process -Name 'cli-proxy-api' -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.Path -eq '{exe}' -or $_.Path -like '{exe}*' }} | Stop-Process -Force")
    for _ in range(20):
        r = _ps("(Get-Process -Name 'cli-proxy-api' -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.Path -like '{exe}*' }}).Count")
        if r.stdout.strip() in ("", "0"):
            return
        time.sleep(0.5)
    raise RuntimeError("proxy process did not stop")


def start_proxy():
    """Start the proxy detached and hidden so it outlives this script."""
    out = open(CPA_DIR / "server-out.txt", "ab")
    err = open(CPA_DIR / "server-err.txt", "ab")
    args = [str(CPA_DIR / EXE_NAME), "-config", "config.yaml"]
    if not WINDOWS:
        subprocess.Popen(args, cwd=CPA_DIR, stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return
    base = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    breakaway = 0x01000000          # CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen(args, cwd=CPA_DIR, stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, creationflags=base | breakaway, close_fds=True)
    except OSError:
        subprocess.Popen(args, cwd=CPA_DIR, stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, creationflags=base, close_fds=True)


def ensure_running():
    if healthy(5):
        return
    if not (CPA_DIR / EXE_NAME).exists() or not (CPA_DIR / "config.yaml").exists():
        raise SystemExit(f"CLIProxyAPI is not set up in {CPA_DIR}. Run: python codex_image.py setup")
    log("proxy not answering; starting it")
    start_proxy()
    if not wait_healthy():
        raise RuntimeError(f"proxy did not come up on {CPA_URL}; see {CPA_DIR / 'server-err.txt'}")


# ---------- setup ----------

def make_config(example):
    """config.yaml text from the release example: loopback only, one random key."""
    key = secrets.token_urlsafe(32)
    text, hosts = re.subn(r'(?m)^  host: ""$', '  host: "127.0.0.1"', example, count=1)
    text, keys = re.subn(r'(?m)^  api-keys:\n(?:    - "your-api-key-\d+"\n)+',
                         f'  api-keys:\n    - "{key}"\n', text, count=1)
    if not hosts or not keys:
        # Never fall through: the example's default host binds every interface.
        raise RuntimeError("config.example.yaml layout changed; write config.yaml by hand")
    return text


def setup():
    """Install the proxy, write its config and log in to Codex. Skips any step
    already done, so it is safe to re-run."""
    CPA_DIR.mkdir(parents=True, exist_ok=True)
    exe = CPA_DIR / EXE_NAME
    if not exe.exists():
        with tempfile.TemporaryDirectory(prefix="cpa-setup-") as tmp:
            tmp = Path(tmp)
            shutil.copy2(fetch_release(latest_release(), tmp), exe)
            shutil.copy2(tmp / "config.example.yaml", CPA_DIR / "config.example.yaml")

    config = CPA_DIR / "config.yaml"
    if not config.exists():
        text = make_config((CPA_DIR / "config.example.yaml").read_text(encoding="utf-8"))
        with os.fdopen(os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                       "w", encoding="utf-8") as f:
            f.write(text)

    if not list(AUTH_DIR.glob("codex-*.json")):
        log("log in: open the URL below and enter the code")
        subprocess.run([str(exe), "-config", "config.yaml", "-codex-device-login"],
                       cwd=CPA_DIR, check=True)

    ensure_running()
    return {"proxy_dir": str(CPA_DIR), "version": exe_version(exe),
            "image_models": [m for m in proxy_models() if m.startswith("gpt-image")]}


# ---------- update ----------

def latest_release():
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "codex-image-skill"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "codex-image-skill"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def asset_suffix():
    """Release asset ending for this machine. The full `_os_arch.ext` match
    skips the `_no-plugin` builds."""
    osname = {"win32": "windows", "darwin": "darwin", "linux": "linux"}.get(sys.platform)
    arch = {"x86_64": "amd64", "amd64": "amd64",
            "arm64": "aarch64", "aarch64": "aarch64"}.get(platform.machine().lower())
    if not osname or not arch:
        raise RuntimeError(f"no CLIProxyAPI build for {sys.platform}/{platform.machine()}")
    return f"_{osname}_{arch}." + ("zip" if WINDOWS else "tar.gz")


def _extract(archive, names, dest):
    """Copy the named top-level files out of a release archive into dest.
    Writes by base name, so a hostile member path cannot escape dest."""
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = Path(info.filename).name
                if base in names:
                    (dest / base).write_bytes(z.read(info))
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                base = Path(member.name).name
                if base in names and member.isfile():
                    (dest / base).write_bytes(t.extractfile(member).read())


def fetch_release(rel, tmp):
    """Download and checksum-verify a release into tmp; return the new binary."""
    tag = rel["tag_name"]
    suffix = asset_suffix()
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    archive = next((n for n in assets if n.endswith(suffix)), None)
    if not archive or "checksums.txt" not in assets:
        raise RuntimeError(f"release {tag} has no {suffix} asset or checksums.txt")

    apath, cpath = tmp / archive, tmp / "checksums.txt"
    log(f"downloading {archive}")
    _download(assets[archive], apath)
    _download(assets["checksums.txt"], cpath)

    want = next((ln.split()[0] for ln in cpath.read_text().splitlines()
                 if ln.strip().endswith(archive)), None)
    got = hashlib.sha256(apath.read_bytes()).hexdigest()
    if not want or want.lower() != got:
        raise RuntimeError(f"checksum mismatch for {archive}")

    _extract(apath, {EXE_NAME, "config.example.yaml"}, tmp)
    new_exe = tmp / EXE_NAME
    new_exe.chmod(0o755)
    if exe_version(new_exe) != tag.lstrip("v"):
        raise RuntimeError(f"downloaded binary does not report version {tag.lstrip('v')}")
    return new_exe


def _acquire_lock():
    try:
        if LOCK_FILE.exists() and time.time() - LOCK_FILE.stat().st_mtime > 900:
            LOCK_FILE.unlink()  # stale lock from a crashed run
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _prune_backups():
    baks = sorted(CPA_DIR.glob(EXE_NAME + ".bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in baks[KEEP_EXE_BACKUPS:]:
        try:
            p.unlink()
        except OSError:
            pass


def install_release(rel, current):
    """Download, verify, swap, restart, health-check; roll back on failure."""
    new_ver = rel["tag_name"].lstrip("v")

    with tempfile.TemporaryDirectory(prefix="cpa-update-") as tmp:
        tmp = Path(tmp)
        new_exe = fetch_release(rel, tmp)

        exe = CPA_DIR / EXE_NAME
        exe_bak = CPA_DIR / f"{EXE_NAME}.bak-{current}"
        cfg_bak = CPA_DIR / f"config.yaml.bak-{current}"
        shutil.copy2(CPA_DIR / "config.yaml", cfg_bak)

        stop_proxy()
        try:
            if exe_bak.exists():
                exe_bak.unlink()
            exe.rename(exe_bak)
            shutil.copy2(new_exe, exe)
        except OSError:
            # Swap failed midway: put the old binary back and bring it up.
            if exe_bak.exists() and not exe.exists():
                exe_bak.rename(exe)
            start_proxy()
            raise
        if (tmp / "config.example.yaml").exists():
            shutil.copy2(tmp / "config.example.yaml", CPA_DIR / "config.example.yaml")
        start_proxy()

        if wait_healthy():
            _prune_backups()
            return True

        log(f"v{new_ver} failed health check; rolling back to v{current}")
        stop_proxy()
        exe.unlink()
        exe_bak.rename(exe)
        shutil.copy2(cfg_bak, CPA_DIR / "config.yaml")
        start_proxy()
        if not wait_healthy():
            raise RuntimeError(f"rollback to v{current} also failed; check {CPA_DIR}")
        return False


def auto_update(force=False):
    """Update the proxy if GitHub has a newer release. Never raises: an update
    problem must not block image generation. Returns a result dict or None."""
    state = load_state()
    if not force and time.time() - state.get("last_check", 0) < CHECK_INTERVAL_S:
        return None
    if not _acquire_lock():
        log("another update is in progress; skipping")
        return None
    try:
        current = exe_version(CPA_DIR / EXE_NAME)
        rel = latest_release()
        state.update(last_check=time.time(), latest=rel["tag_name"], installed=current)
        save_state(state)
        if not current or vtuple(rel["tag_name"]) <= vtuple(current):
            return {"status": "up-to-date", "version": current}
        log(f"updating CLIProxyAPI v{current} -> {rel['tag_name']}")
        ok = install_release(rel, current)
        installed = rel["tag_name"].lstrip("v") if ok else current
        state.update(installed=installed)
        save_state(state)
        return {"status": "updated" if ok else "rolled-back",
                "from": current, "to": rel["tag_name"].lstrip("v"), "version": installed}
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"update check skipped (network): {e}")
        return {"status": "check-failed", "error": str(e)}
    except Exception as e:
        log(f"update failed: {e}")
        return {"status": "failed", "error": str(e)}
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
