"""Upgrade a verified v1 project to root schematic + readable footprint names.

Rebuild from the hash-verified original snapshot in an isolated candidate, then
preserve v1 as a full backup and publish only the validated managed files.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from organize_project import check_nickname,digest,report_nickname,safe_tree,validate_layout,verify_output_hashes,write_json
from project_artifacts import ARTIFACT_NAMES,artifact_path,find_artifact,relocate_legacy_artifacts


def asset_inventory(project, root_names):
    result={}
    for name in ('00_sheet','01_Lib'):
        directory=project/name
        safe_tree(directory)
        if not directory.is_dir(): raise RuntimeError('Missing asset directory: '+str(directory))
        result[name]='DIRECTORY'
        for path in directory.rglob('*'):
            result[path.relative_to(project).as_posix()]=digest(path) if path.is_file() else 'DIRECTORY'
    for name in root_names:
        path=project/name
        safe_tree(path)
        if path.exists() and not path.is_file(): raise RuntimeError('Managed root asset is not a file: '+str(path))
        result[name]=digest(path) if path.is_file() else None
    return result


def preserve_managed(source, destination, root_names):
    """Copy every managed byte, including old libraries absent from v1 hashes."""
    expected=asset_inventory(source,root_names)
    destination.mkdir()
    for name in ('00_sheet','01_Lib'):
        shutil.copytree(source/name,destination/name)
    for name in root_names:
        if (source/name).is_file():
            (destination/name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/name,destination/name)
    if asset_inventory(destination,root_names)!=expected:
        raise RuntimeError('Full asset backup verification failed: '+str(destination))
    if asset_inventory(source,root_names)!=expected:
        raise RuntimeError('Assets changed while copying backup: '+str(source))
    return expected


def require_preserved(current, preserved):
    """Never replace/delete an existing file before its exact bytes are saved."""
    safe_tree(current);safe_tree(preserved)
    if not current.is_file() or not preserved.is_file() or digest(current)!=digest(preserved):
        raise RuntimeError('Asset changed or lacks a verified backup: '+str(current))


def sync_directory(source, destination, preserved):
    """Synchronize children without renaming/removing the destination directory.

    Existing directories retain their identity, so Explorer/editor directory
    handles without FILE_SHARE_DELETE do not prevent publishing. File removals
    require a verified copy; extra directories use empty-only rmdir.
    """
    safe_tree(source);safe_tree(destination);safe_tree(preserved)
    if not source.is_dir() or not destination.is_dir() or not preserved.is_dir():
        raise RuntimeError('Directory synchronization requires three existing directories')
    wanted={p.relative_to(source):p for p in source.rglob('*')}
    # Create directories first, including intentionally empty model libraries.
    for relative,path in sorted(wanted.items(),key=lambda item:len(item[0].parts)):
        if path.is_dir():
            target=destination/relative
            if target.exists() and not target.is_dir():
                raise RuntimeError('File/directory type collision: '+str(target))
            target.mkdir(exist_ok=True)
    for relative,path in wanted.items():
        if not path.is_file(): continue
        target=destination/relative
        safe_tree(target)
        if target.exists():
            if not target.is_file(): raise RuntimeError('File/directory type collision: '+str(target))
            if digest(target)==digest(path): continue
            require_preserved(target,preserved/relative)
        shutil.copy2(path,target)
        if digest(target)!=digest(path): raise RuntimeError('Synchronized file hash mismatch: '+str(target))
    # Remove obsolete files only after all replacements have been copied.
    for target in destination.rglob('*'):
        relative=target.relative_to(destination)
        if target.is_file() and relative not in wanted:
            require_preserved(target,preserved/relative)
            target.unlink()
    extra_dirs=[p for p in destination.rglob('*') if p.is_dir() and p.relative_to(destination) not in wanted]
    for directory in sorted(extra_dirs,key=lambda path:len(path.parts),reverse=True):
        safe_tree(directory)
        directory.rmdir()


def sync_root_file(source, destination, preserved):
    safe_tree(source);safe_tree(destination)
    if source.is_file():
        if destination.exists():
            if not destination.is_file(): raise RuntimeError('Managed root asset is not a file: '+str(destination))
            if digest(source)==digest(destination): return
            require_preserved(destination,preserved)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
        if digest(source)!=digest(destination): raise RuntimeError('Root asset hash mismatch: '+str(destination))
    elif destination.exists():
        require_preserved(destination,preserved)
        destination.unlink()


def preserve_model_manifest(project, previous, mapping, destination):
    """Rebuild external models from verified project assets, not obsolete paths."""
    entries=[]
    for footprint in mapping.get('footprints',[]):
        for model in footprint.get('model_references',[]):
            if model.get('status')!='collected' or model['original'].startswith('kicad-embed://'): continue
            relative=model['asset_path']
            expected=previous['output_hashes'].get(relative)
            path=project/relative
            if not expected or expected!=model['sha256'] or digest(path)!=expected:
                raise RuntimeError('Previously collected model is not hash-verified: '+relative)
            entries.append({'uuid':footprint['uuid'],'index':model['index'],'original':model['original'],
                            'path':str(path.resolve()),'sha256':expected})
    if entries: write_json(destination,{'schema_version':1,'entries':entries})
    return entries


def require_models_preserved(before, after):
    """Never publish a rebuild that loses or changes an already collected model."""
    rebuilt={(fp['uuid'],model['index'],model['original']):model
             for fp in after.get('footprints',[]) for model in fp.get('model_references',[])}
    for fp in before.get('footprints',[]):
        for model in fp.get('model_references',[]):
            if model.get('status')!='collected': continue
            key=(fp['uuid'],model['index'],model['original'])
            new=rebuilt.get(key,{})
            if new.get('status')!='collected' or new.get('sha256')!=model['sha256']:
                raise RuntimeError('Candidate loses or changes a previously collected model: '+repr(key))


def migrate(project,cli,nickname=None,repair_multitop_layout=False):
    started=time.perf_counter()
    project=Path(project).absolute();safe_tree(project)
    project=project.resolve();cli=Path(cli).resolve()
    if project.name.casefold()=='ad2kicad_original_prj': raise ValueError('Original project is protected')
    relocate_legacy_artifacts(project,verify_output_hashes)
    marker=artifact_path(project,'AD2KICAD_REPORT.json')
    previous=json.loads(marker.read_text(encoding='utf-8'))
    nickname=check_nickname(nickname) if nickname is not None else report_nickname(previous)
    old_schema=previous.get('schema_version',1)
    if old_schema not in (1,2): raise ValueError('Expected a verified v1 or v2 project')
    stem=previous['project_name'];root_schematic=project/(stem+'.kicad_sch')
    if old_schema==1 and root_schematic.exists(): raise ValueError('Unexpected root schematic; close/recover any old GUI session')
    if old_schema==2 and not repair_multitop_layout:
        validate_layout(project,previous)
        if report_nickname(previous)==nickname: raise ValueError('Project already has the requested library name')
        if report_nickname(previous).casefold()==nickname.casefold():
            raise ValueError('Case-only library rename is unsupported on Windows; choose a distinct name')
    metadata=verify_output_hashes(project,previous)
    if old_schema==1 and {p.name for p in (project/'00_sheet').iterdir()}!={Path(p).name for p in previous['output_hashes'] if p.startswith('00_sheet/')}:
        raise ValueError('Unexpected files in 00_sheet; preserve and review before migration')
    managed_roots=[stem+'.kicad_pcb',stem+'.kicad_pro',stem+'.kicad_sch','sym-lib-table','fp-lib-table']
    artifact_names=set(ARTIFACT_NAMES)
    artifact_names.update(path.name for path in marker.parent.glob('AD2KICAD_REPAIR-*.md') if path.is_file())
    artifact_relatives={artifact_path(project,name).relative_to(project).as_posix() for name in artifact_names}
    managed_roots += sorted(artifact_relatives)
    marker_relative=marker.relative_to(project).as_posix()
    for top in previous.get('top_level_sheets',[]):
        name=Path(top['filename']).name
        if name not in managed_roots: managed_roots.append(name)
    before_inventory=asset_inventory(project,managed_roots)
    original=project/previous['run_directory']/'input'
    original.resolve().relative_to(project/'_ad2kicad_work')
    for name,expected in previous['input_hashes'].items():
        if digest(original/name)!=expected: raise RuntimeError('Original snapshot hash mismatch: '+name)
    work=project/'_ad2kicad_work'/('migration_v2_'+time.strftime('%Y%m%d_%H%M%S')+'_'+str(time.time_ns()%1000000))
    candidate=work/'candidate';candidate.mkdir(parents=True)
    for name in previous['input_hashes']: shutil.copy2(original/name,candidate/name)
    old_mapping=json.loads(find_artifact(project,'AD2KICAD_MAPPING.json').read_text(encoding='utf-8'))
    preserved_models=preserve_model_manifest(project,previous,old_mapping,work/'preserved-models.json')
    command=[sys.executable,str(Path(__file__).with_name('organize_project.py')),'--project',str(candidate),'--kicad-cli',str(cli),'--library-name='+nickname]
    if preserved_models: command += ['--preserved-models',str(work/'preserved-models.json')]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240)
    (work/'candidate-build.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode: raise RuntimeError('Candidate build failed; actual project unchanged; see '+str(work/'candidate-build.log'))
    updated=json.loads(artifact_path(candidate,'AD2KICAD_REPORT.json').read_text(encoding='utf-8'))
    require_models_preserved(old_mapping,json.loads(artifact_path(candidate,'AD2KICAD_MAPPING.json').read_text(encoding='utf-8')))
    if metadata:
        project_file=candidate/(stem+'.kicad_pro')
        settings=json.loads(project_file.read_text(encoding='utf-8'))
        cache=json.loads((project/(stem+'.kicad_pro')).read_text(encoding='utf-8'))['schematic'].get('used_designators')
        if cache is None: settings['schematic'].pop('used_designators',None)
        else: settings['schematic']['used_designators']=cache
        write_json(project_file,settings)
        updated['output_hashes'][project_file.name]=digest(project_file)
        # Keep the expected snapshot consistent for future metadata-only checks.
        shutil.copy2(project_file,candidate/updated['run_directory']/'output'/project_file.name)
        updated['preserved_derived_project_metadata']=metadata
    validate_layout(candidate,updated)
    verify_output_hashes(project,previous)
    if asset_inventory(project,managed_roots)!=before_inventory:
        raise RuntimeError('Concurrent asset addition/edit before publish; current project unchanged')
    # Preserve and verify complete old trees before editing any published asset.
    # Keep their directory bodies intact for editor/Explorer watch handles.
    backup=work/'before'
    preserved_inventory=preserve_managed(project,backup,managed_roots)
    if preserved_inventory!=before_inventory:
        raise RuntimeError('Concurrent asset change before publish; current project unchanged')
    try:
        for name in ('00_sheet','01_Lib'):
            sync_directory(candidate/name,project/name,backup/name)
        for name in managed_roots:
            if name==marker_relative: continue
            # A candidate rebuild writes mapping/variants/missing models.
            # Retain historical setup/repair records and the current index
            # until their owning entrypoints explicitly regenerate them.
            if name in artifact_relatives and not (candidate/name).exists(): continue
            sync_root_file(candidate/name,project/name,backup/name)
        updated['run_directory']=(candidate/updated['run_directory']).relative_to(project).as_posix()
        updated['migration']={'from_schema':old_schema,'previous_library_name':report_nickname(previous) if old_schema==2 else 'AD2Kicad',
                              'repair_multitop_layout':repair_multitop_layout,
                              'preserved_external_model_references':len(preserved_models),
                              'backup':backup.relative_to(project).as_posix(),
                              'root_sheet':stem+'.kicad_sch','seconds':round(time.perf_counter()-started,3)}
        if old_schema==1: updated['migration']['old_root_sheet']='00_sheet/'+stem+'.kicad_sch'
        validate_layout(project,updated)
        for rel,expected in updated['output_hashes'].items():
            if digest(project/rel)!=expected: raise RuntimeError('Publish hash mismatch: '+rel)
        write_json(marker,updated)
    except BaseException:
        # Preserve every partial-new byte before restoring, without renames.
        failed=work/'failed-publish'
        try:
            preserve_managed(project,failed,managed_roots)
            for name in ('00_sheet','01_Lib'):
                sync_directory(backup/name,project/name,failed/name)
            for name in managed_roots:
                sync_root_file(backup/name,project/name,failed/name)
            if asset_inventory(project,managed_roots)!=before_inventory:
                raise RuntimeError('Restored assets differ from the verified backup')
        except BaseException as rollback_error:
            raise RuntimeError('Publication failed and automatic rollback needs recovery; '
                               'all completed backups remain at '+str(work)) from rollback_error
        raise
    return {'status':'repaired_multitop_verified' if repair_multitop_layout else ('migrated_v2_verified' if old_schema==1 else 'renamed_v2_verified'),
            'library_name':nickname,'project':str(project),'seconds':round(time.perf_counter()-started,3),
            'root_schematic':stem+'.kicad_sch','child_schematic_count':updated['child_schematic_count'],
            'footprints':updated['unique_footprint_count'],'backup':str(backup)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True);parser.add_argument('--kicad-cli',type=Path,required=True)
    parser.add_argument('--library-name',type=check_nickname)
    args=parser.parse_args();print(json.dumps(migrate(args.project,args.kicad_cli,args.library_name),ensure_ascii=False,indent=2))
