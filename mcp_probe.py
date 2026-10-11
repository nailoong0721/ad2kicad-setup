"""Read-only stdio MCP probe, using newline-delimited JSON-RPC (no shell)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


def probe(config: dict, output: Path, search_path: Path, timeout: int = 240) -> dict:
    started = time.monotonic()
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in config.get("env", {}).items()})
    proc = subprocess.Popen(
        [config["command"], *config.get("args", [])],
        cwd=config.get("cwd") or None, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    messages = queue.Queue()
    stderr = []
    non_json = []
    result = {"passed": False, "command": config["command"], "checks": []}

    def read_stdout():
        for line in proc.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                non_json.append(line.rstrip())
        messages.put({"eof": True})

    def read_stderr():
        for line in proc.stderr:
            stderr.append(line.rstrip())

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    request_id = 0

    def send(method, params, expect=True):
        nonlocal request_id
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if expect:
            request_id += 1
            message["id"] = request_id
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        if not expect:
            return None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"MCP {method} timed out after {timeout}s")
            try:
                response = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"MCP {method} timed out after {timeout}s") from exc
            if response.get("eof"):
                raise RuntimeError(f"MCP ended before {method}: exit={proc.poll()}")
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(f"MCP {method}: {response['error']}")
                return response["result"]

    try:
        initialized = send("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "AD2KiCad-environment-verifier", "version": "1.0.0"},
        })
        result["initialize"] = initialized
        result["checks"].append({"name": "initialize", "passed": True})
        send("notifications/initialized", {}, expect=False)
        tools = []
        cursor = None
        while True:
            listed = send("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(listed.get("tools", []))
            cursor = listed.get("nextCursor")
            if not cursor:
                break
        names = [item["name"] for item in tools]
        result["tool_count"] = len(names)
        result["tool_names"] = names
        if not names:
            raise RuntimeError("MCP returned no tools")
        result["checks"].append({"name": "tools/list", "passed": True, "count": len(names)})
        name = "list_footprint_libraries"
        if name not in names:
            raise RuntimeError(f"Required read-only tool is absent: {name}")
        # This upstream version exposes the stdio catalog before Python starts.
        # Retry only its explicit startup race; other tool failures fail closed.
        readonly_started = time.monotonic()
        startup_retries = 0
        while True:
            readonly = send("tools/call", {"name": name, "arguments": {"searchPaths": [str(search_path)]}})
            startup_message = "\n".join(item.get("text", "") for item in readonly.get("content", []))
            if not (readonly.get("isError") and "Python process for KiCAD scripting is not running" in startup_message):
                break
            if time.monotonic() - readonly_started > timeout:
                break
            startup_retries += 1
            time.sleep(min(startup_retries, 5))
        result["startup_retries"] = startup_retries
        result["readonly_tool"] = {"name": name, "search_path": str(search_path), "result": readonly}
        if readonly.get("isError"):
            raise RuntimeError("Read-only MCP tool returned isError")
        texts = [item.get("text", "") for item in readonly.get("content", []) if item.get("type") == "text"]
        payload = json.loads("\n".join(texts))
        if payload.get("success") is not True:
            raise RuntimeError(f"Read-only Python backend query failed: {payload}")
        libraries = payload.get("libraries", [])
        if not libraries:
            raise RuntimeError("Read-only Python backend found no footprint libraries")
        result["checks"].append({"name": name, "passed": True, "library_count": len(libraries)})
        result["passed"] = True
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["server_exit_code"] = proc.returncode
        result["stdout_non_json"] = non_json
        result["stderr_tail"] = stderr[-35:]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--search-path", type=Path, required=True)
    args = parser.parse_args()
    report = probe(json.loads(args.config.read_text(encoding="utf-8-sig")), args.output, args.search_path)
    print(json.dumps({key: report[key] for key in ("passed", "checks", "elapsed_seconds")}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
