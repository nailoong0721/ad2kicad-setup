"""Find existing AD2KiCad tools without installing or launching their programs.

Only explicit selections and this tool's saved selections may name a new
installation directory. Automatic discovery never chooses an installation
destination for a missing program.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from node_setup import inspect_node
from easyki_setup import _plain_path


RECORD_NAME = "ad2kicad-tools.json"
EASY_EXE = "EasyKiConverter.exe"
NODE_EXE = "node.exe"


def _path(value):
    if value is None or not str(value).strip():
        return None
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).absolute()


def _same_path(left, right):
    """Compare location spelling without traversing links or requiring it to exist."""
    if left is None or right is None:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _record(codex_root):
    path = Path(codex_root) / RECORD_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read saved tool locations: {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") not in (1, 2):
        raise ValueError(f"Unsupported saved tool location format: {path}")
    return data


def _mcp_installed(root):
    """A source checkout can be reused even when its build is absent/broken."""
    return bool(root and root.is_dir() and (root / "package.json").is_file()
                and (root / "python" / "kicad_interface.py").is_file())


def _validate_node_directory(root, installed):
    if root.is_symlink() or (root.exists() and getattr(root.stat(), "st_file_attributes", 0) & 0x400):
        raise ValueError(f"Selected Node.js directory must not be a link or reparse point: {root}")
    _validate_selected_directory(root, installed, "Node.js")


def _easy_executable(root):
    if root is None:
        return None
    if root.is_file():
        return root if root.name.casefold() == EASY_EXE.casefold() else None
    for relative in (EASY_EXE, "bin/" + EASY_EXE, "EasyKiConverter/" + EASY_EXE):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def _validate_selected_directory(root, installed, label):
    if root.exists() and not root.is_dir():
        raise ValueError(f"Selected {label} directory is not a directory: {root}")
    if root.is_dir() and not installed and next(root.iterdir(), None) is not None:
        raise ValueError(f"Selected {label} directory is nonempty and has no recognized installation; "
                         f"it will not be overwritten: {root}")


def _mcp_config_candidates(config):
    servers = config.get("mcp_servers", {}) if isinstance(config, dict) else {}
    if not isinstance(servers, dict):
        return []
    found = []
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        arguments = server.get("args", [])
        if not isinstance(arguments, list):
            continue
        for argument in arguments:
            if not isinstance(argument, str):
                continue
            normalized = argument.replace("\\", "/")
            if normalized.casefold().split("/")[-2:] != ["dist", "index.js"]:
                continue
            entry = _path(argument)
            # A relative launch argument is relative to the server's cwd, not
            # the workspace from which this discovery function was called.
            if not Path(argument).is_absolute():
                cwd = _path(server.get("cwd"))
                if cwd is None:
                    continue
                entry = cwd / argument
            found.append((entry.parent.parent, "codex_mcp_config"))
    return found


def _configured_mcp_config(config, root):
    servers = config.get("mcp_servers", {}) if isinstance(config, dict) else {}
    if not isinstance(servers, dict):
        return None
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        candidates = _mcp_config_candidates({"mcp_servers": {name: server}})
        if not any(_same_path(candidate, root) for candidate, _ in candidates):
            continue
        arguments = server.get("args", [])
        for index, argument in enumerate(arguments):
            if argument == "--config" and index + 1 < len(arguments):
                selected = arguments[index + 1]
            elif isinstance(argument, str) and argument.startswith("--config="):
                selected = argument[len("--config="):]
            else:
                continue
            if not isinstance(selected, str) or not selected.strip():
                continue
            path = Path(os.path.expandvars(os.path.expanduser(selected)))
            if not path.is_absolute():
                cwd = _path(server.get("cwd"))
                if cwd is None:
                    continue
                path = cwd / path
            return path.absolute()
    return None


def _powershell_json(script, payload=None):
    """Run fixed read-only PowerShell, passing path data as UTF-8 JSON stdin."""
    if os.name != "nt":
        return []
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not executable.is_file():
        return []
    prefix = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
              "$u=New-Object System.Text.UTF8Encoding -ArgumentList $false; "
              "[Console]::InputEncoding=$u; [Console]::OutputEncoding=$u; ")
    encoded = base64.b64encode((prefix + script).encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                # ASCII JSON escapes round-trip all Unicode
                                # while avoiding PowerShell 5.1 console input
                                # code-page behavior for redirected stdin.
                                input=json.dumps(payload or [], ensure_ascii=True),
                                text=True, encoding="utf-8", capture_output=True, timeout=25,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return []
        parsed = json.loads(result.stdout.lstrip("\ufeff").strip() or "[]")
        return parsed if isinstance(parsed, list) else [parsed]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _process_candidates():
    script = r"""
