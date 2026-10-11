"""Idempotent Windows KiCad/Codex prerequisites setup; standard library only."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib

from mcp_probe import probe
from tool_locations import inspect_tool_locations, remember_tool_locations, validate_tool_plan
from easyki_setup import ensure_easyki, _plain_path
from node_setup import ensure_node
from source_setup import ensure_source

HAPPY_URL = "https://github.com/aklofas/kicad-happy.git"
MCP_URL = "https://github.com/mixelpixx/KiCAD-MCP-Server.git"
SKILLS = ("kicad", "spice", "emc", "datasheets", "bom", "digikey", "mouser", "lcsc", "element14", "jlcpcb", "pcbway")


def run(argv, *, cwd=None, env=None, timeout=300, check=True):
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if check and result.returncode:
        # Commands here contain paths/package names only, never credentials.
        raise RuntimeError(f"Command failed ({result.returncode}): {argv[0]}\n{result.stderr[-1800:]}\n{result.stdout[-1800:]}")
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clone_if_missing(url, target):
    return ensure_source(url, target)


def require_plain_tree(root):
    """A skill repair must not write through an existing file/directory link."""
    root = _plain_path(Path(root))
    if root.exists() and not root.is_dir():
        raise RuntimeError(f'Skill path is not a directory: {root}')
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            _plain_path(Path(current) / name)


def ensure_pip(python):
    """Bootstrap the selected KiCad interpreter's pip only if it is missing."""
    if run([python, '-m', 'pip', '--version'], check=False, timeout=60).returncode == 0:
        return False
    bootstrap = run([python, '-m', 'ensurepip', '--user'], check=False, timeout=180)
    if bootstrap.returncode or run([python, '-m', 'pip', '--version'], check=False, timeout=60).returncode:
        raise RuntimeError('The selected KiCad Python has no working pip and ensurepip could not repair it. '
                           'Repair/reinstall the complete KiCad Windows package, then rerun. '
                           + bootstrap.stderr[-1000:])
    return True


