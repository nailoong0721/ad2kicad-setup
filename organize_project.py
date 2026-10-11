"""Consolidate this imported KiCad project, with native validation before commit.

Supported input: one flat project with embedded schematic symbols and one PCB.
Retain independent top sheets and collect real models without changing circuits.
Run using the Python shipped with KiCad (pcbnew is required by the helper).
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from sexpr import Atom, Node, parse, quote, edits, replace_atom, semantic
from netlist_compat import INVALID_XML, read_netlist
from project_artifacts import artifact_path, find_artifact, relocate_legacy_artifacts

NICK = 'AD2Kicad'

def check_nickname(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('Library name must use 1-64 ASCII letters, digits, underscores or hyphens')
    if name.upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('Library name is reserved by Windows: '+name)
    return name

def report_nickname(report):
    return check_nickname(report.get('library_name', 'AD2Kicad'))

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def verify_output_hashes(root, report):
    """Permit only KiCad's derived used_designators cache to differ from a saved project."""
    root=Path(root)
    metadata=[]
    for rel,expected in report['output_hashes'].items():
        file=root/rel
        if digest(file)==expected: continue
        if rel==report['project_name']+'.kicad_pro':
            snapshot=root/report['run_directory']/'output'/rel
            snapshot.resolve().relative_to((root/'_ad2kicad_work').resolve())
            if snapshot.is_file() and digest(snapshot)==expected:
                before=json.loads(snapshot.read_text(encoding='utf-8'))
                after=json.loads(file.read_text(encoding='utf-8'))
                before_cache=before.get('schematic',{}).pop('used_designators',None)
                after_cache=after.get('schematic',{}).pop('used_designators',None)
                if before==after and isinstance(after_cache,(str,type(None))):
                    metadata.append({'file':rel,'field':'schematic.used_designators',
                                     'before':before_cache,'after':after_cache,
                                     'recorded_sha256':expected,'current_sha256':digest(file)})
                    continue
        raise RuntimeError('Managed file changed since verified run: '+rel)
    return metadata