$rows=@(Get-CimInstance Win32_Process -Filter "Name = 'EasyKiConverter.exe'" -ErrorAction SilentlyContinue);
$result=@(); foreach($row in $rows) { if($row.ExecutablePath) { $result += [string]$row.ExecutablePath } };
ConvertTo-Json -InputObject @($result) -Compress
"""
    return [(_path(value), "running_process") for value in _powershell_json(script)
            if isinstance(value, str) and value]


def _registry_candidates():
    if os.name != "nt":
        return []
    import winreg
    results = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                parent = winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        name = winreg.EnumKey(parent, index)
                        with winreg.OpenKey(parent, name) as key:
                            display = str(winreg.QueryValueEx(key, "DisplayName")[0])
                            if "easykiconverter" not in "".join(display.casefold().split()):
                                continue
                            for field in ("InstallLocation", "DisplayIcon"):
                                try:
                                    raw = str(winreg.QueryValueEx(key, field)[0]).strip()
                                except OSError:
                                    continue
                                if field == "DisplayIcon":
                                    # DisplayIcon is a path (optionally quoted)
                                    # followed by an icon index, never a command.
                                    if raw.startswith('"'):
                                        raw = raw[1:].split('"', 1)[0]
                                    else:
                                        suffix = raw.rsplit(",", 1)
                                        if len(suffix) == 2 and suffix[1].strip().lstrip("-").isdigit():
                                            raw = suffix[0].strip()
                                if raw:
                                    results.append((_path(raw), "uninstall_registry"))
                    except OSError:
                        continue
    return results


def _shortcut_candidates():
    if os.name != "nt":
        return []
    # Only known Desktop/Start Menu trees are enumerated, with a hard bound.
    roots = []
    # Ask Windows for redirected Desktop/Start Menu locations (for example
    # OneDrive Desktop); environment-based locations below remain fallbacks.
    try:
        import ctypes
        for folder_id in (0x10, 0x19, 0x0B, 0x16):
            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, folder_id, None, 0, buffer) == 0 and buffer.value:
                roots.append(Path(buffer.value))
    except (AttributeError, OSError):
        pass
    profile = _path(os.environ.get("USERPROFILE"))
    roaming = _path(os.environ.get("APPDATA"))
    program_data = _path(os.environ.get("PROGRAMDATA"))
    public = _path(os.environ.get("PUBLIC"))
    if profile:
        roots.append(profile / "Desktop")
    if public:
        roots.append(public / "Desktop")
    if roaming:
        roots.append(roaming / "Microsoft/Windows/Start Menu/Programs")
    if program_data:
        roots.append(program_data / "Microsoft/Windows/Start Menu/Programs")
    links = []
    visited = 0
    seen = set()
    for root in roots:
        if str(root).casefold() in seen:
            continue
        seen.add(str(root).casefold())
        try:
            if not root.is_dir() or root.is_symlink() or getattr(root.stat(), "st_file_attributes", 0) & 0x400:
                continue
        except OSError:
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            visited += len(directories) + len(files)
            if visited > 10000:
                break
            safe = []
            for name in directories:
                child = Path(current) / name
                try:
                    if not child.is_symlink() and not getattr(child.stat(), "st_file_attributes", 0) & 0x400:
                        safe.append(name)
                except OSError:
                    pass
            directories[:] = safe
            for name in files:
                if name.casefold().endswith(".lnk") and "easyki" in name.casefold():
                    links.append(str(Path(current) / name))
        if visited > 10000:
            break
    if not links:
        return []
    script = r"""
