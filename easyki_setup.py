"""Install/reuse the official EasyKiConverter portable app without elevation.

Only Python's standard library is required. Existing application preferences are
read, never rewritten by setup. The launcher always passes a native --config
argument, so its location is independent of the installed executable.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import urllib.request
import uuid
import zipfile

REPOSITORY = "EasyKiconverter/EasyKiConverter"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 3 * 1024 * 1024 * 1024


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plain_path(path: Path, *, writable: bool = False) -> Path:
    """Reject links before resolution; never follow junctions during installation."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError(f"Reparse/symbolic-link paths are not supported: {item}")
    if writable and os.name == "nt":
        for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            value = os.environ.get(variable)
            if value and path.is_relative_to(Path(os.path.abspath(value))):
                raise RuntimeError(f"Choose a writable user directory; setup does not elevate: {path}")
    return path


def _run(executable: Path, arguments: list[str], output: Path, name: str, *, cwd: Path) -> subprocess.CompletedProcess:
    """subprocess.run kills only the process it created if the timeout expires."""
    try:
        value = subprocess.run([str(executable), *arguments], cwd=cwd, capture_output=True,
                               timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"EasyKiConverter {name} timed out; the setup probe was stopped") from exc
    (output / f"{name}.stdout.txt").write_bytes(value.stdout)
    (output / f"{name}.stderr.txt").write_bytes(value.stderr)
    if value.returncode:
        raise RuntimeError(f"EasyKiConverter {name} failed ({value.returncode}); see {output / (name + '.stderr.txt')}")
    return value


def windows_architecture() -> str:
    machine = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()).lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    raise RuntimeError(f"Unsupported Windows architecture {machine!r}; choose a supported x64/ARM64 machine")


def fetch_latest_release() -> dict:
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": "AD2KiCad-Setup", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        value = json.loads(response.read(4 * 1024 * 1024))
    if value.get("draft") or value.get("prerelease") or not value.get("assets"):
        raise RuntimeError("GitHub did not return a stable EasyKiConverter release with assets")
    return value


def select_asset(release: dict, architecture: str) -> dict:
    # ARM64 must never silently receive the x64 archive.
    suffix = "-win64.zip" if architecture == "x64" else "-win64-arm64.zip" if architecture == "arm64" else None
    if suffix is None:
        raise RuntimeError(f"Unsupported EasyKiConverter architecture: {architecture}")
    matches = [item for item in release.get("assets", [])
               if item.get("name", "").lower().startswith("easykiconverter-") and item["name"].lower().endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"Release {release.get('tag_name')} has no unique Windows {architecture} portable archive; no architecture substitution was made")
    asset = matches[0]
    if Path(asset["name"]).name != asset["name"] or not 0 < int(asset.get("size", 0)) <= MAX_ARCHIVE_BYTES:
        raise RuntimeError("Invalid EasyKiConverter release asset metadata")
    return asset


def _official_asset_url(url: str) -> str:
    expected = f"https://github.com/{REPOSITORY}/releases/download/".lower()
    if not isinstance(url, str) or not url.lower().startswith(expected):
        raise RuntimeError(f"Refusing a non-official EasyKiConverter release asset URL: {url}")
    return url


def expected_sha256(release: dict, asset: dict) -> str:
    digest = asset.get("digest") or ""
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        return digest.split(":", 1)[1].lower()
    sidecars = [item for item in release.get("assets", []) if item.get("name") == asset["name"] + ".sha256sum"]
    if len(sidecars) != 1:
        raise RuntimeError("Official release has no SHA-256 digest or checksum asset; cannot verify download")
    request = urllib.request.Request(_official_asset_url(sidecars[0]["browser_download_url"]), headers={"User-Agent": "AD2KiCad-Setup"})
    with urllib.request.urlopen(request, timeout=45) as response:
        checksum = response.read(8192).decode("utf-8-sig")
    match = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+\*?([^\r\n]+)\s*", checksum)
    if not match or match.group(2).strip() != asset["name"]:
        raise RuntimeError("Official SHA-256 checksum does not name the selected archive")
    return match.group(1).lower()


