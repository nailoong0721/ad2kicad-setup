"""Restore a failed AD2KiCad publish without discarding its partial output.

Only projects without a completed report in either metadata layout may be recovered. Select the exact
run directory containing input/ and audit/input_hashes.json. No recursive
deletion is used: generated library/sheet directories are renamed into the
recovery snapshot, and all overwritten root files are copied there first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid
from project_artifacts import ARTIFACT_NAMES,artifact_path


class RecoveryError(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plain_absolute(path):
    path = Path(path)
    if '..' in path.parts:
        raise RecoveryError('Parent traversal is not supported in recovery paths')
    return Path(os.path.abspath(os.fspath(path)))


def reject_links(path):
    """Inspect lexical ancestors before resolve can hide a symlink/junction."""
    path = plain_absolute(path)
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RecoveryError('Refusing symlink or reparse point: ' + str(item))


def assert_below(path, root):
    path = plain_absolute(path)
    root = plain_absolute(root)
    reject_links(root)
    reject_links(path)
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise RecoveryError('Path escapes intended root: ' + str(path)) from None
    if not relative.parts:
        raise RecoveryError('Path must be strictly below intended root: ' + str(path))
    return path


def scan_tree(path):
    """Reject links before descent and return regular-file SHA-256 inventory."""
    reject_links(path)
    result = {}
    pending = [Path(path)]
    while pending:
        current = pending.pop()
        for child in current.iterdir():
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise RecoveryError('Refusing linked tree entry: ' + str(child))
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                result[child.relative_to(path).as_posix()] = digest(child)
            else:
                raise RecoveryError('Refusing non-regular tree entry: ' + str(child))
    return result


def write_json(path, value, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def require_no_completed_report(project, message):
    """A completed marker in either supported layout protects the project."""
    for marker in (project/'AD2KICAD_REPORT.json',artifact_path(project,'AD2KICAD_REPORT.json')):
        reject_links(marker)
        if marker.exists():
            raise RecoveryError(message)


def generated_report_paths(project):
    """Enumerate explicit metadata files, never the entire work directory."""
    relatives=set()
    for name in ARTIFACT_NAMES:
        relatives.add(name)
        relatives.add(artifact_path(project,name).relative_to(project).as_posix())
    for directory in (project,project/'_ad2kicad_work'):
        reject_links(directory)
        if directory.is_dir():
            for file in directory.glob('AD2KICAD_REPAIR-*.md'):
                relatives.add(file.relative_to(project).as_posix())
    return sorted(relatives)


def validate_inputs(project, run_directory):
    project = plain_absolute(project)
    reject_links(project)
    if not project.is_dir():
        raise RecoveryError('Project directory does not exist')
    if project.name.casefold() == 'ad2kicad_original_prj':
        raise RecoveryError('The original project is protected; recover a copy only')
    require_no_completed_report(project,'Completed project cannot be recovered by this failed-run command')
    run_directory = Path(run_directory)
    if not run_directory.is_absolute():
        run_directory = project / run_directory
    run_directory = assert_below(run_directory, project / '_ad2kicad_work')
    if not run_directory.is_dir():
        raise RecoveryError('Run directory does not exist')
    # Resolve only after rejecting the lexical ancestor chain.
    project = project.resolve(strict=True)
    run_directory = run_directory.resolve(strict=True)
    if project.name.casefold() == 'ad2kicad_original_prj':
        raise RecoveryError('The original project is protected; recover a copy only')
    assert_below(run_directory, project / '_ad2kicad_work')
    manifest = run_directory / 'audit' / 'input_hashes.json'
    source = run_directory / 'input'
    reject_links(manifest)
    reject_links(source)
    if not manifest.is_file() or not source.is_dir():
        raise RecoveryError('Selected run lacks audit/input_hashes.json or input/')
    hashes = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(hashes, dict) or not hashes:
        raise RecoveryError('Input hash manifest must be a nonempty object')
    approved_suffixes = {'.kicad_sch', '.kicad_pcb', '.kicad_pro', '.schlib', '.pcblib', '.intlib'}
    folded_names = set()
    for name, expected in hashes.items():
        if (not isinstance(name, str) or not name or name in {'.', '..'}
                or Path(name).name != name or any(char in name for char in '/\\:*?"<>|')
                or name.rstrip(' .') != name):
            raise RecoveryError('Unsafe snapshot filename: ' + repr(name))
        if name.casefold() in folded_names:
            raise RecoveryError('Case-colliding snapshot filenames')
        folded_names.add(name.casefold())
        if Path(name).suffix.lower() not in approved_suffixes and name not in {'sym-lib-table', 'fp-lib-table'}:
            raise RecoveryError('Unexpected snapshot file type: ' + name)
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise RecoveryError('Invalid SHA-256 for ' + name)
        original = assert_below(source / name, source)
        if not original.is_file() or digest(original) != expected:
            raise RecoveryError('Recovery source hash mismatch: ' + name)
        target = assert_below(project / name, project)
        if target.exists() and not target.is_file():
            raise RecoveryError('Root file path is occupied by a directory: ' + name)
    pro = [name for name in hashes if Path(name).suffix == '.kicad_pro']
    pcb = [name for name in hashes if Path(name).suffix == '.kicad_pcb']
    if len(pro) != 1 or len(pcb) != 1 or Path(pro[0]).stem != Path(pcb[0]).stem:
        raise RecoveryError('Snapshot must contain one matching project and PCB')
    if Path(pro[0]).stem + '.kicad_sch' not in hashes:
        raise RecoveryError('Snapshot lacks its matching top-level schematic')
    for name in ('00_sheet', '01_Lib'):
        generated = assert_below(project / name, project)
        if generated.exists():
            if not generated.is_dir():
                raise RecoveryError('Generated directory path is occupied by a file: ' + name)
            scan_tree(generated)
    return project, run_directory, source, hashes


def recover(project, run_directory):
    project, run_directory, source, hashes = validate_inputs(project, run_directory)
    completed = run_directory / 'RECOVERY_COMPLETE.json'
    reject_links(completed)
    if completed.exists():
        previous = json.loads(completed.read_text(encoding='utf-8'))
        if previous.get('restored_hashes') != hashes:
            raise RecoveryError('Previous recovery manifest differs from source snapshot')
        if any((project / name).exists() for name in ('00_sheet', '01_Lib')):
            raise RecoveryError('Recovered project has a new partial publish; choose its new run directory')
        if any((project/name).exists() for name in generated_report_paths(project)):
            raise RecoveryError('Recovered project has new generated reports; choose its new run directory')
        if any(not (project / name).is_file() or digest(project / name) != expected for name, expected in hashes.items()):
            raise RecoveryError('Recovered originals changed; refusing to overwrite later work')
        return {**previous, 'status': 'already_recovered_verified'}

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]
    snapshot = assert_below(run_directory / ('recovery_' + stamp), run_directory)
    snapshot.mkdir(exist_ok=False)
    partial = snapshot / 'partial_root_files'
    partial.mkdir()
    moved = snapshot / 'generated_directories'
    moved.mkdir()
    report_path = snapshot / 'recovery_report.json'
    report = {'schema_version': 1, 'status': 'preparing', 'project': str(project),
              'run_directory': str(run_directory), 'recovery_snapshot': str(snapshot),
              'current_file_backups': {}, 'moved_directories': {}, 'archived_reports': {}, 'restored_hashes': {}}
    write_json(report_path, report, exclusive=True)
    try:
        # Preserve all root files the operation may overwrite, plus generated
        # reports for diagnosis. Unlisted project files remain untouched.
        generated_reports=generated_report_paths(project)
        names = set(hashes) | set(generated_reports)
        before = {}
        for name in sorted(names):
            current = assert_below(project / name, project)
            if current.exists():
                if not current.is_file():
                    raise RecoveryError('Cannot snapshot non-file root entry: ' + name)
                checksum = digest(current)
                backup = partial / name
                backup.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(current, backup)
                if digest(backup) != checksum or digest(current) != checksum:
                    raise RecoveryError('Root file changed during snapshot: ' + name)
                before[name] = checksum
                report['current_file_backups'][name] = checksum
        report['status'] = 'snapshot_complete'
        write_json(report_path, report)
        require_no_completed_report(project,'Completed marker appeared before recovery; no published data moved')
        for name in ('00_sheet', '01_Lib'):
            current = assert_below(project / name, project)
            if not current.exists():
                continue
            inventory = scan_tree(current)
            destination = assert_below(moved / name, snapshot)
            if destination.exists():
                raise RecoveryError('Recovery destination collision: ' + str(destination))
            # Same-volume rename after validating both ancestor chains and the
            # entire source tree. No recursive deletion or traversal move.
            reject_links(current)
            reject_links(destination)
            current.rename(destination)
            if scan_tree(destination) != inventory:
                raise RecoveryError('Moved directory verification failed: ' + name)
            report['moved_directories'][name] = {'path': str(destination), 'file_hashes': inventory}
            write_json(report_path, report)
        # Archive only identified partial reports after their exact bytes have
        # been copied. Input snapshots and all unrelated work history stay put.
        for name in generated_reports:
            current=assert_below(project/name,project)
            if not current.exists():
                if name in before: raise RecoveryError('Generated report disappeared after snapshot: '+name)
                continue
            if name not in before or not current.is_file() or digest(current)!=before[name]:
                raise RecoveryError('Generated report changed after snapshot: '+name)
            destination=assert_below(snapshot/'generated_reports'/name,snapshot)
            destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists(): raise RecoveryError('Generated report archive collision: '+name)
            reject_links(current);reject_links(destination)
            current.rename(destination)
            if digest(destination)!=before[name]: raise RecoveryError('Archived report hash mismatch: '+name)
            report['archived_reports'][name]={'path':str(destination),'sha256':before[name]}
            write_json(report_path,report)
        for name, expected in hashes.items():
            original = assert_below(source / name, source)
            target = assert_below(project / name, project)
            if digest(original) != expected:
                raise RecoveryError('Recovery input changed before restore: ' + name)
            if target.exists() and (name not in before or digest(target) != before[name]):
                raise RecoveryError('Root file changed after snapshot: ' + name)
            if not target.exists() and name in before:
                raise RecoveryError('Root file disappeared after snapshot: ' + name)
            staging = assert_below(snapshot / ('.ad2kicad_restore_' + uuid.uuid4().hex + '.tmp'), snapshot)
            with original.open('rb') as stream, staging.open('xb') as sink:
                shutil.copyfileobj(stream, sink)
                sink.flush()
                os.fsync(sink.fileno())
            if digest(staging) != expected:
                raise RecoveryError('Staged original hash mismatch: ' + name)
            reject_links(target)
            os.replace(staging, target)
            if digest(target) != expected:
                raise RecoveryError('Restored original hash mismatch: ' + name)
            report['restored_hashes'][name] = expected
            report['status'] = 'restoring'
            write_json(report_path, report)
        if any((project / name).exists() for name in ('00_sheet', '01_Lib')):
            raise RecoveryError('New generated directory appeared during recovery')
        require_no_completed_report(project,'Completed marker appeared during recovery')
        if any((project/name).exists() for name in generated_report_paths(project)):
            raise RecoveryError('New generated report appeared during recovery')
        report['status'] = 'recovered_verified'
        report['flat_layout_ready_for_setup'] = True
        report['no_files_deleted'] = True
        write_json(report_path, report)
        write_json(completed, report, exclusive=True)
        return report
    except BaseException as error:
        report['status'] = 'recovery_incomplete'
        report['error'] = str(error)
        write_json(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-directory', type=Path, required=True)
    args = parser.parse_args()
    result = recover(args.project, args.run_directory)
    print(json.dumps({key: result[key] for key in ('status', 'project', 'recovery_snapshot',
                     'flat_layout_ready_for_setup', 'no_files_deleted')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