def write_json(path, data):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def run(args, log, env=None):
    Path(log).parent.mkdir(parents=True,exist_ok=True)
    try:
        result=subprocess.run([str(a) for a in args],capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=180)
    except subprocess.TimeoutExpired as exc:
        def decoded(value): return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else (value or '')
        Path(log).write_text(json.dumps([str(a) for a in args],ensure_ascii=False)+'\nTIMEOUT after 180 seconds\n'+decoded(exc.stdout)+'\n'+decoded(exc.stderr),encoding='utf-8')
        raise RuntimeError('Command timed out; project not published; see '+str(log)) from exc
    Path(log).write_text(json.dumps([str(a) for a in args],ensure_ascii=False)+'\n'+result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        detail='\n'.join((result.stderr or result.stdout).strip().splitlines()[-12:])
        raise RuntimeError(f'Command failed ({result.returncode}); see {log}\n{detail}')
    return result

def safe_tree(path):
    """Reject junctions/symlinks before copying or replacing project assets."""
    path=Path(path).absolute()
    for p in [path]+list(path.parents):
        if p.exists() and getattr(p.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError(f'Reparse point is not supported: {p}')
    if path.is_dir():
        for base,dirs,files in os.walk(path,followlinks=False):
            for name in dirs+files:
                p=Path(base)/name
                if getattr(p.lstat(),'st_file_attributes',0)&0x400 or p.is_symlink():
                    raise ValueError(f'Reparse point is not supported: {p}')

def net_signature(path):
    doc=read_netlist(path)
    comps=sorted((c.attrib['ref'],c.findtext('value','')) for c in doc.findall('./components/comp'))
    # Compare actual pin membership; automatic net names can depend on library ID.
    nets=sorted(tuple(sorted((n.attrib['ref'],n.attrib['pin']) for n in e.findall('node'))) for e in doc.findall('./nets/net'))
    return {'components':comps,'nets':nets}

def schematic_topology(input_dir, root_name):
    documents={p.name:parse(p.read_text(encoding='utf-8')) for p in input_dir.glob('*.kicad_sch')}
    settings=json.loads((input_dir/(root_name+'.kicad_pro')).read_text(encoding='utf-8-sig'))
    declared=settings.get('schematic',{}).get('top_level_sheets',[])
    if not declared:
        declared=[{'filename':root_name+'.kicad_sch','name':root_name}]
    tops=[];owners={}
    for entry in declared:
        filename=entry.get('filename','')
        if filename not in documents or Path(filename).name!=filename:
            raise ValueError('Top-level schematic must exist in the flat input: '+filename)
        if filename in [row['source_file'] for row in tops]:
            raise ValueError('Duplicate top-level schematic: '+filename)
        tops.append({**entry,'source_file':filename,
                     'filename':filename,
                     'uuid':documents[filename].child('uuid').value()})
    if root_name+'.kicad_sch' not in [row['source_file'] for row in tops]:
        raise ValueError('Project-level schematic must be a declared top-level sheet')
    tops.sort(key=lambda row:(row['source_file']!=root_name+'.kicad_sch',row['source_file']))
    def walk(filename, owner, active):
        if filename in active: raise ValueError('Cyclic schematic hierarchy: '+filename)
        if filename in owners:
            if owners[filename]!=owner: raise ValueError('A schematic is shared by distinct top-level circuits: '+filename)
            return
        owners[filename]=owner
        for sheet in documents[filename].children('sheet'):
            field=sheet.prop('Sheetfile')
            child=field.value(2) if field else ''
            if child not in documents or Path(child).name!=child:
                raise ValueError('Missing or non-flat child schematic: '+child)
            walk(child,owner,active|{filename})
    for top in tops: walk(top['source_file'],top['source_file'],set())
    if set(owners)!=set(documents): raise ValueError('Undeclared orphan schematics in input')
    return tops,owners

def erc_counts(path):
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    return dict(collections.Counter(v['type'] for s in d.get('sheets',[]) for v in s.get('violations',[])))

def symbol_text(text, node, name, fp, cached=False):
    old=node.value(); oldstem=old.split(':')[-1]
    replacement=[replace_atom(node.items[1], NICK+':'+name if cached else name)]
    for child in node.children('symbol'):
        childname=child.value()
        if not childname.startswith(oldstem+'_'):
            raise ValueError('Unexpected unit name: '+childname)
        replacement.append(replace_atom(child.items[1],name+childname[len(oldstem):]))
    prop=node.prop('Footprint')
    if prop: replacement.append(replace_atom(prop.items[2],fp))
    if node.child('extends'): raise ValueError('Inherited symbol needs resolution: '+old)
    offset=node.start
    return edits(text[node.start:node.end],[(a-offset,b-offset,c) for a,b,c in replacement])

def collect_symbols(input_dir,out_dir,fp_report,root_name):
    tops,owners=schematic_topology(input_dir,root_name)
    root_uuids={row['source_file']:row['uuid'] for row in tops}
    byref={r:[x for x in fp_report['footprint_map'] if x['reference']==r] for r in {x['reference'] for x in fp_report['footprint_map'] if x['reference']}}
    for ref,rows in byref.items():
        if len(rows)!=1: raise ValueError('Duplicate PCB reference: '+ref)
    all_defs={}; mapping=[]; identities=[]; count=0
    root_text=(input_dir/(root_name+'.kicad_sch')).read_text(encoding='utf-8')
    root_uuid=parse(root_text).child('uuid').value()
    sheet_names={}
    for file in sorted(input_dir.glob('*.kicad_sch')):
        owner_uuid=root_uuids[owners[file.name]]
        relative=file.name if file.name in root_uuids else '00_sheet/'+file.name
        text=file.read_text(encoding='utf-8'); doc=parse(text)
        cache=doc.child('lib_symbols')
        if cache is None: raise ValueError('No embedded lib_symbols in '+file.name)
        defs={x.value():x for x in cache.children('symbol')}
        used={}; repl=[]
        for instance in doc.children('symbol'):
            oldid=instance.child('lib_id').value()
            alias=instance.child('lib_name')
            effective=alias.value() if alias else oldid
            if effective not in defs: raise ValueError('Missing cached definition '+effective)
            ref=instance.prop('Reference').value(2)
            fp=byref[ref][0]['library_id'] if ref in byref else ''
            if not ref.startswith('#') and ref not in byref:
                raise ValueError('Schematic reference has no PCB footprint: '+ref)
            template=symbol_text(text,defs[effective],'SYMBOL',fp)
            signature=json.dumps(semantic(parse(template)),ensure_ascii=False,separators=(',',':'))
            key=hashlib.sha256(signature.encode('utf-8')).hexdigest()
            # Content + mapped footprint distinguish same-name variants across sheets.
            if key not in all_defs:
                stem=re.sub(r'[^A-Za-z0-9_.+-]+','_',oldid.split(':')[-1])[:72] or 'Symbol'
                name=stem+'__'+key[:12]
                all_defs[key]={'name':name,'text':symbol_text(text,defs[effective],name,fp),'fp':fp}
            name=all_defs[key]['name'];newid=NICK+':'+name
            used[name]=symbol_text(text,defs[effective],name,fp,True)
            repl.append(replace_atom(instance.child('lib_id').items[1],newid))
            if alias: repl.append((alias.start,alias.end,''))
            prop=instance.prop('Footprint')
            if prop: repl.append(replace_atom(prop.items[2],fp))
            elif fp: raise ValueError('Missing instance Footprint property for '+ref)
            count+=1
            mapping.append({'file':relative,'uuid':instance.child('uuid').value(),'reference':ref,'old_lib_id':oldid,'effective_cached_id':effective,'lib_id':newid,'footprint':fp})
            # Correct stale imported root UUID in stored instance associations.
            instances=instance.child('instances')
            if instances:
                for project in instances.children('project'):
                    if project.value()!=root_name: continue
                    for path in project.children('path'):
                        parts=path.value().split('/')
                        if len(parts)>1 and parts[1]!=owner_uuid:
                            oldpath=path.value();parts[1]=owner_uuid
                            newpath='/'.join(parts)
                            repl.append(replace_atom(path.items[1],newpath))
                            identities.append({'file':file.name,'uuid':instance.child('uuid').value(),'old':oldpath,'new':newpath})
        repl.append((cache.start,cache.end,'(lib_symbols\n'+'\n'.join(used[k] for k in sorted(used))+'\n)'))
        for sheet in doc.children('sheet'):
            target=sheet.prop('Sheetfile')
            if not target or not (input_dir/target.value(2)).is_file(): raise ValueError('Missing child sheet')
            if Path(target.value(2)).name!=target.value(2): raise ValueError('Nested input layout not supported')
            child_file='00_sheet/'+target.value(2)
            if file.name in root_uuids:
                repl.append(replace_atom(target.items[2],child_file))
            sheet_names[sheet.child('uuid').value()]={'name':sheet.prop('Sheetname').value(2),'file':child_file}
        if file.stem==root_name:
            project=json.loads((input_dir/(root_name+'.kicad_pro')).read_text(encoding='utf-8'))
            aliases=project.get('schematic',{}).get('bus_aliases',{})
            existing={n.value() for n in doc.children('bus_alias')}
            entries=[]
            for alias_name, members in aliases.items():
                if alias_name not in existing:
                    entries.append('(bus_alias '+quote(alias_name)+' (members '+' '.join(quote(x) for x in members)+'))')
            if entries: repl.append((doc.end-1,doc.end-1,'\n'+'\n'.join(entries)+'\n'))
        (out_dir/relative).write_text(edits(text,repl),encoding='utf-8')
    lib='(kicad_symbol_lib (version 20241209) (generator "ad2kicad_setup")\n'+'\n'.join(all_defs[k]['text'] for k in sorted(all_defs,key=lambda x:all_defs[x]['name']))+'\n)\n'
    (out_dir/'01_Lib'/(NICK+'.kicad_sym')).write_text(lib,encoding='utf-8')
    return {'instance_count':count,'unique_symbol_count':len(all_defs),'symbol_map':mapping,'instance_path_repairs':identities,'root_uuid':root_uuid,'sheets':sheet_names,'top_level_sheets':tops,'root_uuids':root_uuids,'sheet_owners':owners}

def remap_pcb(input_file,output_file,fp_report,symbol_report,baseline_net):
    text=input_file.read_text(encoding='utf-8');doc=parse(text)
    byuuid={x['uuid']:x for x in fp_report['footprint_map']}
    symbol_files={(row['reference'],row['uuid']):row['file'] for row in symbol_report['symbol_map']}
    sources=baseline_net if isinstance(baseline_net,dict) else {input_file.stem+'.kicad_sch':baseline_net}
    comps={};component_roots={}
    for source_name,netlist in sources.items():
        for comp in read_netlist(netlist).findall('./components/comp'):
            reference=comp.attrib['ref']
            if reference in comps: raise ValueError('Duplicate component between top-level circuits: '+reference)
            comps[reference]=comp
            component_roots[reference]=symbol_report.get('root_uuids',{}).get(source_name,symbol_report['root_uuid'])
    repl=[];pathrepairs=[]
    for fp in doc.children('footprint'):
        item=byuuid[fp.child('uuid').value()]
        repl.append(replace_atom(fp.items[1],item['library_id']))
        models=fp.children('model')
        references=item.get('model_references',[])
        if len(models)!=len(references): raise RuntimeError('Model mapping count mismatch: '+item['uuid'])
        for index,(model,reference) in enumerate(zip(models,references)):
            if reference['index']!=index or reference['original']!=model.value():
                raise RuntimeError('Model mapping order/source mismatch: '+item['uuid'])
            if reference['target']!=model.value():
                repl.append(replace_atom(model.items[1],reference['target']))
        ref=item['reference']
        if ref in comps:
            comp=comps[ref]; sheetpath=comp.find('sheetpath').attrib['tstamps']
            tstamp=comp.findtext('tstamps','').split()[0]
            newpath='/'+component_roots[ref]+sheetpath+tstamp
            oldpath=fp.child('path')
            if oldpath:
                repl.append(replace_atom(oldpath.items[1],newpath))
                pathrepairs.append({'reference':ref,'old':oldpath.value(),'new':newpath})
            sheetfile=fp.child('sheetfile')
            if sheetfile is not None:
                relative=symbol_files.get((ref,tstamp))
                if relative is None: raise RuntimeError('Cannot resolve component schematic file: '+ref)
                repl.append(replace_atom(sheetfile.items[1],relative))
    output_file.write_text(edits(text,repl),encoding='utf-8')
    newdoc=parse(output_file.read_text(encoding='utf-8'))
    # Permit library IDs, association paths and explicitly mapped model filenames.
    # Never omit model transforms from the geometry comparison.
    for old,new in zip(doc.children('footprint'),newdoc.children('footprint')):
        old_models=old.children('model');new_models=new.children('model')
        if len(old_models)!=len(new_models): raise RuntimeError('PCB model count changed')
        for old_model,new_model in zip(old_models,new_models):
            before=semantic(old_model);after=semantic(new_model);before[1]=after[1]='MODEL_PATH'
            if before!=after: raise RuntimeError('PCB model transform changed: '+old.child('uuid').value())
        a=semantic(old,('path','sheetfile'));b=semantic(new,('path','sheetfile'));a[1]=b[1]='LIB_ID'
        for tree in (a,b):
            for item in tree:
                if isinstance(item,list) and item and item[0]=='model': item[1]='MODEL_PATH'
        if a!=b: raise RuntimeError('PCB geometry/net drift: '+old.child('uuid').value())
    oldrest=[semantic(x) for x in doc.children() if x.kind!='footprint']
    newrest=[semantic(x) for x in newdoc.children() if x.kind!='footprint']
    if oldrest!=newrest: raise RuntimeError('Board routing/setup drift')
    return pathrepairs

def library_tables(output):
    (output/'sym-lib-table').write_text(f'(sym_lib_table (version 7)\n  (lib (name "{NICK}") (type "KiCad") (uri "${{KIPRJMOD}}/01_Lib/{NICK}.kicad_sym") (options "") (descr "Project embedded symbols"))\n)\n',encoding='utf-8')
    (output/'fp-lib-table').write_text(f'(fp_lib_table (version 7)\n  (lib (name "{NICK}") (type "KiCad") (uri "${{KIPRJMOD}}/01_Lib/{NICK}.pretty") (options "") (descr "Project PCB footprints"))\n)\n',encoding='utf-8')

def validate_layout(root,report):
    root=Path(root)
    nick=report_nickname(report)
    for table, extension in (('sym-lib-table','.kicad_sym'),('fp-lib-table','.pretty')):
        document=parse((root/table).read_text(encoding='utf-8'))
        libraries=document.children('lib')
        if len(libraries)!=1 or libraries[0].child('name').value()!=nick or libraries[0].child('uri').value()!=f'${{KIPRJMOD}}/01_Lib/{nick}{extension}':
            raise RuntimeError('Library table does not match project nickname: '+table)
    root_file=root/(report['project_name']+'.kicad_sch')
    roots=list(root.glob('*.kicad_sch'))
    declared=report.get('top_level_sheets',[{'filename':root_file.name}])
    if any(Path(row['filename']).name!=row['filename'] for row in declared):
        raise RuntimeError('Native top-level filenames must not contain directories')
    if {p.name for p in roots}!={row['filename'] for row in declared}:
        raise RuntimeError('Exactly the independently declared top-level schematics must remain at root')
    children=list((root/'00_sheet').iterdir())
    expected_children=report.get('child_schematic_count',report['schematic_count']-len(roots))
    if len(children)!=expected_children or len(children)+len(roots)!=report['schematic_count'] or any(not p.is_file() or p.suffix!='.kicad_sch' for p in children):
        raise RuntimeError('00_sheet must contain only child schematic files')
    sch=roots+children
    settings=json.loads((root/(report['project_name']+'.kicad_pro')).read_text(encoding='utf-8-sig'))
    tops=settings.get('schematic',{}).get('top_level_sheets',[])
    top_paths=[row.get('filename','') for row in tops]
    if not tops or len(top_paths)!=len(set(top_paths)) or root_file.name not in top_paths:
        raise RuntimeError('Native project must declare its root-level schematic and unique top sheets')
    expected_top_paths=[row['filename'] for row in report.get('top_level_sheets',tops)]
    if top_paths!=expected_top_paths: raise RuntimeError('Project top-level sheet list differs from report')
    top_uuids={}
    for top in tops:
        path=Path(top['filename'])
        if path.is_absolute() or '..' in path.parts or (root/path) not in sch:
            raise RuntimeError('Top-level sheet is outside the managed schematic files')
        top_uuids[(root/path).resolve()]=top['uuid']
    expected={nick+'.kicad_sym',nick+'.pretty',nick+'.3dmodels','AD_lib_bak'}
    if {p.name for p in (root/'01_Lib').iterdir()}!=expected: raise RuntimeError('01_Lib layout mismatch')
    models=root/'01_Lib'/(nick+'.3dmodels')
    assets=report.get('model_assets',[])
    prefix=f'01_Lib/{nick}.3dmodels/'
    asset_paths={}
    for asset in assets:
        rel=asset['path']
        if not rel.startswith(prefix) or Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise RuntimeError('Model asset escapes the project model library: '+rel)
        if rel in asset_paths: raise RuntimeError('Duplicate model asset in report: '+rel)
        file=root/rel
        if not file.is_file() or digest(file)!=asset['sha256']:
            raise RuntimeError('Collected model missing or changed: '+rel)
        asset_paths[rel]=asset
    if {p.relative_to(root).as_posix() for p in models.rglob('*') if p.is_file()}!=set(asset_paths):
        raise RuntimeError('3D model library contents differ from recorded assets')
    unresolved=set(report.get('unresolved_model_references',[]))
    def check_models(footprint):
        for model in footprint.children('model'):
            reference=model.value()
            if reference.startswith('${KIPRJMOD}/'):
                relative=reference[len('${KIPRJMOD}/'):]
                if relative in asset_paths: continue
            if reference not in unresolved:
                raise RuntimeError('Unrecorded or unresolved model reference: '+reference)
    pretty=root/'01_Lib'/(nick+'.pretty')
    footprints=list(pretty.iterdir())
    if len(footprints)!=report['unique_footprint_count'] or any(not p.is_file() or p.suffix!='.kicad_mod' for p in footprints):
        raise RuntimeError('Footprint library must contain exactly the reported .kicad_mod files')
    footprint_ids=set();footprint_names=set()
    for file in footprints:
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+',file.stem) or re.search(r'__[0-9a-fA-F]{8,}$',file.stem):
            raise RuntimeError('Footprint filename must be readable and have no hash suffix: '+file.name)
        if file.stem.casefold() in footprint_names: raise RuntimeError('Case-insensitive duplicate footprint name: '+file.name)
        footprint_names.add(file.stem.casefold())
        footprint=parse(file.read_text(encoding='utf-8'))
        if footprint.kind!='footprint' or footprint.value()!=file.stem:
            raise RuntimeError('Footprint internal name differs from filename: '+file.name)
        check_models(footprint)
        value=footprint.prop('Value')
        if value is None or value.value(2)!=file.stem:
            raise RuntimeError('Library footprint Value differs from filename: '+file.name)
        footprint_ids.add(nick+':'+file.stem)
    board_file=root/(report['project_name']+'.kicad_pcb')
    board=parse(board_file.read_text(encoding='utf-8'))
    if board.kind!='kicad_pcb': raise RuntimeError('Invalid PCB document')
    board_footprints=board.children('footprint')
    if len(board_footprints)!=report['pcb_footprint_count']: raise RuntimeError('PCB footprint instance count mismatch')
    used_footprints=set();board_refs={};board_uuids=set()
    for footprint in board_footprints:
        check_models(footprint)
        lid=footprint.value()
        if lid not in footprint_ids: raise RuntimeError('Unresolved PCB footprint ID: '+lid)
        used_footprints.add(lid)
        uuid=footprint.child('uuid').value()
        if uuid in board_uuids: raise RuntimeError('Duplicate PCB footprint UUID: '+uuid)
        board_uuids.add(uuid)
        reference=footprint.prop('Reference')
        if reference is not None and reference.value(2):
            ref=reference.value(2)
            if ref in board_refs: raise RuntimeError('Duplicate PCB footprint reference: '+ref)
            board_refs[ref]=lid
    if used_footprints!=footprint_ids: raise RuntimeError('Project footprint library contains unused definitions')
    libdoc=parse((root/'01_Lib'/(nick+'.kicad_sym')).read_text(encoding='utf-8'))
    definitions={x.value():x for x in libdoc.children('symbol')}
    if len(definitions)!=len(libdoc.children('symbol')) or len(definitions)!=report['unique_symbol_count']:
        raise RuntimeError('Unique symbol definition count mismatch')
    count=0; edges={}; seen_uuids=set(); used_definitions=set()
    for f in sch:
        doc=parse(f.read_text(encoding='utf-8'))
        if doc.kind!='kicad_sch': raise RuntimeError('Invalid schematic root: '+str(f))
        if f.resolve() in top_uuids and top_uuids[f.resolve()]!=doc.child('uuid').value():
            raise RuntimeError('Native project root UUID mismatch')
        edges[f.resolve()]=[]
        for sheet in doc.children('sheet'):
            target=sheet.prop('Sheetfile')
            if target is None: raise RuntimeError('Missing Sheetfile: '+str(f))
            rel=Path(target.value(2))
            if rel.is_absolute() or '..' in rel.parts or '${' in target.value(2):
                raise RuntimeError('Unsupported Sheetfile path: '+target.value(2))
            child=(f.parent/rel).resolve()
            if child.parent!=(root/'00_sheet').resolve() or not child.is_file() or child.suffix!='.kicad_sch':
                raise RuntimeError('Child sheet must resolve inside 00_sheet: '+target.value(2))
            edges[f.resolve()].append(child)
        if doc.child('lib_symbols') is None: raise RuntimeError('Missing embedded symbol cache: '+str(f))
        cache={x.value():x for x in doc.child('lib_symbols').children('symbol')}
        for inst in doc.children('symbol'):
            lid=inst.child('lib_id').value();instance_nick,part=lid.split(':',1)
            if instance_nick!=nick or part not in definitions or lid not in cache: raise RuntimeError('Unresolved symbol '+lid)
            cached=semantic(cache[lid]);cached[1]=part
            if cached!=semantic(definitions[part]): raise RuntimeError('Embedded symbol differs from project library: '+lid)
            used_definitions.add(part)
            uuid=inst.child('uuid').value()
            if uuid in seen_uuids: raise RuntimeError('Duplicate symbol instance UUID: '+uuid)
            seen_uuids.add(uuid)
            fp=inst.prop('Footprint').value(2) if inst.prop('Footprint') else ''
            library_fp=definitions[part].prop('Footprint')
            if (library_fp.value(2) if library_fp else '')!=fp:
                raise RuntimeError('Instance/library footprint mismatch: '+lid)
            if fp and fp not in footprint_ids: raise RuntimeError('Unresolved schematic footprint ID: '+fp)
            ref=inst.prop('Reference').value(2)
            if not ref.startswith('#') and board_refs.get(ref)!=fp:
                raise RuntimeError('Schematic/PCB footprint ID mismatch: '+ref)
            count+=1
    if count!=report['symbol_instance_count']: raise RuntimeError('Instance count mismatch')
    if used_definitions!=set(definitions): raise RuntimeError('Project symbol library contains unused definitions')
    visited=set();active=set()
    def visit(file):
        if file in active: raise RuntimeError('Cyclic schematic hierarchy')
        if file in visited: return
        if file not in edges: raise RuntimeError('Missing child schematic from layout')
        active.add(file)
        for child in edges[file]: visit(child)
        active.remove(file);visited.add(file)
    for top_path in top_uuids: visit(top_path)
    if visited!=set(edges): raise RuntimeError('Orphan schematic files in 00_sheet')
    mapping_file=find_artifact(root,'AD2KICAD_MAPPING.json')
    if mapping_file.exists():
        mapping=json.loads(mapping_file.read_text(encoding='utf-8'))
        footprints=mapping.get('footprints',[])
        expected={p.child('uuid').value():p.value() for p in board_footprints}
        if len(footprints)!=len(expected) or {row['uuid']:row['library_id'] for row in footprints}!=expected:
            raise RuntimeError('PCB mapping report differs from active board')
        rows_by_uuid={row['uuid']:row for row in footprints}
        for fp in board_footprints:
            references=rows_by_uuid[fp.child('uuid').value()].get('model_references',[])
            if [row['target'] for row in references]!=[model.value() for model in fp.children('model')]:
                raise RuntimeError('PCB model references differ from instance mapping')
        expected_symbols={}
        for file in sch:
            for inst in parse(file.read_text(encoding='utf-8')).children('symbol'):
                key=(file.relative_to(root).as_posix(),inst.child('uuid').value())
                expected_symbols[key]=(inst.child('lib_id').value(),inst.prop('Footprint').value(2) if inst.prop('Footprint') else '')
        rows=mapping.get('symbols',[])
        if len(rows)!=len(expected_symbols) or {(row['file'],row['uuid']):(row['lib_id'],row['footprint']) for row in rows}!=expected_symbols:
            raise RuntimeError('Symbol mapping report differs from active schematics')
        symbol_files={(row['reference'],row['uuid']):row['file'] for row in rows}
        for fp in board_footprints:
            association=fp.child('path');sheetfile=fp.child('sheetfile');reference=fp.prop('Reference')
            if association is None or sheetfile is None or reference is None: continue
            key=(reference.value(2),association.value().rstrip('/').split('/')[-1])
            if key in symbol_files and sheetfile.value()!=symbol_files[key]:
                raise RuntimeError('PCB schematic filename differs from symbol mapping: '+reference.value(2))
    elif 'output_hashes' in report:
        raise RuntimeError('Missing project mapping report')

def repair_generated_sheetfiles(project, report, verify_only=False):
    """Repair exporter-derived filenames only after all managed hashes match.

    Early multi-top builds used a CLI diagnostic filename for PCB sheetfile.
    The authoritative symbol/UUID map supplies the exact project-relative name.
    Never apply this correction to a board the user has subsequently edited.
    """
    mapping=json.loads(find_artifact(project,'AD2KICAD_MAPPING.json').read_text(encoding='utf-8'))
    symbol_files={(row['reference'],row['uuid']):row['file'] for row in mapping['symbols']}
    filename=report['project_name']+'.kicad_pcb'
    path=project/filename
    original=path.read_bytes();text=original.decode('utf-8');document=parse(text)
    replacements=[];repairs=[]
    for fp in document.children('footprint'):
        ref=fp.prop('Reference');association=fp.child('path');sheetfile=fp.child('sheetfile')
        if ref is None or association is None or sheetfile is None: continue
        target=symbol_files.get((ref.value(2),association.value().rstrip('/').split('/')[-1]))
        if target and target!=sheetfile.value():
            if Path(target).name!=target and (not target.startswith('00_sheet/') or Path(target).name!=target[len('00_sheet/'):]):
                raise RuntimeError('Unsafe recorded schematic path: '+target)
            if not (project/target).is_file(): raise RuntimeError('Missing mapped schematic: '+target)
            replacements.append(replace_atom(sheetfile.items[1],target))
            repairs.append({'reference':ref.value(2),'old':sheetfile.value(),'new':target})
    if not repairs: return []
    if verify_only: raise RuntimeError('Generated PCB sheetfile paths need repair; rerun the normal setup entry')
    verify_output_hashes(project,report)
    snapshot=project/report['run_directory']/'output'/filename
    snapshot.resolve().relative_to((project/'_ad2kicad_work').resolve())
    if not snapshot.is_file() or digest(snapshot)!=report['output_hashes'][filename]:
        raise RuntimeError('Expected verified output snapshot is missing; refusing association repair')
    backup=project/'_ad2kicad_work'/('association_repair_'+str(time.time_ns()))
    backup.mkdir(parents=True)
    marker=artifact_path(project,'AD2KICAD_REPORT.json')
    marker_before=marker.read_bytes();snapshot_before=snapshot.read_bytes()
    (backup/filename).write_bytes(original)
    (backup/'AD2KICAD_REPORT.json').write_bytes(marker_before)
    (backup/'output-snapshot.kicad_pcb').write_bytes(snapshot_before)
    revised=edits(text,replacements).encode('utf-8')
    try:
        if path.read_bytes()!=original: raise RuntimeError('PCB changed while preparing association repair')
        path.write_bytes(revised);snapshot.write_bytes(revised)
        report['output_hashes'][filename]=digest(path)
        report['association_filename_repairs']={'backup':backup.relative_to(project).as_posix(),'changes':repairs}
        validate_layout(project,report)
        write_json(marker,report)
    except BaseException:
        (backup/'failed-board.kicad_pcb').write_bytes(path.read_bytes())
        path.write_bytes(original);snapshot.write_bytes(snapshot_before);marker.write_bytes(marker_before)
        raise
    return repairs

def main():
    global NICK
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project',type=Path,required=True)
    ap.add_argument('--kicad-cli',type=Path,required=True)
    ap.add_argument('--verify-only',action='store_true')
    ap.add_argument('--library-name',type=check_nickname)
    ap.add_argument('--preserved-models',type=Path,help=argparse.SUPPRESS)
    args=ap.parse_args();start=time.perf_counter()
    safe_tree(args.project.absolute())
    project=args.project.resolve();cli=args.kicad_cli.resolve()
    if project.name.casefold()=='ad2kicad_original_prj': raise ValueError('Protected original: select test or speedup copy')
    artifact_moves=relocate_legacy_artifacts(project,verify_output_hashes,args.verify_only)
    marker=artifact_path(project,'AD2KICAD_REPORT.json')
    if args.library_name is None:
        NICK=report_nickname(json.loads(marker.read_text(encoding='utf-8'))) if marker.exists() else 'AD2Kicad'
    else:
        NICK=args.library_name
    if marker.exists():
        report=json.loads(marker.read_text(encoding='utf-8'))
        if report.get('schema_version')==1:
            if args.verify_only:
                raise RuntimeError('Legacy layout detected; run Setup-AD2KiCad without --verify-only to migrate')
            from migrate_v1 import migrate
            print(json.dumps(migrate(project,cli,NICK),ensure_ascii=False,indent=2))
            return
        if report.get('schema_version')!=2: raise RuntimeError('Unsupported project report schema')
        if any(Path(top['filename']).name!=top['filename'] for top in report.get('top_level_sheets',[])):
            if args.verify_only: raise RuntimeError('Native multi-top layout needs repair; rerun normal setup')
            from migrate_v1 import migrate
            print(json.dumps(migrate(project,cli,NICK,repair_multitop_layout=True),ensure_ascii=False,indent=2))
            return
        metadata=verify_output_hashes(project,report)
        filename_repairs=repair_generated_sheetfiles(project,report,args.verify_only)
        validate_layout(project,report)
        if report_nickname(report)!=NICK:
            if args.verify_only: raise RuntimeError('Configured library name differs from requested name')
            if report_nickname(report).casefold()==NICK.casefold():
                raise RuntimeError('Case-only library rename is unsupported on Windows; choose a distinct name')
            from migrate_v1 import migrate
            print(json.dumps(migrate(project,cli,NICK),ensure_ascii=False,indent=2))
            return
        print(json.dumps({'status':'already_configured_verified','elapsed_seconds':round(time.perf_counter()-start,3),
                          'project':str(project),'library_name':NICK,'derived_project_metadata_changes':metadata,'association_filename_repairs':filename_repairs,'relocated_artifacts':artifact_moves}))
        return
    if args.verify_only: raise RuntimeError('No completed run to verify')
    schemas=list(project.glob('*.kicad_sch'));boards=list(project.glob('*.kicad_pcb'));pros=list(project.glob('*.kicad_pro'))
    if len(boards)!=1 or len(pros)!=1 or not schemas: raise ValueError('Expected one flat KiCad project')
    stem=pros[0].stem
    if not (project/(stem+'.kicad_sch')).exists() or boards[0].stem!=stem: raise ValueError('Project/schematic/PCB stems differ')
    if (project/'00_sheet').exists() or (project/'01_Lib').exists(): raise ValueError('Unrecognized partial layout; recover or choose clean copy')
    work=project/'_ad2kicad_work'/('run_'+time.strftime('%Y%m%d_%H%M%S'))
    source=work/'input';output=work/'output';audit=work/'audit'
    source.mkdir(parents=True);output.mkdir();audit.mkdir()
    (output/'00_sheet').mkdir();(output/'01_Lib').mkdir()
    asset_files=schemas+boards+pros+[p for p in project.iterdir() if p.is_file() and (p.suffix.lower() in {'.schlib','.pcblib','.intlib'} or p.name in {'sym-lib-table','fp-lib-table'})]
    input_hashes={p.name:digest(p) for p in asset_files}
    for f in asset_files: shutil.copy2(f,source/f.name)
    write_json(audit/'input_hashes.json',input_hashes)
    tops,owners=schematic_topology(source,stem)
    baselines={};baseline_erc=collections.Counter();xml_controls=[]
    for index,top in enumerate(tops):
        suffix='' if index==0 else '-'+str(index+1)
        baseline=audit/('baseline'+suffix+'.xml')
        erc_file=audit/('baseline-erc'+suffix+'.json')
        run([cli,'sch','export','netlist','--format','kicadxml','-o',baseline,source/top['source_file']],audit/('baseline-net'+suffix+'.log'))
        run([cli,'sch','erc','--format','json','-o',erc_file,source/top['source_file']],audit/('baseline-erc'+suffix+'.log'))
        baselines[top['source_file']]=baseline
        net_signature(baseline)  # Validate before building output, including imported XML controls.
        codepoints=sorted({ord(char) for char in INVALID_XML.findall(baseline.read_text(encoding='utf-8-sig'))})
        if codepoints: xml_controls.append({'filename':top['filename'],'codepoints':codepoints,'policy':'Preserved in design and exported file; reversible parser-only encoding'})
        baseline_erc.update(erc_counts(erc_file))
    extract_command=[sys.executable,Path(__file__).with_name('extract_footprints.py'),'--pcb',source/boards[0].name,'--project-root',project,'--kicad-cli',cli,'--output-root',output/'01_Lib','--nickname='+NICK,'--json',audit/'footprint_map.json']
    if args.preserved_models: extract_command += ['--preserved-models',args.preserved_models.resolve(strict=True)]
    run(extract_command,audit/'extract.log')
    fp_report=json.loads((audit/'footprint_map.json').read_text(encoding='utf-8'))
    symbols=collect_symbols(source,output,fp_report,stem)
    write_json(audit/'symbol_map.json',symbols)
    repairs=remap_pcb(source/boards[0].name,output/boards[0].name,fp_report,symbols,baselines)
    write_json(audit/'pcb_path_repairs.json',repairs)
    pro=json.loads((source/pros[0].name).read_text(encoding='utf-8'))
    published_tops=[{k:v for k,v in row.items() if k!='source_file'} for row in symbols['top_level_sheets']]
    original_top_uuids={row.get('uuid'):symbols['root_uuids'][row['filename']] for row in pro.get('schematic',{}).get('top_level_sheets',[]) if row.get('filename') in symbols['root_uuids']}
    pro.setdefault('schematic',{})['top_level_sheets']=published_tops
    for entry in pro.get('sheets',[]):
        if entry and entry[0] in original_top_uuids: entry[0]=original_top_uuids[entry[0]]
    write_json(output/pros[0].name,pro)
    library_tables(output)
    bak=output/'01_Lib'/'AD_lib_bak';bak.mkdir(exist_ok=True)
    for f in source.iterdir():
        if f.suffix.lower() in {'.schlib','.pcblib','.intlib'} or f.name in {'sym-lib-table','fp-lib-table'}: shutil.copy2(f,bak/f.name)
    report={'schema_version':2,'library_name':NICK,'layout':'root_project_with_child_sheets','root_schematic':stem+'.kicad_sch','child_schematic_count':len(schemas)-1,'project_name':stem,'schematic_count':len(schemas),'symbol_instance_count':symbols['instance_count'],'unique_symbol_count':symbols['unique_symbol_count'],'pcb_footprint_count':fp_report['pcb_footprint_count'],'unique_footprint_count':fp_report['unique_footprint_count'],'missing_3d_instances':fp_report['missing_model_count'],'models_policy':'Collect existing real models; preserve unresolved references and report missing models; never fabricate models','input_hashes':input_hashes,'run_directory':str(work.relative_to(project)).replace('\\','/'),'pcb_path_repairs':len(repairs),'instance_path_repairs':len(symbols['instance_path_repairs'])}
    report.update({'top_level_sheets':published_tops,'top_level_schematic_count':len(published_tops),'imported_xml_controls':xml_controls,
                   'model_assets':fp_report.get('model_assets',[]),
                   'collected_model_count':fp_report.get('collected_model_count',0),
                   'model_reference_count':fp_report.get('model_reference_count',0),
                   'unresolved_model_references':sorted({row['original'] for fp in fp_report['footprint_map'] for row in fp.get('model_references',[]) if row['status']=='missing'})})
    report['child_schematic_count']=len(schemas)-len(published_tops)
    report['layout']='native_top_levels_at_root_with_hierarchical_children'
    report['artifact_directory']='_ad2kicad_work'
    report['artifact_layout_version']=1
    validate_layout(output,report)
    run([sys.executable,Path(__file__).with_name('verify_native_board.py'),'--project',output,'--board',boards[0].name,'--mapping',audit/'footprint_map.json','--json',audit/'native-board.json'],audit/'native-board.log')
    report['native_board_validation']=json.loads((audit/'native-board.json').read_text(encoding='utf-8'))
    run([cli,'sym','export','svg','-o',audit/'symbols-svg',output/'01_Lib'/(NICK+'.kicad_sym')],audit/'symbol-load.log')
    run([sys.executable,Path(__file__).with_name('project_session.py'),'--project',output,'--kicad-cli',cli,'--mode','validate','--output-dir',audit/'native-project'],audit/'native-project.log')
    native=json.loads((audit/'native-project'/'session_report.json').read_text(encoding='utf-8'))
    native_tops=native.get('top_level_results',[{'filename':stem+'.kicad_sch','netlist':str(audit/'native-project'/'netlist.xml'),'erc':str(audit/'native-project'/'erc.json')}])
    if {row['filename'] for row in native_tops}!={row['filename'] for row in published_tops}:
        raise RuntimeError('Native validation did not cover every top-level circuit')
    final_erc=collections.Counter();circuit_counts=[]
    for result in native_tops:
        source_name=Path(result['filename']).name
        before=net_signature(baselines[source_name]);after=net_signature(result['netlist'])
        if before!=after: raise RuntimeError('Native project netlist differs for circuit: '+source_name)
        final_erc.update(erc_counts(result['erc']))
        circuit_counts.append({'filename':result['filename'],'components':len(after['components']),'nets':len(after['nets'])})
    final_erc=dict(final_erc);baseline_erc=dict(baseline_erc)
    report['circuit_counts']=circuit_counts
    mapping_errors=('lib_symbol_issues','lib_symbol_mismatch','footprint_link_issues')
    if any(final_erc.get(k,0) for k in mapping_errors): raise RuntimeError('Native library mapping errors remain')
    if {k:v for k,v in baseline_erc.items() if k not in mapping_errors}!=final_erc:
        raise RuntimeError('Non-library ERC findings changed unexpectedly')
    report['verified']={'netlist_components_and_pin_membership_unchanged':True,'pcb_geometry_nets_and_routing_unchanged':True,'all_symbols_native_parse':True,**fp_report['verified']}
    report['verified'].update({'all_top_level_circuits_validated':True,'pcb_model_transforms_unchanged':True})
    report['baseline_erc']=baseline_erc
    report['final_erc']=final_erc
    report['native_project_validation']='passed; zero library resolution/mismatch errors'
    # Publish only after all preparation passes. Keep full input snapshot for recovery.
    for f in asset_files:
        if digest(f)!=input_hashes[f.name]: raise RuntimeError('Input changed during run: '+f.name)
    for name in ('00_sheet','01_Lib'):
        shutil.copytree(output/name,project/name)
    top_filenames={row['filename'] for row in published_tops}
    for name in (*top_filenames,boards[0].name,pros[0].name,'sym-lib-table','fp-lib-table'):
        shutil.copy2(output/name,project/name)
    for f in schemas:
        if f.name not in top_filenames: f.unlink()
    for f in asset_files:
        if f.suffix.lower() in {'.schlib','.pcblib','.intlib'}: f.unlink()
    write_json(artifact_path(project,'AD2KICAD_MISSING_3D.json'),{'policy':report['models_policy'],'missing_models':fp_report['missing_models']})
    write_json(artifact_path(project,'AD2KICAD_MAPPING.json'),{'symbols':symbols['symbol_map'],'footprints':fp_report['footprint_map']})
    write_json(artifact_path(project,'AD2KICAD_FOOTPRINT_VARIANTS.json'),{k:fp_report[k] for k in ('naming_policy','true_pad_variants','silkscreen_variant_groups','archived_silkscreen_variant_count','instance_library_graphics_differences','functional_mapping_mismatch_count')})
    # Some CLI operations create sidecar settings; none are allowed in 00_sheet.
    for f in (project/'00_sheet').iterdir():
        if f.suffix!='.kicad_sch': raise RuntimeError('Unexpected schematic sidecar: '+str(f))
    report['elapsed_seconds']=round(time.perf_counter()-start,3)
    managed=[project/(stem+'.kicad_sch'),project/boards[0].name,project/pros[0].name,project/'sym-lib-table',project/'fp-lib-table']+list((project/'00_sheet').glob('*.kicad_sch'))+list((project/'01_Lib').glob('*.kicad_sym'))+list((project/'01_Lib'/(NICK+'.pretty')).glob('*.kicad_mod'))
    managed += [artifact_path(project,name) for name in ('AD2KICAD_MAPPING.json','AD2KICAD_MISSING_3D.json','AD2KICAD_FOOTPRINT_VARIANTS.json')]
    managed += [project/asset['path'] for asset in report['model_assets']]
    managed += [project/name for name in top_filenames if name!=stem+'.kicad_sch']
    report['output_hashes']={str(p.relative_to(project)).replace('\\','/'):digest(p) for p in managed}
    write_json(marker,report)
    validate_layout(project,report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'output_hashes','input_hashes','baseline_erc'}},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