def download_archive(asset: dict, target: Path, expected: str) -> Path:
    if target.is_file() and _sha256(target) == expected:
        return target
    if target.exists():
        raise RuntimeError(f"Existing download has a different SHA-256; retain it for inspection: {target}")
    request = urllib.request.Request(_official_asset_url(asset["browser_download_url"]), headers={"User-Agent": "AD2KiCad-Setup"})
    partial = target.with_name(target.name + ".partial-" + uuid.uuid4().hex)
    received = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as destination:
        while chunk := response.read(1024 * 1024):
            received += len(chunk)
            if received > MAX_ARCHIVE_BYTES or received > int(asset["size"]):
                raise RuntimeError(f"Archive exceeds the declared size; partial file retained: {partial}")
            destination.write(chunk)
    if received != int(asset["size"]) or _sha256(partial) != expected:
        raise RuntimeError(f"EasyKiConverter archive size/SHA-256 mismatch; retained: {partial}")
    partial.rename(target)
    return target


def safe_extract(archive: Path, destination: Path) -> None:
    destination = _plain_path(destination, writable=True)
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"Extraction directory must be empty: {destination}")
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 30000 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
            raise RuntimeError("EasyKiConverter archive expands beyond the supported limit")
        names: set[str] = set()
        for item in entries:
            name = item.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            mode = (item.external_attr >> 16) & 0xffff
            if (not parts or name.startswith("/") or any(part in ("", ".", "..") for part in parts)
                    or any(any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                           or part.rstrip(". ") != part for part in parts)
                    or stat.S_ISLNK(mode) or item.external_attr & 0x400):
                raise RuntimeError(f"Unsafe ZIP member: {item.filename}")
            key = "/".join(parts).casefold()
            if key in names:
                raise RuntimeError(f"Duplicate Windows ZIP member: {item.filename}")
            names.add(key)
            for part in parts:
                if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I):
                    raise RuntimeError(f"Reserved Windows ZIP member: {item.filename}")
        destination.mkdir(parents=True, exist_ok=True)
        for item in entries:
            target = destination.joinpath(*PurePosixPath(item.filename.replace("\\", "/")).parts)
            if item.is_dir() or item.filename.endswith("\\"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink)


def _install(root: Path, output: Path) -> dict:
    if os.name != "nt":
        raise RuntimeError("The AD2KiCad EasyKiConverter installer currently supports Windows only")
    _plain_path(root, writable=True)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise RuntimeError(f"EasyKiConverter installation folder is nonempty and no executable was found: {root}")
    release = fetch_latest_release()
    _write_json(output / "release-metadata.json", release)
    architecture = windows_architecture()
    asset = select_asset(release, architecture)
    expected = expected_sha256(release, asset)
    archive = download_archive(asset, output / asset["name"], expected)
    # Always verify even if a caller supplied a local cache in a test harness.
    if _sha256(archive) != expected:
        raise RuntimeError("EasyKiConverter ZIP checksum mismatch")
    staged = output / ("extracted-" + uuid.uuid4().hex)
    safe_extract(archive, staged)
    if not (staged / "easykiconverter.exe").is_file() or not (staged / "Qt6Core.dll").is_file():
        raise RuntimeError(f"Official portable archive lacks executable/Qt runtime: {staged}")
    # Verify DLL loading before publishing. Do not run the bundled VC runtime
    # installer: it can require elevation; a missing runtime is reported instead.
    _run(staged / "easykiconverter.exe", ["--version"], output, "staged-version", cwd=staged)
    _plain_path(root, writable=True)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"Installation directory changed while downloading: {root}")
    shutil.copytree(staged, root, dirs_exist_ok=True)
    receipt = {"repository": REPOSITORY, "tag": release["tag_name"], "asset": asset["name"],
               "architecture": architecture, "sha256": expected, "download_url": asset["browser_download_url"]}
    _write_json(root / ".ad2kicad-easyki-install.json", receipt)
    return receipt


