"""Keep conversion bookkeeping under _ad2kicad_work, with legacy migration."""
import hashlib
import json
from pathlib import Path
import re
import stat
import time

WORK_DIR = '_ad2kicad_work'
ARTIFACT_NAMES = tuple('AD2KICAD_'+name for name in (
    'REPORT.json','MAPPING.json','MISSING_3D.json','FOOTPRINT_VARIANTS.json',
    'FOOTPRINT_INDEX.md','STANDALONE_PADS.json','SETUP_RUN.json'))


def _check_path(path):
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Refusing linked artifact path: '+str(item))


def artifact_path(root, name):
    if Path(name).name != name or not name.startswith('AD2KICAD_') or Path(name).suffix not in ('.json','.md'):
        raise ValueError('Invalid conversion artifact name: '+str(name))
    path = Path(root) / WORK_DIR / name
    _check_path(path)
    return path


def find_artifact(root, name):
    """Read a canonical artifact, falling back to an unmigrated legacy file."""
    current = artifact_path(root,name)
    legacy = Path(root)/name
    _check_path(legacy)
    for path in (current,legacy):
        if path.exists() and not path.is_file():
            raise ValueError('Artifact path is not a regular file: '+str(path))
    if current.is_file() and legacy.is_file() and current.read_bytes()!=legacy.read_bytes():
        raise RuntimeError('Conflicting canonical and legacy artifacts; preserve both and review: '+name)
    return current if current.exists() or not legacy.exists() else legacy


def legacy_artifact_names(root):
    names = set(ARTIFACT_NAMES)
    names.update(p.name for p in Path(root).glob('AD2KICAD_REPAIR-*.md'))
    return tuple(sorted(name for name in names if (Path(root)/name).exists()))


def relocate_legacy_artifacts(root, verify_output_hashes, verify_only=False):
    """Move only tool-owned reports, preserving exact originals before changes.

    All file/hash paths inside reports stay relative to the project root.
    No project geometry, library, input snapshot or run directory is moved.
    """
    root = Path(root).absolute()
    _check_path(root)
    names = legacy_artifact_names(root)
    if not names:
        return []
    if verify_only:
        raise RuntimeError('Legacy reports remain at project root; run normal setup once to move them into '+WORK_DIR)
    marker = find_artifact(root,'AD2KICAD_REPORT.json')
    report = json.loads(marker.read_text(encoding='utf-8-sig')) if marker.is_file() else None
    if report is not None:
        verify_output_hashes(root,report)
    originals = {}
    existing = {}
    for name in names:
        source = root/name
        target = artifact_path(root,name)
        find_artifact(root,name)  # Reject conflicts before making a backup or change.
        if not source.is_file():
            raise ValueError('Legacy artifact is not a regular file: '+str(source))
        originals[name] = source.read_bytes()
        existing[name] = target.read_bytes() if target.is_file() else None
    canonical_marker = artifact_path(root,'AD2KICAD_REPORT.json')
    if report is not None and canonical_marker.name not in existing:
        existing[canonical_marker.name] = canonical_marker.read_bytes() if canonical_marker.is_file() else None
    backup = root/WORK_DIR/('artifact-relocation-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1000000))
    backup.mkdir(parents=True,exist_ok=False)
    (backup/'legacy-root').mkdir()
    (backup/'previous-work').mkdir()
    for name,data in originals.items():
        (backup/'legacy-root'/name).write_bytes(data)
    for name,data in existing.items():
        if data is not None: (backup/'previous-work'/name).write_bytes(data)
    planned = dict(originals)
    index = 'AD2KICAD_FOOTPRINT_INDEX.md'
    if index in planned:
        text = planned[index].decode('utf-8-sig')
        planned[index] = re.sub(r'(?<=\]\()01_Lib/', '../01_Lib/', text).encode('utf-8')
    changes = [{'file':name,'from':name,'to':WORK_DIR+'/'+name,
                'original_sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(originals.items())]
    if report is not None:
        hashes = {}
        for relative,checksum in report['output_hashes'].items():
            key = WORK_DIR+'/'+relative if relative in names else relative
            hashes[key] = hashlib.sha256(planned[relative]).hexdigest() if relative in planned else checksum
        report['output_hashes'] = hashes
        report['artifact_directory'] = WORK_DIR
        report['artifact_layout_version'] = 1
        report['artifact_relocation'] = {'backup':backup.relative_to(root).as_posix(),'files':changes}
        planned[canonical_marker.name] = (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    journal = {'status':'prepared','files':changes,'backup':backup.relative_to(root).as_posix()}
    journal_path = backup/'relocation.json'
    def save_journal():
        journal_path.write_text(json.dumps(journal,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save_journal()
    try:
        for name,data in planned.items():
            target = artifact_path(root,name)
            target.write_bytes(data)
            if target.read_bytes()!=data: raise RuntimeError('Artifact copy verification failed: '+name)
        if report is not None: verify_output_hashes(root,report)
        for name,data in originals.items():
            if (root/name).read_bytes()!=data: raise RuntimeError('Legacy artifact changed during relocation: '+name)
            if (backup/'legacy-root'/name).read_bytes()!=data: raise RuntimeError('Artifact backup verification failed: '+name)
        for name in originals:
            (root/name).unlink()
        journal['status']='completed_verified'
        save_journal()
    except BaseException:
        # Retain the backup/journal. Restore only files this transaction owns.
        for name,data in originals.items():
            source=root/name
            if source.exists() and source.read_bytes()!=data:
                raise RuntimeError('Concurrent artifact edit; manual recovery needed at '+str(backup))
            if not source.exists(): source.write_bytes(data)
        for name,data in existing.items():
            target=artifact_path(root,name)
            if data is None:
                if target.is_file(): target.unlink()
            else:
                target.write_bytes(data)
        journal['status']='rolled_back'
        save_journal()
        raise
    return changes
