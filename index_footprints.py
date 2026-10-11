"""Create a readable footprint index in the work directory without changing geometry."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import pcbnew
from organize_project import report_nickname, verify_output_hashes
from sexpr import parse
from extract_footprints import native_directory
from project_artifacts import artifact_path, find_artifact, relocate_legacy_artifacts

def build_index(project):
    root=Path(project).resolve()
    relocate_legacy_artifacts(root,verify_output_hashes)
    report=json.loads(find_artifact(root,'AD2KICAD_REPORT.json').read_text(encoding='utf-8'))
    nickname=report_nickname(report)
    mapping=json.loads(find_artifact(root,'AD2KICAD_MAPPING.json').read_text(encoding='utf-8'))
    # KiCad's Windows embedded-file resolver can assert when passed a full
    # drive-qualified filename. Load from the real project cwd with a basename.
    with native_directory(root):
        board=pcbnew.LoadBoard(report['project_name']+'.kicad_pcb')
    if board is None: raise RuntimeError('KiCad could not load the board for the footprint index')
    footprints={fp.m_Uuid.AsString():fp for fp in board.GetFootprints()}
    rows=[]
    for row in mapping['footprints']:
        if row['old_name'] or row['reference'] or row['pad_count']!=1: continue
        fp=footprints[row['uuid']]
        assert fp.GetFPIDAsString()==row['library_id']
        pads=list(fp.Pads())
        assert len(pads)==1
        pad=pads[0]
        library=root/'01_Lib'/(nickname+'.pretty')/(row['footprint_name']+'.kicad_mod')
        definition=parse(library.read_text(encoding='utf-8')).children('pad')
        assert len(definition)==1 and definition[0].value()==pad.GetNumber()
        rows.append({'library_id':row['library_id'],'library_file':library.relative_to(root).as_posix(),
            'uuid':row['uuid'],'pad_number':pad.GetNumber(),'net':pad.GetNetname(),
            'position_mm':[pcbnew.ToMM(pad.GetPosition().x),pcbnew.ToMM(pad.GetPosition().y)],
            'size_mm':[pcbnew.ToMM(pad.GetSize().x),pcbnew.ToMM(pad.GetSize().y)],
            'drill_mm':[pcbnew.ToMM(pad.GetDrillSize().x),pcbnew.ToMM(pad.GetDrillSize().y)]})
    rows.sort(key=lambda r:r['library_id'])
    def cell(value): return str(value).replace('|','\\|').replace('\n',' ')
    lines=['# Project footprint index','',f'Library: `{nickname}`. Total definitions: {report["unique_footprint_count"]}.',
        '', '## Standalone PCB pads', '',
        'These single-pad objects already existed in the imported PCB without footprint names or references. '
        'Their pad numbers and networks are preserved. Do not merge different pad numbers into one library definition.',
        '', '| Footprint | Pad | Net | X / Y (mm) | Size (mm) | Drill (mm) |', '|---|---|---|---|---|---|']
    for row in rows:
        name=row['library_id'].split(':',1)[1]
        lines.append(f'| [{name}](../{row["library_file"]}) | {cell(row["pad_number"])} | {cell(row["net"] or "(none)")} | '
            + ' | '.join(' / '.join(f'{x:g}' for x in row[k]) for k in ('position_mm','size_mm','drill_mm'))+' |')
    standalone_ids={row['library_id'] for row in rows}
    groups={}
    for row in mapping['footprints']:
        if row['library_id'] not in standalone_ids:
            groups.setdefault(row['library_id'],[]).append(row['reference'] or row['uuid'])
    lines += ['', '## Named component footprints', '', '| Footprint | Board references |', '|---|---|']
    for lid,refs in sorted(groups.items()):
        name=lid.split(':',1)[1]
        lines.append(f'| [{name}](../01_Lib/{nickname}.pretty/{name}.kicad_mod) | {cell(", ".join(sorted(refs)))} |')
    data={'library_name':nickname,'standalone_pad_count':len(rows),
          'with_net':sum(bool(r['net']) for r in rows),'without_net':sum(not r['net'] for r in rows),'pads':rows}
    outputs={'AD2KICAD_FOOTPRINT_INDEX.md':'\n'.join(lines)+'\n',
             'AD2KICAD_STANDALONE_PADS.json':json.dumps(data,ensure_ascii=False,indent=2)+'\n'}
    for name,text in outputs.items():
        target=artifact_path(root,name)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists() or target.read_text(encoding='utf-8')!=text: target.write_text(text,encoding='utf-8')
    return {k:v for k,v in data.items() if k!='pads'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--native-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.native_worker:
        print(json.dumps(build_index(args.project),indent=2))
    else:
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--project',str(args.project),'--native-worker'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        except subprocess.TimeoutExpired as exc:
            detail=exc.stderr.decode('utf-8',errors='replace') if isinstance(exc.stderr,bytes) else (exc.stderr or '')
            raise RuntimeError('Native footprint indexing timed out after 120 seconds: '+detail[-2000:]) from exc
        print(result.stdout,end='')
        if result.stderr: print(result.stderr,file=sys.stderr,end='')
        raise SystemExit(result.returncode)