def _known_appdata_paths() -> list[Path]:
    roots = [Path(os.environ[name]) for name in ("APPDATA", "LOCALAPPDATA") if os.environ.get(name)]
    if os.name == "nt":
        # Qt uses KnownFolder APIs on Windows, so changing APPDATA in a child
        # environment does not isolate its recovery directory.
        import ctypes
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        for identifier in ("3eb685db-65f9-4cf6-a03a-e3ef65729f3d", "f1b32785-6fba-4fcf-9d55-7b8e7f157091"):
            folder_id = ctypes.create_string_buffer(uuid.UUID(identifier).bytes_le)
            value = ctypes.c_wchar_p()
            result = shell.SHGetKnownFolderPath(folder_id, 0x4000, None, ctypes.byref(value))
            if result != 0:
                raise RuntimeError("Cannot verify Windows AppData; config-writing smoke probe is unsafe")
            try:
                roots.append(Path(value.value))
            finally:
                ole.CoTaskMemFree(value)
    return list(dict.fromkeys(roots))


def _recovery_data_present() -> bool:
    # Every CLI mode except help/version invokes upstream transaction recovery
    # in default AppData. Avoid touching another running GUI's transaction data.
    for root in _known_appdata_paths():
        backup = root / "EasyKiConverter" / "EasyKiConverter" / "backups"
        if backup.exists() and any(backup.iterdir()):
            return True
    return False


def _launcher(config_root: Path, executable: Path, config_path: Path, cache: Path | None) -> Path:
    config_root = _plain_path(config_root, writable=True)
    config_root.mkdir(parents=True, exist_ok=True)
    launcher = config_root / "Start-EasyKiConverter.ps1"
    settings = config_root / "ad2kicad-easyki-launch.json"
    log_directory = config_root / "logs"
    log_directory.mkdir(exist_ok=True)
    data = {"executable": str(executable), "config": str(config_path),
            "log": str(log_directory / "easykiconverter.log"), "cache": str(cache) if cache else None}
    script = '''# Generated by AD2KiCad. All path data is read from JSON, never evaluated.
[CmdletBinding(PositionalBinding=$false)]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArguments)
$ErrorActionPreference = 'Stop'
$settingsFile = Join-Path $PSScriptRoot 'ad2kicad-easyki-launch.json'
$settings = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not [System.IO.File]::Exists($settings.executable)) { throw 'EasyKiConverter executable is missing.' }
if (-not [System.IO.File]::Exists($settings.config)) { throw 'EasyKiConverter config is missing. Rerun setup.' }
$arguments = @('--config', [string]$settings.config, '--log-file', [string]$settings.log)
if ($settings.cache) { $arguments += @('--cache-dir', [string]$settings.cache) }
if ($AppArguments) { $arguments += $AppArguments }
Push-Location -LiteralPath $PSScriptRoot
try {
    & ([string]$settings.executable) @arguments
    $resultCode = $LASTEXITCODE
} finally { Pop-Location }
exit $resultCode
'''
    for path in (launcher, settings):
        _plain_path(path, writable=True)
    # Back up previous launcher settings when configuration intentionally changes.
    for path, content in ((settings, json.dumps(data, ensure_ascii=False, indent=2) + "\n"), (launcher, script)):
        encoded = content.encode("utf-8-sig" if path.suffix == ".ps1" else "utf-8")
        if path.exists() and path.read_bytes() == encoded:
            continue
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".backup-" + uuid.uuid4().hex))
        path.write_bytes(encoded)
    return launcher


