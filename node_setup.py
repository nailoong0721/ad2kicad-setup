"""Detect, install and configure an isolated, non-administrator Node.js runtime.

Only official Windows ZIPs are used, verified against the matching official
SHA-256 list. Installation never replaces a nonempty directory or changes the
system PATH. Existing runtimes and user npm configuration are not modified.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import time
import urllib.request
import uuid
import zipfile

NODE_INDEX = "https://nodejs.org/dist/index.json"
NODE_DIST = "https://nodejs.org/dist/"
# Official schedule: https://github.com/nodejs/Release/blob/main/schedule.json
# Local inspection also works offline. Future LTS builds identify themselves
# through process.release.lts; known releases are rejected after their EOL date.
LTS_END = {22: "2027-04-30", 24: "2028-04-30", 26: "2029-04-30"}
MIN_NODE_MAJOR = 22
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
NETWORK_TIMEOUT = 30
METADATA_TIMEOUT = 90
DOWNLOAD_TIMEOUT = 600
PROBE_TIMEOUT = 20
PUBLISH_TIMEOUT = 5.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(executable: Path, arguments: list[str], *, cwd: Path | None = None,
         env: dict | None = None, timeout: int = PROBE_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run([str(executable), *arguments], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _host_arch() -> str:
    if platform.system() != "Windows":
        raise RuntimeError("This installer requires Windows 10/Server 2016 or newer")
    version = platform.version().split(".")
    if not version[0].isdigit() or int(version[0]) < 10:
        raise RuntimeError("Node.js LTS requires Windows 10/Server 2016 or newer")
    machine = (os.environ.get("PROCESSOR_ARCHITEW6432") or
               os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()).lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    raise RuntimeError("Node.js LTS installation requires 64-bit Windows (x64 or ARM64)")


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    return tuple(map(int, match.groups())) if match else None


def _supported_lts(version: str, lts) -> bool:
    parsed = _version_tuple(version)
    return bool(parsed and parsed[0] >= MIN_NODE_MAJOR and lts and
                (parsed[0] not in LTS_END or date.today() < date.fromisoformat(LTS_END[parsed[0]])))


def _node_executable(root: Path | None) -> Path | None:
    if root is None:
        return None
    root = Path(root)
    if root.is_file() and root.name.casefold() == "node.exe":
        return root
    return next((path for path in (root / "node.exe", root / "bin/node.exe") if path.is_file()), None)


def _npm_cli(node: Path) -> Path | None:
    candidate = node.parent / "node_modules/npm/bin/npm-cli.js"
    return candidate if candidate.is_file() else None


def _npm_executable(node: Path) -> Path | None:
    return next((path for path in (node.with_name("npm.cmd"), node.with_name("npm.exe"),
                                   node.parent / "node_modules/npm/bin/npm-cli.js")
                 if path.is_file()), None)


def find_node(root: Path | None = None) -> tuple[Path | None, str]:
    """Find a runtime, never substituting PATH for an explicitly selected path."""
    if root is not None:
        explicit = _node_executable(root)
        return (explicit.absolute() if explicit else None), "explicit"
    found = shutil.which("node.exe") or shutil.which("node")
    return (Path(found).absolute(), "path") if found else (None, "not_found")


def _probe_env() -> dict:
    # Inherited NODE_OPTIONS can load addons or make an otherwise healthy
    # executable fail. A health probe describes this runtime itself.
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ("NODE_OPTIONS", "NODE_PATH")}
    env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    return env


def inspect_node(root: Path | None = None, config_root: Path | None = None) -> dict:
    """Read-only, bounded health check of both node.exe and npm-cli.js."""
    node, source = find_node(root)
    selected = Path(root).absolute() if root is not None else None
    info = {"root": str(node.parent if node else selected) if node or selected else None,
            "config_root": str(Path(config_root).absolute()) if config_root is not None else None,
            "executable": str(node) if node else None, "npm": None, "npm_cli": None,
            "installed": False, "needs_directory": selected is None and node is None,
            "source": source, "version": None, "npm_version": None,
            "reason": "node.exe was not found in the selected directory"}
    if node is None:
        return info
    npm, cli = _npm_executable(node), _npm_cli(node)
    info.update(npm=str(npm) if npm else None, npm_cli=str(cli) if cli else None)
    try:
        host_arch = _host_arch()
        env = _probe_env()
        result = _run(node, ["-p", "JSON.stringify({version:process.version,platform:process.platform,arch:process.arch,lts:process.release.lts||false})"], env=env)
        if result.returncode:
            raise RuntimeError("node.exe could not start: " + (result.stderr.strip() or str(result.returncode))[:400])
        details = json.loads(result.stdout.strip())
        if not isinstance(details, dict):
            raise ValueError("node.exe returned invalid runtime details")
        info.update({key: details.get(key) for key in ("version", "platform", "arch", "lts")})
        if details.get("platform") != "win32" or details.get("arch") not in ("x64", "arm64"):
            raise RuntimeError("The selected runtime must be a 64-bit Windows Node.js build")
        if host_arch == "x64" and details["arch"] != "x64":
            raise RuntimeError("The selected Node.js architecture does not match this Windows computer")
        if not _supported_lts(details.get("version", ""), details.get("lts")):
            raise RuntimeError("Select a supported Node.js LTS release (22 or newer); current/non-LTS and end-of-life releases are not reused")
        if npm is None or cli is None:
            raise RuntimeError("Node.js is present but its bundled npm-cli.js is missing")
        result = _run(node, [str(cli), "--version"], env=env, cwd=node.parent)
        npm_version = result.stdout.strip()
        if result.returncode or not _version_tuple(npm_version):
            raise RuntimeError("Bundled npm failed its version check: " + (result.stderr.strip() or npm_version)[:400])
        info.update(installed=True, needs_directory=False, npm_version=npm_version, reason="Node.js LTS and npm passed their health checks")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        info["reason"] = str(exc)
    return info


def _read_official(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AD2KiCad-Setup"})
    started, chunks, received = time.monotonic(), [], 0
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
        # read1 returns available data without waiting to fill the whole buffer,
        # so even a slowly trickling response is covered by the total deadline.
        read_chunk = getattr(response, "read1", response.read)
        while chunk := read_chunk(64 * 1024):
            received += len(chunk)
            if time.monotonic() - started > METADATA_TIMEOUT:
                raise RuntimeError("Official Node.js metadata exceeded its 90-second timeout")
            if received > limit:
                raise RuntimeError("Official Node.js metadata exceeds its size limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _latest_release() -> tuple[str, str, str]:
    arch = "win-" + _host_arch()
    releases = json.loads(_read_official(NODE_INDEX, 4 * 1024 * 1024))
    if not isinstance(releases, list):
        raise RuntimeError("Official Node.js release index is invalid")
    eligible = [item for item in releases if isinstance(item, dict)
                and _supported_lts(item.get("version", ""), item.get("lts"))
                and arch + "-zip" in item.get("files", [])]
    if not eligible:
        raise RuntimeError("Official Node.js index contains no supported LTS Windows ZIP for this computer")
    # Security releases are also LTS releases. Never exclude their fixes.
    latest = max(eligible, key=lambda item: _version_tuple(item["version"]))
    version = latest["version"]
    return version, f"node-{version}-{arch}.zip", NODE_DIST + version + "/"


def _download(url: str, target: Path, expected_size: int | None = None) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "AD2KiCad-Setup"})
    partial = target.with_name(target.name + ".partial-" + uuid.uuid4().hex)
    started, received = time.monotonic(), 0
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response, partial.open("xb") as destination:
            declared = response.headers.get("Content-Length")
            declared_size = int(declared) if declared and declared.isdigit() else None
            size = expected_size if expected_size is not None else declared_size
            if size is not None and (size <= 0 or size > MAX_ARCHIVE_BYTES):
                raise RuntimeError("Node.js archive has an invalid declared size")
            read_chunk = getattr(response, "read1", response.read)
            while chunk := read_chunk(1024 * 1024):
                received += len(chunk)
                if time.monotonic() - started > DOWNLOAD_TIMEOUT:
                    raise RuntimeError("Node.js archive download exceeded its 10-minute timeout")
                if received > MAX_ARCHIVE_BYTES or (size is not None and received > size):
                    raise RuntimeError("Node.js archive exceeds its declared size")
                destination.write(chunk)
            if not received or (size is not None and received != size):
                raise RuntimeError("Node.js archive download is incomplete")
        if target.exists():
            raise RuntimeError(f"Download destination already exists: {target}")
        partial.rename(target)
        return target
    finally:
        if partial.exists():
            partial.unlink()


def _assert_plain_path(path: Path) -> None:
    """Refuse reparse points anywhere in a path that setup will write into."""
    path = path.absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.stat(), "st_file_attributes", 0) & 0x400):
            raise RuntimeError(f"Setup directory must not contain a link or reparse point: {item}")


def _official_archive(version: str, archive: str, base: str, cache: Path) -> Path:
    expected_name = rf"node-{re.escape(version)}-win-(?:x64|arm64)\.zip"
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version) or not re.fullmatch(expected_name, archive) or base != NODE_DIST + version + "/":
        raise RuntimeError("Refusing a non-official Node.js archive location")
    _assert_plain_path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / archive
    checksums = _read_official(base + "SHASUMS256.txt", 512 * 1024).decode("utf-8")
    match = re.search(r"^([0-9a-fA-F]{64})\s+\*?" + re.escape(archive) + r"\s*$", checksums, re.M)
    if not match:
        raise RuntimeError(f"Official Node.js checksum is missing for {archive}")
    expected = match.group(1).lower()
    _assert_plain_path(target)
    if target.is_file() and _sha256(target) == expected:
        return target
    # Preserve any mismatched pre-existing cache file, including user assets.
    if target.exists():
        fresh_cache = cache / ("download-" + uuid.uuid4().hex)
        fresh_cache.mkdir()
        target = fresh_cache / archive
    _download(base + archive, target)
    if _sha256(target) != expected:
        target.unlink()  # This attempt's freshly downloaded file only.
        raise RuntimeError("Node.js archive SHA-256 mismatch; installation was not changed")
    return target


def _require_empty_destination(destination: Path) -> None:
    _assert_plain_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise RuntimeError(f"Node.js directory is not empty. Existing files were preserved; select a new empty installation directory: {destination}")


def _publish_verified_stage(stage: Path, destination: Path) -> None:
    """Allow brief Windows executable/antivirus locks without replacing files."""
    deadline = time.monotonic() + PUBLISH_TIMEOUT
    delay = 0.1
    while True:
        # Recheck on every attempt: another process may create the destination
        # while the preceding rename is waiting for a transient lock to clear.
        _assert_plain_path(stage)
        _require_empty_destination(destination)
        if destination.exists():
            destination.rmdir()
        try:
            stage.rename(destination)
            return
        except PermissionError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 1.0)


def _extract(archive: Path, destination: Path, version: str) -> Path:
    destination = Path(destination).absolute()
    _require_empty_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / (".node-stage-" + uuid.uuid4().hex)
    stage.mkdir(exist_ok=False)
    verified = False
    try:
        with zipfile.ZipFile(archive) as bundle:
            expected_root = archive.name[:-4] if archive.suffix.casefold() == ".zip" else None
            if not expected_root or not expected_root.startswith(f"node-{version}-win-"):
                raise RuntimeError(f"Unexpected Node.js archive name: {archive.name}")
            total_size, seen, members = 0, set(), []
            for item in bundle.infolist():
                name = item.filename.replace("\\", "/")
                parts = name.rstrip("/").split("/")
                if not parts or parts[0] != expected_root:
                    raise RuntimeError(f"Unexpected Node.js archive member: {item.filename}")
                if (name.startswith("/") or any(part in ("", ".", "..") or ":" in part
                        or part.endswith((".", " ")) or any(ord(char) < 32 for char in part)
                        or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
                        for part in parts)):
                    raise RuntimeError(f"Unsafe Node.js archive member: {item.filename}")
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise RuntimeError(f"Node.js archive contains a symbolic link: {item.filename}")
                identity = "/".join(parts).casefold()
                if identity in seen:
                    raise RuntimeError(f"Duplicate Node.js archive member: {item.filename}")
                seen.add(identity)
                total_size += item.file_size
                if total_size > MAX_UNPACKED_BYTES or len(seen) > 20000:
                    raise RuntimeError("Node.js archive exceeds its unpacked size/file limit")
                members.append((item, Path(*parts[1:])))
            for item, relative in members:
                target = stage / relative
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as source, target.open("xb") as sink:
                        shutil.copyfileobj(source, sink)
        health = inspect_node(stage)
        if not health["installed"] or health.get("version") != version:
            raise RuntimeError("Downloaded Node.js failed its health check: " + health["reason"])
        verified = True
        # Publish only a verified runtime. A concurrent writer makes this fail
        # rather than lose files added to the destination during extraction.
        try:
            _publish_verified_stage(stage, destination)
        except Exception as exc:
            raise RuntimeError(f"Node.js could not publish its verified runtime to {destination}. "
                               f"The verified files are preserved at {stage}. Close applications using these files, "
                               f"allow any antivirus scan to finish, then retry with an empty installation directory. {exc}") from exc
    finally:
        if stage.exists() and not verified:
            shutil.rmtree(stage)
    return destination / "node.exe"


def _default_config_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "tools-config/nodejs"


def _configure_npm(config: Path) -> dict:
    _assert_plain_path(config)
    config.mkdir(parents=True, exist_ok=True)
    paths = {"NPM_CONFIG_USERCONFIG": config / "npmrc", "NPM_CONFIG_CACHE": config / "cache",
             "NPM_CONFIG_PREFIX": config / "prefix", "NPM_CONFIG_LOGS_DIR": config / "logs"}
    for key, path in paths.items():
        _assert_plain_path(path)
        if key != "NPM_CONFIG_USERCONFIG":
            path.mkdir(parents=True, exist_ok=True)
    npmrc = paths["NPM_CONFIG_USERCONFIG"]
    if not npmrc.exists():
        contents = "".join(name + "=" + json.dumps(str(paths[key]).replace("\\", "/"), ensure_ascii=False) + "\n"
                           for name, key in (("cache", "NPM_CONFIG_CACHE"), ("prefix", "NPM_CONFIG_PREFIX"),
                                             ("logs-dir", "NPM_CONFIG_LOGS_DIR")))
        with npmrc.open("x", encoding="utf-8", newline="\n") as sink:
            sink.write(contents + "fund=false\naudit=false\nupdate-notifier=false\n")
    elif not npmrc.is_file():
        raise RuntimeError(f"The selected npm config is not a file: {npmrc}")
    return {key: str(value) for key, value in paths.items()}


def ensure_node(root: Path | None, config_root: Path | None, output: Path) -> dict:
    """Reuse a healthy LTS runtime or atomically install into a selected empty path."""
    if root is None:
        raise RuntimeError("Select a Node.js installation directory before installing")
    root, output = Path(root).absolute(), Path(output).absolute()
    config = Path(config_root).absolute() if config_root is not None else _default_config_root().absolute()
    _assert_plain_path(output)
    _assert_plain_path(config)
    info = inspect_node(root, config)
    installed = False
    if not info["installed"]:
        _require_empty_destination(root)
        version, archive, base = _latest_release()
        output.mkdir(parents=True, exist_ok=True)
        archive_path = _official_archive(version, archive, base, output / "node-cache")
        _extract(archive_path, root, version)
        installed = True
        info = inspect_node(root, config)
    if not info["installed"]:
        raise RuntimeError("Node.js/npm health check failed: " + info["reason"])
    env = _configure_npm(config)
    process_env = _probe_env()
    process_env.update(env)
    node, cli = Path(info["executable"]), Path(info["npm_cli"])
    for key, variable in (("cache", "NPM_CONFIG_CACHE"), ("prefix", "NPM_CONFIG_PREFIX"),
                          ("userconfig", "NPM_CONFIG_USERCONFIG")):
        check = _run(node, [str(cli), "config", "get", key], cwd=config, env=process_env)
        if check.returncode or os.path.normcase(os.path.abspath(check.stdout.strip())) != os.path.normcase(os.path.abspath(env[variable])):
            raise RuntimeError(f"npm did not use the selected {key} path: {check.stderr.strip()[:400]}")
    result = {**info, "root": str(node.parent), "node": str(node), "npm_cli": str(cli),
              "npm_user_config": env["NPM_CONFIG_USERCONFIG"], "npm_cache": env["NPM_CONFIG_CACHE"],
              "npm_prefix": env["NPM_CONFIG_PREFIX"], "config_root": str(config),
              "npmrc": env["NPM_CONFIG_USERCONFIG"], "cache": env["NPM_CONFIG_CACHE"],
              "installed": True, "just_installed": installed, "env": env,
              "source_index": NODE_INDEX if installed else None}
    output.mkdir(parents=True, exist_ok=True)
    (output / "node-state.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
