"""Acquire the two official source trees without making Git a prerequisite.

An existing checkout is reused by its required files, including ZIP checkouts.
New sources are validated in a sibling staging directory before publication.
Download hashes are local integrity records, not upstream signature validation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
import zipfile

from easyki_setup import _plain_path, safe_extract


HAPPY_URL = "https://github.com/aklofas/kicad-happy.git"
MCP_URL = "https://github.com/mixelpixx/KiCAD-MCP-Server.git"
SOURCE_RECORD = ".ad2kicad-source.json"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 768 * 1024 * 1024
MAX_FILES = 30000
NETWORK_TIMEOUT = 60
DOWNLOAD_DEADLINE = 600
SKILLS = ("kicad", "spice", "emc", "datasheets", "bom", "digikey", "mouser",
          "lcsc", "element14", "jlcpcb", "pcbway")
REPOSITORIES = {
    HAPPY_URL: ("aklofas/kicad-happy", tuple("skills/" + name + "/SKILL.md" for name in SKILLS)
                + tuple("skills/kicad/scripts/" + name for name in
                        ("analyze_schematic.py", "analyze_pcb.py", "sexp_parser.py"))),
    MCP_URL: ("mixelpixx/KiCAD-MCP-Server", ("package.json", "requirements.txt",
                                          "python/kicad_interface.py", "src/index.ts", "tsconfig.json")),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_files(root: Path, required: tuple[str, ...]) -> list[str]:
    missing = []
    for relative in required:
        candidate = _plain_path(root / relative)
        if not candidate.is_file():
            missing.append(relative)
    return missing


def _target_is_empty(target: Path) -> None:
    _plain_path(target, writable=True)
    if target.exists() and (not target.is_dir() or next(target.iterdir(), None) is not None):
        raise RuntimeError(f"Source directory is nonempty or is not a directory; it was preserved: {target}")


def _tree_sha256(root: Path) -> str:
    """Hash source bytes with their relative names; reject links before reading."""
    rows = []
    total = 0
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            candidate = Path(current) / name
            attributes = candidate.lstat()
            if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & 0x400:
                raise RuntimeError(f"Source contains a link or reparse point: {candidate}")
        directories[:] = [name for name in directories if not (Path(current) == root and name == ".git")]
        for name in files:
            candidate = Path(current) / name
            if candidate == root / SOURCE_RECORD:
                continue
            total += candidate.stat().st_size
            if len(rows) >= MAX_FILES or total > MAX_EXPANDED_BYTES:
                raise RuntimeError("Source tree exceeds the supported size/file limit")
            rows.append((candidate.relative_to(root).as_posix(), _sha256(candidate)))
    digest = hashlib.sha256()
    for relative, checksum in sorted(rows):
        digest.update(relative.encode("utf-8") + b"\0" + checksum.encode("ascii") + b"\n")
    return digest.hexdigest()


def _latest_commit(repository: str) -> str:
    # Omitting sha means the default branch, per GitHub's List commits API.
    url = f"https://api.github.com/repos/{repository}/commits?per_page=1"
    request = urllib.request.Request(url, headers={"User-Agent": "AD2KiCad-Setup",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
        if response.geturl() != url:
            raise RuntimeError("The official GitHub commit request redirected unexpectedly")
        chunks, received, started = [], 0, time.monotonic()
        read_chunk = getattr(response, 'read1', response.read)
        while chunk := read_chunk(65536):
            received += len(chunk)
            if received > 2 * 1024 * 1024 or time.monotonic() - started > 90:
                raise RuntimeError('GitHub commit metadata exceeds its size/time limit')
            chunks.append(chunk)
        body = b''.join(chunks)
    if len(body) > 2 * 1024 * 1024:
        raise RuntimeError("GitHub commit metadata exceeds the supported limit")
    value = json.loads(body.decode("utf-8"))
    commit = value[0].get("sha") if isinstance(value, list) and value and isinstance(value[0], dict) else None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise RuntimeError("GitHub returned no valid default-branch commit SHA")
    return commit.lower()


def _download_archive(repository: str, commit: str, archive: Path) -> str:
    url = f"https://codeload.github.com/{repository}/zip/{commit}"
    request = urllib.request.Request(url, headers={"User-Agent": "AD2KiCad-Setup"})
    received = 0
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response, archive.open("xb") as destination:
        if response.geturl() != url:
            raise RuntimeError("The official source archive request redirected unexpectedly")
        length = response.headers.get("Content-Length")
        expected = int(length) if length is not None else None
        if expected is not None and not 0 < expected <= MAX_ARCHIVE_BYTES:
            raise RuntimeError("GitHub source archive size is outside the supported limit")
        read_chunk = getattr(response, 'read1', response.read)
        while chunk := read_chunk(1024 * 1024):
            received += len(chunk)
            if received > MAX_ARCHIVE_BYTES or time.monotonic() - started > DOWNLOAD_DEADLINE:
                raise RuntimeError("GitHub source archive exceeds the size/time limit")
            destination.write(chunk)
    if received == 0 or (expected is not None and received != expected):
        raise RuntimeError("GitHub source archive download was incomplete")
    return url


def _skill_alias_target(relative: str, value: str, repository: str) -> str | None:
    """Recognize only the upstream aliases whose real skill files we install."""
    parts = PurePosixPath(relative).parts
    if (repository == "aklofas/kicad-happy" and len(parts) == 3
            and parts[0] in (".agents", ".claude") and parts[1] == "skills"
            and parts[2] in SKILLS and value == "../../skills/" + parts[2]):
        return "skills/" + parts[2] + "/SKILL.md"
    return None


def _extract_archive(archive: Path, destination: Path, repository: str, commit: str,
                     skipped_links: list | None = None) -> Path:
    expected = repository.split("/")[1] + "-" + commit
    skipped = []
    filtered = None
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if (not entries or len(entries) > MAX_FILES
                or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES):
            raise RuntimeError("Source archive exceeds the supported file/expanded size limit")
        names = {}
        for item in entries:
            parts = PurePosixPath(item.filename.replace("\\", "/")).parts
            if not parts or parts[0].casefold() != expected.casefold():
                raise RuntimeError("Source archive does not match its pinned repository/commit root")
            key = "/".join(parts).casefold()
            if key in names:
                raise RuntimeError("Duplicate Windows source ZIP member: " + item.filename)
            names[key] = item
        for item in entries:
            if not stat.S_ISLNK((item.external_attr >> 16) & 0xffff):
                continue
            relative = "/".join(PurePosixPath(item.filename.replace("\\", "/")).parts[1:])
            value = bundle.read(item).decode("utf-8") if item.file_size <= 4096 else ""
            target = _skill_alias_target(relative, value, repository)
            actual = names.get((expected + "/" + target).casefold()) if target else None
            if (actual is None or actual.is_dir() or stat.S_ISLNK((actual.external_attr >> 16) & 0xffff)
                    or actual.external_attr & 0x400
                    or any(name.startswith(item.filename.casefold().rstrip("/") + "/") for name in names)):
                raise RuntimeError("Unsafe ZIP symbolic link: " + item.filename)
            skipped.append({"path": relative, "target": value})
        if skipped:
            omitted = {expected.casefold() + "/" + item["path"].casefold() for item in skipped}
            filtered = destination.parent / "source-without-aliases.zip"
            with zipfile.ZipFile(filtered, "x", compression=zipfile.ZIP_DEFLATED) as sanitized:
                for item in entries:
                    if item.filename.replace("\\", "/").casefold() in omitted:
                        continue
                    with bundle.open(item) as source, sanitized.open(item, "w") as sink:
                        shutil.copyfileobj(source, sink)
    # Validates every member before extraction, including Windows reserved names,
    # traversal, duplicates, symlinks, reparse attributes and alternate streams.
    safe_extract(filtered or archive, destination)
    if filtered:
        filtered.unlink()
    children = list(destination.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        raise RuntimeError("Expected one source directory in the GitHub archive")
    if skipped_links is not None:
        skipped_links.extend(skipped)
    return children[0]


def _omit_git_skill_aliases(root: Path, repository: str) -> list[dict]:
    """Git on Windows may materialize symlinks as ordinary tiny text files."""
    skipped = []
    if repository != "aklofas/kicad-happy":
        return skipped
    for directory in (".agents", ".claude"):
        parent = _plain_path(root / directory / "skills")
        if not parent.is_dir():
            continue
        for name in SKILLS:
            candidate = parent / name
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                value = os.readlink(candidate)
            elif stat.S_ISREG(info.st_mode) and info.st_size <= 4096:
                value = candidate.read_text(encoding="utf-8")
            else:
                continue
            relative = candidate.relative_to(root).as_posix()
            target = _skill_alias_target(relative, value, repository)
            if target and _plain_path(root / target).is_file():
                candidate.unlink()  # Delete the alias itself; never its target.
                skipped.append({"path": relative, "target": value})
    return skipped


def _git_checkout(git: str, url: str, checkout: Path) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    options = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
               "env": env, "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}
    result = subprocess.run([git, "clone", "--quiet", "--depth", "1", "--no-tags",
                             "--no-recurse-submodules", url, str(checkout)], timeout=600, **options)
    if result.returncode:
        raise RuntimeError("Official source Git clone failed: " + result.stderr[-1800:])
    result = subprocess.run([git, "-C", str(checkout), "rev-parse", "HEAD"], timeout=30, **options)
    commit = result.stdout.strip().lower()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Cannot verify the cloned source commit SHA")
    return commit


def ensure_source(url: str, target: Path) -> bool:
    """Return True for a new install, False for a reusable existing source tree.

    Only HAPPY_URL and MCP_URL are accepted. Failures retain staging evidence
    and never overwrite an existing nonempty destination.
    """
    if url not in REPOSITORIES:
        raise ValueError("Only the two official AD2KiCad source repository URLs are allowed")
    repository, required = REPOSITORIES[url]
    target = _plain_path(Path(target))
    if target.is_dir() and not _missing_files(target, required):
        return False
    _target_is_empty(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ad2kicad-source-", dir=target.parent))
    try:
        git = shutil.which("git")
        archive_hash = None
        skipped_links = []
        if git:
            source = staging / "checkout"
            commit = _git_checkout(git, url, source)
            skipped_links = _omit_git_skill_aliases(source, repository)
            download_url = url
            method = "git_shallow_clone"
        else:
            commit = _latest_commit(repository)
            archive = staging / "source.zip"
            download_url = _download_archive(repository, commit, archive)
            archive_hash = _sha256(archive)
            source = _extract_archive(archive, staging / "unpacked", repository, commit, skipped_links)
            method = "github_commit_zip"
        missing = _missing_files(source, required)
        if missing:
            raise RuntimeError("Official source is incomplete: " + ", ".join(missing))
        record = {"version": 1, "repository": repository, "commit": commit,
                  "repository_url": url, "download_url": download_url, "method": method,
                  "archive_sha256": archive_hash, "source_tree_sha256": _tree_sha256(source),
                  "skipped_internal_skill_aliases": skipped_links,
                  "integrity_note": "SHA-256 values record downloaded/local bytes; no upstream signature verification is claimed."}
        marker = _plain_path(source / SOURCE_RECORD, writable=True)
        marker.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _target_is_empty(target)
        if target.exists():
            target.rmdir()  # Empty-only deletion; a concurrent new file fails closed.
        source.rename(target)
    except Exception as exc:
        raise RuntimeError(f"Source setup failed; staging evidence retained at {staging}: {exc}") from exc
    # Only remove known files and empty directories from our private staging.
    # Never recursively delete a downloaded/cloned tree during error handling.
    for relative in ("source.zip", "unpacked"):
        item = staging / relative
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            item.rmdir()
    staging.rmdir()
    return True