def ensure_easyki(root: Path, config_root: Path | None, output: Path, *, existing_config: Path | None = None) -> dict:
    """Configure the selected installation; preserve pre-existing preferences.

    For a reused app, existing_config identifies its current JSON preferences.
    config_root then holds only the AD2KiCad launcher. For a new app, config_root
    holds both config.json and the launcher; default is <installation>/config.
    All audit logs and download artifacts are written under output.
    """
    root = _plain_path(root)
    output = _plain_path(output, writable=True)
    output.mkdir(parents=True, exist_ok=True)
    executable = root / "easykiconverter.exe"
    _plain_path(executable)
    installed = not executable.is_file()
    release = _install(root, output) if installed else None
    version_run = _run(executable, ["--version"], output, "version", cwd=output)
    version = version_run.stdout.decode("utf-8", errors="replace").strip()
    if not re.search(r"EasyKiConverter\s+\d+\.\d+", version, re.I):
        raise RuntimeError(f"Unexpected EasyKiConverter version response: {version!r}")
    help_run = _run(executable, ["--help"], output, "help", cwd=output)
    help_text = help_run.stdout.decode("utf-8", errors="replace")
    required = ("--config", "--cache-dir", "--log-file")
    if not all(option in help_text for option in required):
        raise RuntimeError("This EasyKiConverter version lacks required --config/--cache-dir/--log-file support; update it explicitly")
    config_root = _plain_path(config_root or root / "config", writable=True)
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = _plain_path(existing_config if existing_config else config_root / "config.json")
    config_existed = config_path.exists()
    if existing_config and not config_existed:
        raise RuntimeError(f"Selected existing EasyKiConverter config is missing: {config_path}")
    if config_existed:
        preferences = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(preferences, dict):
            raise RuntimeError(f"EasyKiConverter config must be a JSON object: {config_path}")
    else:
        _plain_path(config_path, writable=True)
        cache = config_root / "cache"
        cache.mkdir(exist_ok=True)
        preferences = {"language": "zh_CN", "cacheDir": cache.as_posix(), "exportSymbol": True,
                       "exportFootprint": True, "exportModel3D": True,
                       "exportModel3DPathMode": 0, "overwriteExistingFiles": False}
        # An absent --config file falls back to upstream's default location.
        # Seed valid JSON before the first invocation to bind saving to this file.
        _write_json(config_path, preferences)
    smoke = {"passed": None, "status": "skipped_existing_transaction_data"}
    if not _recovery_data_present() and "--completion" in help_text:
        probe_dir = output / ("config-probe-" + uuid.uuid4().hex)
        probe_dir.mkdir()
        probe_config = probe_dir / "config.json"
        _write_json(probe_config, {"ad2kicadProbe": "preserve", "cacheDir": (probe_dir / "cache").as_posix()})
        _run(executable, ["--config", str(probe_config), "--cache-dir", str(probe_dir / "cache"),
                          "--completion", "bash"], output, "config-probe", cwd=probe_dir)
        actual = json.loads(probe_config.read_text(encoding="utf-8-sig"))
        if actual.get("ad2kicadProbe") != "preserve" or "libName" not in actual:
            raise RuntimeError("EasyKiConverter did not load/save the selected custom config snapshot")
        smoke = {"passed": True, "status": "snapshot_loaded_and_saved", "path": str(probe_config)}
    elif "--completion" not in help_text:
        smoke["status"] = "skipped_no_completion_command"
    cache = Path(preferences["cacheDir"]) if not existing_config and preferences.get("cacheDir") else None
    launcher = _launcher(config_root, executable, config_path, cache)
    result = {"passed": True, "path": str(root), "executable": str(executable), "version": version,
              "installed": installed, "reused": not installed, "config_path": str(config_path),
              "config_root": str(config_root), "config_preserved": config_existed,
              "config_sha256": _sha256(config_path), "launcher": str(launcher), "release": release,
              "config_smoke": smoke, "checks": ["native --version", "native --help", "required native options", "valid config JSON", "explicit-config launcher"],
              "notes": ["No conversion is performed during setup.",
                        "Upstream export-transaction backups still use the application's default AppData location.",
                        "Custom configuration applies when launching through the generated launcher."]}
    _write_json(output / "easyki-state.json", result)
    return result