def check_happy(codex_root, output, smoke_schematic):
    skills_root = codex_root / "skills"
    missing = [name for name in SKILLS if not (skills_root / name / "SKILL.md").is_file()]
    required = ("analyze_schematic.py", "analyze_pcb.py", "sexp_parser.py")
    core_missing = [name for name in required if not (skills_root / "kicad" / "scripts" / name).is_file()]
    # Detect a broken existing installation as well as missing files. A healthy
    # copy is left untouched; the health error is retained if repair is needed.
    repair_reason = None
    if not core_missing:
        try:
            for filename in ("analyze_schematic.py", "analyze_pcb.py"):
                schema = run([sys.executable, skills_root / "kicad" / "scripts" / filename, "--schema"], timeout=90)
                json.loads(schema.stdout)
        except Exception as exc:
            core_missing = ["unhealthy-core"]
            repair_reason = str(exc)
    installed = []
    if missing or core_missing:
        checkout = codex_root / "tools" / "kicad-happy"
        clone_if_missing(HAPPY_URL, checkout)
        for name in SKILLS:
            target = skills_root / name
            if name not in missing and not (name == "kicad" and core_missing):
                continue
            source = checkout / "skills" / name
            if not (source / "SKILL.md").is_file():
                raise RuntimeError(f"Source skill is incomplete: {source}")
            require_plain_tree(source)
            require_plain_tree(target)
            if target.exists():
                backup = codex_root / "backups" / "ad2kicad" / (name + "-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
                shutil.copytree(target, backup, symlinks=True)
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
            installed.append(name)
    kicad = skills_root / "kicad"
    for filename in ("analyze_schematic.py", "analyze_pcb.py"):
        schema = run([sys.executable, kicad / "scripts" / filename, "--schema"], timeout=90)
        json.loads(schema.stdout)
    parser = kicad / "scripts" / "sexp_parser.py"
    parser_code = "import sys; sys.path.insert(0, sys.argv[1]); import sexp_parser; assert sexp_parser.parse('(kicad_pcb (version 20241229))')[0] == 'kicad_pcb'"
    run([sys.executable, "-c", parser_code, parser.parent])
    result = {"passed": True, "path": str(kicad), "reused": not installed,
              "installed_skills": installed, "skill_count": len(SKILLS),
              "python": sys.executable, "parser_sha256": sha256(parser),
              "checks": ["schematic analyzer schema", "pcb analyzer schema", "S-expression parser"]}
    if repair_reason:
        result["repair_reason"] = repair_reason
    if smoke_schematic:
        sample_dir = output / "smoke-input"
        sample_dir.mkdir(exist_ok=True)
        sample = sample_dir / Path(smoke_schematic).name
        if Path(smoke_schematic).resolve() != sample.resolve():
            shutil.copy2(smoke_schematic, sample)
        report_path = output / "happy-schematic-smoke.json"
        run([sys.executable, kicad / "scripts" / "analyze_schematic.py", sample,
             "--no-hierarchy", "--output", report_path, "--compact"], timeout=180)
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        count = len(report.get("components", []))
        if not count:
            raise RuntimeError("kicad-happy smoke analysis returned zero components")
        result["schematic_smoke"] = {"path": str(sample), "component_count": count, "output": str(report_path)}
    return result


def probe_python(python, output):
    script = output / "check-kicad-python.py"
    script.write_text('''import importlib, importlib.metadata, json, sys
result = {"python": sys.executable, "version": sys.version, "modules": {}, "passed": True}
modules = {"pcbnew": None, "kipy": "kicad-python", "sexpdata": "sexpdata", "skip": "kicad-skip", "PIL": "Pillow", "pymupdf": "pymupdf", "colorlog": "colorlog", "pydantic": "pydantic", "requests": "requests", "dotenv": "python-dotenv"}
for module, distribution in modules.items():
    try:
        imported = importlib.import_module(module)
        version = importlib.metadata.version(distribution) if distribution else imported.GetBuildVersion()
        result["modules"][module] = {"passed": True, "version": version}
    except Exception as exc:
        result["passed"] = False
        result["modules"][module] = {"passed": False, "error": str(exc)}
try:
    import cairosvg
    result["optional_cairosvg"] = {"passed": True}
except Exception as exc:
    result["optional_cairosvg"] = {"passed": False, "error": str(exc), "fallback": "pymupdf"}
print(json.dumps(result))
''', encoding="utf-8")
    value = run([python, script], timeout=120)
    return json.loads(value.stdout)


def validate_mcp_build(root):
    """Only bypass npm shims when the upstream pure-TypeScript contract holds."""
    package = json.loads((root / 'package.json').read_text(encoding='utf-8-sig'))
    scripts = package.get('scripts', {})
    if (scripts.get('build') != 'tsc' or scripts.get('prepare', 'npm run build') != 'npm run build'
            or any(scripts.get(key) for key in ('preinstall', 'install', 'postinstall', 'prepublish',
                                               'preprepare', 'postprepare', 'prebuild', 'postbuild'))):
        raise RuntimeError('This KiCAD MCP source uses a different build/install workflow. '
                           'Preserve it and update the setup package for this upstream version; no custom lifecycle script was skipped.')
    lock_path = root / 'package-lock.json'
    if not lock_path.is_file():
        raise RuntimeError('KiCAD MCP source is missing package-lock.json. Use a complete official checkout so runtime installation hooks can be checked before installing.')
    lock = json.loads(lock_path.read_text(encoding='utf-8-sig'))
    packages = lock.get('packages')
    if not isinstance(packages, dict):
        raise RuntimeError('KiCAD MCP dependency lock has no package metadata for installation-hook verification.')
    runtime_hooks = [name for name, item in packages.items() if name and isinstance(item, dict)
                     and item.get('hasInstallScript') and not item.get('dev')]
    if runtime_hooks:
        raise RuntimeError('This KiCAD MCP version requires runtime dependency installation hooks; '
                           'update the setup package before installing: ' + ', '.join(runtime_hooks))


def ensure_mcp(root, kicad_python, output, node_info):
    # Existing installations may have been obtained as a GitHub ZIP archive.
    # Validate their contents below without requiring or fabricating a .git dir.
    installed = clone_if_missing(MCP_URL, root) if not root.exists() or (root.is_dir() and not any(root.iterdir())) else False
    if not (root / "package.json").is_file() or not (root / "python" / "kicad_interface.py").is_file():
        raise RuntimeError(f"Incomplete KiCAD MCP source checkout: {root}")
    node = Path(node_info["node"])
    npm = Path(node_info["npm"])
    if not node.is_file() or not npm.is_file():
        raise RuntimeError("Configured Node.js executable/npm.cmd is missing")
    if int(run([node, "--version"]).stdout.strip().lstrip("v").split(".")[0]) < 18:
        raise RuntimeError("KiCAD MCP requires Node.js 18 or newer")
    dependencies_ok = run([node, "--input-type=module", "-e", "await import('@modelcontextprotocol/sdk/server/mcp.js'); await import('zod');"], cwd=root, check=False).returncode == 0
    built = False
    if not dependencies_ok or not (root / "dist" / "index.js").is_file():
        validate_mcp_build(root)
        npm_script = Path(node_info.get("npm_cli") or node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js")
        if not npm_script.is_file():
            raise RuntimeError("Cannot find npm-cli.js beside npm.cmd or node.exe")
        npm_env = os.environ.copy()
        # npm prepare/build scripts launch node/npm by name, even though npm
        # itself is invoked through an absolute executable. Scope PATH here.
        npm_env['PATH'] = str(node.parent) + os.pathsep + npm_env.get('PATH', '')
        owned_keys = {'NPM_CONFIG_USERCONFIG', 'NPM_CONFIG_CACHE', 'NPM_CONFIG_LOGS_DIR', 'NPM_CONFIG_PREFIX'}
        npm_env = {k: v for k, v in npm_env.items() if k.upper() not in owned_keys}
        npm_env.update(node_info.get('env', {}))
        npm_env.update({'NPM_CONFIG_USERCONFIG': str(node_info['npm_user_config']),
                        'NPM_CONFIG_CACHE': str(node_info['npm_cache']),
                        'NPM_CONFIG_LOGS_DIR': str(Path(node_info['config_root']) / 'logs')})
        # npm's generated tsc.cmd has an unquoted SET dp0=%~dp0, which breaks
        # valid Windows directories containing '&'. The upstream build is pure
        # TypeScript: avoid command-shell shims and invoke its JS entry directly.
        # Dev install hooks are not needed to build/run this MCP service.
        run([node, npm_script, "ci" if (root / "package-lock.json").is_file() else "install",
             '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=root, env=npm_env, timeout=900)
        compiler = root / 'node_modules/typescript/bin/tsc'
        if not compiler.is_file():
            raise RuntimeError('KiCAD MCP TypeScript compiler is missing after npm installation')
        run([node, compiler], cwd=root, env=npm_env, timeout=300)
        built = True
    python_status = probe_python(kicad_python, output)
    packages_installed = False
    pip_bootstrapped = False
    if not python_status["modules"]["pcbnew"]["passed"]:
        raise RuntimeError("The selected KiCad installation's Python cannot import pcbnew")
    if not python_status["passed"]:
        pip_bootstrapped = ensure_pip(kicad_python)
        run([kicad_python, "-m", "pip", "install", "--user", "-r", root / "requirements.txt"], timeout=900)
        packages_installed = True
        python_status = probe_python(kicad_python, output)
    write_json(output / "kicad-python-status.json", python_status)
    if not python_status["passed"]:
        raise RuntimeError("KiCad Python runtime requirements still fail after installation")
    version = json.loads((root / "package.json").read_text(encoding="utf-8-sig"))["version"]
    git = shutil.which("git")
    commit = run([git, "-C", root, "rev-parse", "HEAD"]).stdout.strip() if git and (root / ".git").exists() else None
    return node, {"path": str(root), "version": version, "commit": commit, "source_installed": installed,
                  "built": built, "python_packages_installed": packages_installed,
                  "pip_bootstrapped": pip_bootstrapped, "python": python_status,
                  "build_method": "direct_typescript_without_lifecycle_hooks" if built else "existing_dist"}


def configure_mcp_file(location, root, kicad_python, cli):
    """Use upstream --config to make the selected service directory effective."""
    configured = location.get('config_path')
    directory = location.get('config_root')
    path = Path(configured) if configured else Path(directory)/'config.json' if directory else root/'config'/'default-config.json'
    path = _plain_path(path)
    if path.is_file():
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict): raise RuntimeError('KiCad MCP configuration must be a JSON object: '+str(path))
        if data.get('logLevel','info') not in ('error','warn','info','debug'):
            raise RuntimeError('Unsupported logLevel in selected KiCad MCP config: '+str(path))
        created=False
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        write_json(path,{'name':'kicad-mcp-server','pythonPath':str(kicad_python),'kicadPath':str(cli),'logLevel':'warn'})
        created=True
    return {'config_path':str(path),'config_root':str(path.parent),'config_created':created,'config_sha256':sha256(path)}


def toml_string(text):
    # JSON basic strings are also TOML basic strings for these filesystem values.
    return json.dumps(str(text), ensure_ascii=False)


def register_mcp(codex_root, config_path, config, launch):
    servers = config.get("mcp_servers", {})

    def entrypoint(value):
        if not isinstance(value, dict) or not isinstance(value.get("args"), list):
            return None
        # Node's script is distinct from its script arguments. In particular,
        # a --config value must never identify an unrelated service as KiCad.
        script = None
        skip_value = False
        for argument in value["args"]:
            if skip_value:
                skip_value = False
                continue
            if not isinstance(argument, str):
                return None
            if argument in ("--config", "--require", "-r", "--import", "--loader", "--experimental-loader"):
                skip_value = True
                continue
            if argument.startswith("-"):
                continue
            if Path(argument).suffix.lower() in (".js", ".mjs", ".cjs"):
                script = Path(os.path.expandvars(os.path.expanduser(argument)))
                break
        if script is None:
            return None
        if not script.is_absolute():
            cwd = value.get("cwd")
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                return None
            script = Path(cwd) / script
        return os.path.normcase(os.path.normpath(str(script)))

    desired_entry = entrypoint(launch)
    if desired_entry is None:
        raise RuntimeError("Cannot identify the selected KiCad MCP entrypoint")
    matches = [key for key, value in servers.items() if entrypoint(value) == desired_entry]
    if len(matches) > 1:
        raise RuntimeError("Multiple MCP registrations point to the selected KiCad entrypoint; aliases were preserved: "
                           + ", ".join(matches))
    name = matches[0] if matches else None
    if name is None:
        name = "kicad"
        if name in servers:
            raise RuntimeError("An unrelated MCP server already uses the name 'kicad'; it was preserved")
    desired = {"command": launch["command"], "args": launch["args"], "cwd": launch["cwd"],
               "startup_timeout_sec": 180, "tool_timeout_sec": 240, "enabled": True,
               "env": launch["env"]}
    existing = servers.get(name, {})
    env_correct = all(existing.get("env", {}).get(k) == v for k, v in desired["env"].items())
    if env_correct and all(existing.get(k) == v for k, v in desired.items() if k != "env"):
        return {"name": name, "changed": False, "config_path": str(config_path), "backup": None}
    # Preserve extra server options and unrelated configuration. Only owned keys change.
    for key, value in existing.items():
        if key not in desired:
            desired[key] = value
    desired["env"] = {**existing.get("env", {}), **desired["env"]}
    original = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
    target_prefix = ("mcp_servers", name)
    lines = original.splitlines(keepends=True)
    kept = []
    keep = True
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            try:
                header = tomllib.loads(stripped)
                keys = []
                while isinstance(header, dict) and len(header) == 1:
                    key, header = next(iter(header.items()))
                    keys.append(key)
                keep = tuple(keys[:2]) != target_prefix
            except tomllib.TOMLDecodeError:
                pass
        if keep:
            kept.append(line)
    # Keep arbitrary existing keys through a small general TOML value serializer.
    def serialize(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return toml_string(value)
        if isinstance(value, list):
            return "[" + ", ".join(serialize(v) for v in value) + "]"
        if isinstance(value, dict):
            return "{ " + ", ".join(toml_string(k) + " = " + serialize(v) for k, v in value.items()) + " }"
        raise TypeError(f"Unsupported existing MCP setting type: {type(value).__name__}")
    section = "\n[mcp_servers." + toml_string(name) + "]\n"
    for key, value in desired.items():
        section += toml_string(key) + " = " + serialize(value) + "\n"
    revised = "".join(kept).rstrip() + "\n" + section
    parsed = tomllib.loads(revised)
    unaffected_before = {k: v for k, v in config.items() if k != "mcp_servers"}
    unaffected_after = {k: v for k, v in parsed.items() if k != "mcp_servers"}
    if unaffected_before != unaffected_after:
        raise RuntimeError("Refusing config update because unrelated settings changed")
    for key, value in servers.items():
        if key != name and parsed["mcp_servers"].get(key) != value:
            raise RuntimeError("Refusing config update because another MCP server changed")
    backup = None
    if config_path.exists():
        backup_dir = codex_root / "backups" / "ad2kicad"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / ("config-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".toml")
        shutil.copy2(config_path, backup)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_name("config.ad2kicad.tmp")
    temporary.write_text(revised, encoding="utf-8")
    os.replace(temporary, config_path)
    readback = read_toml(config_path)
    if readback["mcp_servers"][name] != desired:
        raise RuntimeError("Codex MCP configuration read-back mismatch")
    return {"name": name, "changed": True, "config_path": str(config_path), "backup": str(backup) if backup else None}


def setup(args):
    started = time.monotonic()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = {"passed": False, "timestamp": dt.datetime.now().astimezone().isoformat(),
             "workspace": str(args.workspace.resolve()), "stage": "preflight"}
    def progress(stage, message):
        state['stage'] = stage
        write_json(output / 'environment-state.json', state)
        print(message, flush=True)
    try:
        cli = args.kicad_cli.resolve()
        if not cli.is_file() or cli.name.lower() != "kicad-cli.exe":
            raise RuntimeError("A user-confirmed kicad-cli.exe is required")
        version = run([cli, "--version"]).stdout.strip()
        kicad_python = cli.with_name("python.exe")
        if not kicad_python.is_file():
            raise RuntimeError("KiCad bundled python.exe is missing beside the selected CLI")
        state["kicad"] = {"cli": str(cli), "version": version, "python": str(kicad_python)}
        codex_root = args.codex_root.resolve() if args.codex_root else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
        config_path = codex_root / "config.toml"
        config = read_toml(config_path)
        locations = inspect_tool_locations(codex_root, config, getattr(args, 'mcp_root', None), getattr(args, 'easyki_root', None), getattr(args, 'easyki_config_root', None),
                                           mcp_config_root=getattr(args, 'mcp_config_root', None), node_root=getattr(args, 'node_root', None),
                                           node_config_root=getattr(args, 'node_config_root', None))
        state['tool_locations'] = locations
        missing = [name for name in ('node', 'mcp', 'easyki') if locations[name]['needs_directory']]
        if missing:
            raise RuntimeError('Select custom installation/configuration directories before installing: '+', '.join(missing)+'. Use the setup window, or pass --node-root / --mcp-root / --easyki-root and their configuration roots.')
        for name in ('node','mcp','easyki'):
            if not locations[name]['installed'] and not locations[name].get('config_root'):
                raise RuntimeError('A new '+name+' installation requires an explicitly selected configuration directory (--node-config-root / --mcp-config-root / --easyki-config-root).')
        state['directory_preflight'] = validate_tool_plan(locations, output)
        footprint_root = cli.parent.parent / 'share' / 'kicad' / 'footprints'
        if not footprint_root.is_dir():
            raise RuntimeError('Selected KiCad has no standard footprint library directory. Install KiCad with its standard libraries.')
        run([kicad_python, '-c', 'import sys, pcbnew; assert sys.version_info >= (3, 11), "KiCad Python 3.11+ is required"; print(pcbnew.GetBuildVersion())'], timeout=60)
        # Save choices before network-dependent work. This record stores paths,
        # never claims a tool has installed successfully; every rerun probes it.
        state['selected_location_record'] = remember_tool_locations(codex_root, locations['mcp'], locations['easyki'], locations['node'])
        progress('node', '[1/5] Checking/installing Node.js and configuring npm...')
        node_status = ensure_node(Path(locations['node']['root']) if locations['node'].get('root') else None,
                                  Path(locations['node']['config_root']) if locations['node'].get('config_root') else None,
                                  output)
        state["node"] = node_status
        progress('kicad_happy', '[2/5] Checking/installing kicad-happy...')
        state["kicad_happy"] = check_happy(codex_root, output, args.smoke_schematic)
        progress('mcp', '[3/5] Checking/installing KiCAD MCP and Python dependencies...')
        mcp_root = Path(locations['mcp']['root'])
        node, mcp_status = ensure_mcp(mcp_root, kicad_python, output, node_status)
        mcp_status.update(configure_mcp_file(locations['mcp'], mcp_root, kicad_python, cli))
        state["mcp"] = mcp_status
        site_packages = cli.parent / "Lib" / "site-packages"
        if not site_packages.is_dir():
            site_packages = cli.parent.parent / "lib" / "python3" / "dist-packages"
        launch = {"command": str(node), "args": [str(mcp_root / "dist" / "index.js"), '--config', mcp_status['config_path']], "cwd": str(mcp_root), "env": {
            "KICAD_PYTHON": str(kicad_python), "KICAD_CLI": str(cli), "KICAD_BACKEND": "swig",
            "PYTHONPATH": str(site_packages), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "NODE_ENV": "production", "LOG_LEVEL": "warn"}}
        write_json(output / "mcp-launch.json", launch)
        probe_result = probe(launch, output / "mcp-probe.json", footprint_root)
        state["mcp"]["probe"] = {k: probe_result.get(k) for k in ("passed", "tool_count", "checks", "elapsed_seconds", "startup_retries", "error")}
        if not probe_result["passed"]:
            raise RuntimeError("KiCad MCP operational verification failed; see mcp-probe.json")
        progress('easyki', '[4/5] Checking/installing EasyKiConverter...')
        easy = locations['easyki']
        state['easykiconverter'] = ensure_easyki(Path(easy['root']), Path(easy['config_root']) if easy.get('config_root') else None,
                                               output, existing_config=Path(easy['existing_config']) if easy.get('existing_config') else None)
        progress('registration', '[5/5] Saving verified Codex MCP configuration...')
        state["codex_registration"] = register_mcp(codex_root, config_path, config, launch)
        state['tool_location_record'] = remember_tool_locations(codex_root, state['mcp'], state['easykiconverter'], node_status)
        state["passed"] = True
        state['stage'] = 'complete'
    except Exception as exc:
        state["error"] = str(exc)
    state["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_json(output / "environment-state.json", state)
    lines = ["# AD2KiCad environment run", "", "- Time: " + state["timestamp"], "- Passed: " + str(state["passed"]),
             "- Elapsed seconds: " + str(state["elapsed_seconds"]), "", "## Decisions and evidence", "",
             "1. The CLI path is supplied by explicit user confirmation or the file selector. KiCad is not installed by this script.",
             "2. Reuse all healthy installed kicad-happy skills; install missing skills only. Validate both analyzer schemas and the parser.",
             "3. Reuse the existing KiCAD-MCP-Server checkout and dependencies. Install/build only missing requirements.",
             "4. Use the selected KiCad installation's Python/pcbnew; use SWIG for unattended file operations without a GUI dependency.",
             "5. Verify MCP initialize, tools/list and a real read-only Python-backed footprint query before registration.",
             "6. Preserve unrelated Codex TOML settings. Keep backups under the user's Codex backups directory, never in the project.",
             "7. Restart/reload the Codex MCP connection to expose newly registered tools in a new session.", "",
             "8. Reuse the detected EasyKiConverter; when absent, install the official Windows portable release in the user's selected directory. Keep custom config/cache/log locations and verify native CLI startup.",
             "9. Node.js is reused when healthy; otherwise the official Windows LTS ZIP is installed into the selected directory. npm cache/config/logs stay under the selected Node.js configuration directory.",
             "10. Missing Node.js, MCP and EasyKiConverter installations require explicit directory selection. Persist only paths in the user's ad2kicad-tools.json for repeat runs.", "",
             "## Results", "", "```json", json.dumps(state, ensure_ascii=False, indent=2), "```", "",
             "## Sources", "", "- https://github.com/aklofas/kicad-happy", "- https://github.com/mixelpixx/KiCAD-MCP-Server",
             "- https://developers.openai.com/codex/mcp", "- https://github.com/EasyKiconverter/EasyKiConverter/", ""]
    (output / "ENVIRONMENT-PROCESS.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"passed": state["passed"], "elapsed_seconds": state["elapsed_seconds"], "report": str(output / "environment-state.json"), "error": state.get("error")}, ensure_ascii=False))
    return 0 if state["passed"] else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--kicad-cli", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--codex-root", type=Path)
    p.add_argument("--mcp-root", type=Path)
    p.add_argument("--mcp-config-root", type=Path)
    p.add_argument("--easyki-root", type=Path)
    p.add_argument("--easyki-config-root", type=Path)
    p.add_argument("--node-root", type=Path)
    p.add_argument("--node-config-root", type=Path)
    p.add_argument("--inspect-only", action='store_true')
    p.add_argument("--validate-plan", action='store_true')
    p.add_argument("--smoke-schematic", type=Path)
    args=p.parse_args()
    if args.inspect_only or args.validate_plan:
        try:
            codex_root=args.codex_root.resolve() if args.codex_root else Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).resolve()
            locations = inspect_tool_locations(codex_root,read_toml(codex_root/'config.toml'),args.mcp_root,args.easyki_root,args.easyki_config_root,
                                               mcp_config_root=args.mcp_config_root, node_root=args.node_root,
                                               node_config_root=args.node_config_root)
            if args.validate_plan:
                result = validate_tool_plan(locations, args.output)
                result['locations'] = locations
            else:
                result = locations
            print(json.dumps(result,ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False))
            raise SystemExit(1)
    else:
        raise SystemExit(setup(args))