$paths=ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd());
$shell=New-Object -ComObject WScript.Shell; $result=@();
foreach($item in $paths) {
 if([System.IO.File]::Exists([string]$item)) {
  try { $link=$shell.CreateShortcut([string]$item); if($link.TargetPath) { $result += [string]$link.TargetPath } } catch { }
 }
}; ConvertTo-Json -InputObject @($result) -Compress
"""
    return [(_path(value), "shortcut") for value in _powershell_json(script, links)
            if isinstance(value, str) and value]


def _easy_candidates():
    # No candidate is executed here: these paths are for a later health probe.
    candidates = []
    which = shutil.which(EASY_EXE)
    if which:
        candidates.append((_path(which), "path"))
    candidates.extend(_process_candidates())
    candidates.extend(_registry_candidates())
    candidates.extend(_shortcut_candidates())
    local = _path(os.environ.get("LOCALAPPDATA"))
    if local:
        candidates.extend([(local / "Programs/EasyKiConverter", "local_programs"),
                           (local / "Programs/EasyKiconverter/EasyKiConverter", "local_programs"),
                           (local / "EasyKiConverter", "local_appdata")])
    return candidates


def inspect_tool_locations(codex_root: Path, config: dict, mcp_root=None,
                           easyki_root=None, easyki_config_root=None, mcp_config_root=None,
                           node_root=None, node_config_root=None) -> dict:
    saved = _record(codex_root)
    mcp_saved = saved.get("mcp", {})
    easy_saved = saved.get("easyki", {})
    node_saved = saved.get("node", {}) if isinstance(saved.get("node", {}), dict) else {}
    if not isinstance(mcp_saved, dict) or not isinstance(easy_saved, dict):
        raise ValueError("Saved tool location entries must be JSON objects")
    mcp = {"root": None, "config_root": None, "config_path": None,
           "installed": False, "needs_directory": True, "source": "not_found"}
    saved_mcp_root = _path(mcp_saved.get("root"))
    selected_mcp = _path(mcp_root) or saved_mcp_root
    if (selected_mcp and not _path(mcp_root) and selected_mcp.exists()
            and not _mcp_installed(selected_mcp)
            and (not selected_mcp.is_dir() or any(selected_mcp.iterdir()))):
        mcp.update(rejected_saved_root=str(selected_mcp), reason='Saved MCP installation is incomplete; select a new empty directory or repair it.')
        selected_mcp = None
    if selected_mcp:
        installed = _mcp_installed(selected_mcp)
        _validate_selected_directory(selected_mcp, installed, "KiCAD MCP")
        mcp.update(root=str(selected_mcp), installed=installed, needs_directory=False,
                   source="explicit" if _path(mcp_root) else "saved_selection")
    else:
        candidates = _mcp_config_candidates(config) + [(Path(codex_root) / "tools/KiCAD-MCP-Server", "legacy_codex_tools")]
        for candidate, source in candidates:
            if _mcp_installed(candidate):
                mcp.update(root=str(candidate.absolute()), installed=True, needs_directory=False, source=source)
                break
    explicit_mcp_config = _path(mcp_config_root)
    # A saved configuration belongs to its saved installation. Selecting a
    # different program directory must not carry that private choice across.
    reuse_mcp_saved_config = _same_path(mcp["root"], saved_mcp_root)
    saved_mcp_config = _path(mcp_saved.get("config_path")) if reuse_mcp_saved_config else None
    saved_mcp_config_root = _path(mcp_saved.get("config_root")) if reuse_mcp_saved_config else None
    if explicit_mcp_config:
        mcp_config = explicit_mcp_config / "config.json"
    elif saved_mcp_config:
        mcp_config = saved_mcp_config
    elif saved_mcp_config_root:
        mcp_config = saved_mcp_config_root / "config.json"
    else:
        mcp_config = _configured_mcp_config(config, Path(mcp["root"])) if mcp["root"] else None
        if mcp_config is None and mcp["installed"]:
            default_config = Path(mcp["root"]) / "config/default-config.json"
            if default_config.is_file():
                mcp_config = default_config
    if mcp_config:
        mcp.update(config_root=str(mcp_config.parent), config_path=str(mcp_config))
    easy = {"root": None, "executable": None, "config_root": None, "existing_config": None,
            "installed": False, "needs_directory": True, "source": "not_found"}
    saved_easy_root = _path(easy_saved.get("root"))
    selected_easy = _path(easyki_root) or saved_easy_root
    if (selected_easy and not _path(easyki_root) and selected_easy.exists()
            and not _easy_executable(selected_easy)
            and (not selected_easy.is_dir() or any(selected_easy.iterdir()))):
        easy.update(rejected_saved_root=str(selected_easy), reason='Saved EasyKiConverter installation is incomplete; select a new empty directory or repair it.')
        selected_easy = None
    if selected_easy:
        executable = _easy_executable(selected_easy)
        # root means installation directory; an exe path is not silently
        # reinterpreted as a destination that could overwrite its parent.
        _validate_selected_directory(selected_easy, bool(executable), "EasyKiConverter")
        # Consumers launch root/easykiconverter.exe. An installed wrapper
        # directory may put it in bin/ or EasyKiConverter/: normalize to the
        # executable's parent so detection and setup agree on that location.
        normalized_root = executable.parent if executable else selected_easy
        easy.update(root=str(normalized_root), executable=str(executable) if executable else None,
                    installed=bool(executable), needs_directory=False,
                    source="explicit" if _path(easyki_root) else "saved_selection")
    else:
        for candidate, source in _easy_candidates():
            executable = _easy_executable(candidate)
            if executable:
                easy.update(root=str(executable.parent), executable=str(executable), installed=True,
                            needs_directory=False, source=source)
                break
    saved_easy_executable = _easy_executable(saved_easy_root)
    normalized_saved_easy_root = saved_easy_executable.parent if saved_easy_executable else saved_easy_root
    reuse_easy_saved_config = _same_path(easy["root"], normalized_saved_easy_root)
    config_root = _path(easyki_config_root) or (_path(easy_saved.get("config_root")) if reuse_easy_saved_config else None)
    if config_root:
        easy["config_root"] = str(config_root)
        config_path = config_root / "config.json"
        if config_path.is_file():
            easy["existing_config"] = str(config_path)
    elif easy["installed"]:
        local = _path(os.environ.get("LOCALAPPDATA"))
        if local:
            config_path = local / "EasyKiConverter/EasyKiConverter/config.json"
            if config_path.is_file():
                easy["config_root"] = str(config_path.parent)
                easy["existing_config"] = str(config_path)
    node = {"root": None, "executable": None, "npm": None, "config_root": None,
            "installed": False, "needs_directory": True, "source": "not_found"}
    saved_node_root = _path(node_saved.get("root"))
    selected_node = _path(node_root) or saved_node_root
    # A stale saved installation must not prevent opening the directory wizard.
    # Explicit bad selections still fail with a concrete correction message.
    if selected_node and not _path(node_root) and selected_node.is_dir() and any(selected_node.iterdir()):
        saved_health = inspect_node(selected_node)
        if not saved_health['installed']:
            node.update(reason=saved_health.get('reason'), rejected_saved_root=str(selected_node))
            selected_node = None
    if selected_node:
        status = inspect_node(selected_node)
        healthy, reason = bool(status['installed']), status.get('reason')
        _validate_node_directory(selected_node, healthy)
        normalized_root = Path(status['root']) if healthy else selected_node
        node.update(root=str(normalized_root), executable=status.get('executable'),
                    npm=status.get('npm'), installed=healthy, version=status.get('version'),
                    needs_directory=False, source="explicit" if _path(node_root) else "saved_selection",
                    reason=reason)
    else:
        path_node = shutil.which("node.exe") or shutil.which("node")
        candidates = []
        if path_node:
            candidates.append((_path(path_node).parent, "path"))
        servers = config.get('mcp_servers', {})
        for server in (servers.values() if isinstance(servers, dict) else []):
            if isinstance(server, dict) and isinstance(server.get('command'), str):
                command = _path(server['command'])
                if command and command.name.casefold() == NODE_EXE and command.is_file():
                    candidates.append((command.parent, 'codex_mcp_command'))
        local = _path(os.environ.get("LOCALAPPDATA"))
        program = _path(os.environ.get("ProgramFiles"))
        if local:
            candidates.append((local / "Programs/nodejs", "local_programs"))
        if program:
            candidates.append((program / "nodejs", "program_files"))
        for candidate, source in candidates:
            status = inspect_node(candidate)
            if status['installed']:
                node.update(root=status['root'], executable=status['executable'], npm=status['npm'],
                            installed=True, needs_directory=False, source=source, reason=status.get('reason'),
                            version=status.get('version'))
                break
    saved_node_matches = _same_path(node["root"], saved_node_root)
    selected_config = _path(node_config_root)
    saved_config = _path(node_saved.get("config_root")) if saved_node_matches else None
    config_root = selected_config or saved_config
    if config_root:
        node["config_root"] = str(config_root)
    elif node["installed"]:
        node["config_root"] = str(Path(codex_root) / "tools-config" / "nodejs")
    return {"node": node, "mcp": mcp, "easyki": easy}


def validate_tool_plan(locations, output=None):
    """Reject conflicting or unwritable choices before downloading any tools."""
    programs = {}
    configs = {}
    for name in ('node', 'mcp', 'easyki'):
        choice = locations[name]
        if choice.get('needs_directory') or not choice.get('root'):
            raise ValueError(f'Select custom {name} program and configuration directories first.')
        root = _plain_path(Path(choice['root']), writable=not choice['installed'])
        _validate_selected_directory(root, choice['installed'], name)
        programs[name] = root
        if not choice.get('config_root'):
            if choice['installed'] and name == 'easyki':
                continue  # Existing upstream application preferences may use its native default.
            raise ValueError(f'Select custom {name} configuration directory first.')
        config = _plain_path(Path(choice['config_root']))
        if config.exists() and not config.is_dir():
            raise ValueError(f'{name} configuration path is not a directory: {config}')
        configs[name] = config
    for name, root in programs.items():
        for other, other_root in programs.items():
            if name != other and (root.is_relative_to(other_root) or other_root.is_relative_to(root)):
                raise ValueError(f'Program directories must be separate, not equal or nested: {name}, {other}')
        for other, config in configs.items():
            if root == config or (config.is_relative_to(root) and (name != other or not locations[name]['installed'])):
                raise ValueError(f'{other} configuration must be outside the {name} installation directory: {config}')
            if name != other and root.is_relative_to(config):
                raise ValueError(f'{name} program directory must not be inside {other} configuration: {root}')
        if not locations[name]['installed'] and output and Path(output).absolute().is_relative_to(root):
            raise ValueError(f'Run logs must be outside a new installation directory: {root}')
    for name, config in configs.items():
        for other, other_config in configs.items():
            if name != other and (config.is_relative_to(other_config) or other_config.is_relative_to(config)):
                raise ValueError(f'Configuration directories must be separate, not equal or nested: {name}, {other}')
    # A short-lived probe checks Windows ACLs accurately, without creating the
    # chosen installation directories or touching existing configuration files.
    writable = [root for name, root in programs.items() if not locations[name]['installed']]
    for name, config in configs.items():
        if name == 'node' or not (config / 'config.json').is_file():
            writable.append(config)
    for target in writable:
        _plain_path(target, writable=True)
        parent = target
        while not parent.exists():
            parent = parent.parent
        if not parent.is_dir():
            raise ValueError(f'An ancestor is not a directory: {parent}')
        try:
            descriptor, temporary = tempfile.mkstemp(prefix='.ad2kicad-writecheck-', dir=parent)
            os.close(descriptor)
            os.unlink(temporary)
        except OSError as exc:
            raise ValueError(f'Choose a writable directory for {target}: {exc}') from exc
    return {'passed': True, 'programs': {k: str(v) for k, v in programs.items()},
            'configurations': {k: str(v) for k, v in configs.items()}}


def remember_tool_locations(codex_root, mcp_status, easyki_status, node_status=None):
    """Persist paths only; changing an existing record first creates a backup."""
    mcp_root = _path(mcp_status.get("root") or mcp_status.get("path"))
    mcp_config_root = _path(mcp_status.get("config_root"))
    mcp_config_path = _path(mcp_status.get("config_path"))
    if mcp_config_path is None and mcp_config_root:
        mcp_config_path = mcp_config_root / "config.json"
    if mcp_config_root is None and mcp_config_path:
        mcp_config_root = mcp_config_path.parent
    easy_root = _path(easyki_status.get("root") or easyki_status.get("path") or easyki_status.get("install_root"))
    executable = _path(easyki_status.get("executable"))
    if easy_root is None and executable:
        easy_root = executable.parent
    config_root = _path(easyki_status.get("config_root"))
    config_path = _path(easyki_status.get("config_path") or easyki_status.get("existing_config"))
    if config_root is None and config_path:
        config_root = config_path.parent
    node_root = _path((node_status or {}).get("root"))
    node_executable = _path((node_status or {}).get("node") or (node_status or {}).get("executable"))
    node_npm = _path((node_status or {}).get("npm"))
    node_config_root = _path((node_status or {}).get("config_root"))
    if mcp_root is None or easy_root is None or node_root is None:
        raise ValueError("MCP, EasyKiConverter and Node.js installation roots are required before saving selections")
    value = {"version": 2, "node": {"root": str(node_root), "executable": str(node_executable) if node_executable else None,
                                      "npm": str(node_npm) if node_npm else None,
                                      "config_root": str(node_config_root) if node_config_root else None},
             "mcp": {"root": str(mcp_root),
                                  "config_root": str(mcp_config_root) if mcp_config_root else None,
                                  "config_path": str(mcp_config_path) if mcp_config_path else None},
             "easyki": {"root": str(easy_root), "executable": str(executable) if executable else None,
                        "config_root": str(config_root) if config_root else None}}
    root = Path(codex_root)
    path = root / RECORD_NAME
    previous = _record(root)
    # Windows executable lookups can differ only by case (e.g. EasyKiConverter
    # vs easykiconverter). Preserve the saved spelling rather than making two
    # needless backups on every otherwise identical preflight/final save.
    for name in ('node', 'mcp', 'easyki'):
        previous_tool = previous.get(name, {})
        if isinstance(previous_tool, dict):
            for field, candidate in value[name].items():
                earlier = previous_tool.get(field)
                if candidate and earlier and _same_path(candidate, earlier):
                    value[name][field] = earlier
    if previous == value:
        return {"path": str(path), "changed": False, "backup": None}
    root.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup_root = root / "backups/ad2kicad"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_root / ("ad2kicad-tools-" + stamp + ".json")
        shutil.copy2(path, backup)
    descriptor, temporary = tempfile.mkstemp(prefix=".ad2kicad-tools-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if _record(root) != value:
        raise RuntimeError("Tool location record read-back mismatch")
    return {"path": str(path), "changed": True, "backup": str(backup) if backup else None}
