"""Open or validate the real root schematic beside its native KiCad project.

All independent top-level schematics remain at the project root. Only actual
hierarchical child schematics belong in 00_sheet. KiCad CLI processes its input
top-level schematic; each additional top is therefore validated explicitly.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

from sexpr import parse, edits, replace_atom


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_no_links(path):
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            value = item.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Refusing symlink or reparse-point path: ' + str(item))


def resolve_project(path):
    ensure_no_links(path.absolute())
    path = path.resolve(strict=True)
    if path.is_dir():
        files = list(path.glob('*.kicad_pro'))
        if len(files) != 1:
            raise ValueError('Expected exactly one .kicad_pro at project root')
        path = files[0]
    if path.suffix.lower() != '.kicad_pro' or not path.is_file():
        raise ValueError('--project must be a project directory or .kicad_pro file')
    root = path.parent
    settings = json.loads(path.read_text(encoding='utf-8-sig'))
    tops = settings.get('schematic', {}).get('top_level_sheets', [])
    if not tops or tops[0].get('filename') != path.stem + '.kicad_sch':
        raise ValueError('Native project must reference its project-level schematic at root')
    source = root / tops[0]['filename']
    ensure_no_links(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    if any(Path(item.get('filename', '')).name != item.get('filename', '') for item in tops):
        raise ValueError('Native top-level schematic filenames must not contain directories')
    root_schematics = list(root.glob('*.kicad_sch'))
    if {p.name for p in root_schematics} != {item['filename'] for item in tops}:
        raise ValueError('Root schematics must exactly match the declared independent top levels')
    document = parse(source.read_text(encoding='utf-8-sig'))
    if document.kind != 'kicad_sch' or tops[0].get('uuid') != document.child('uuid').value():
        raise ValueError('Native project and root schematic UUID do not match')
    sheets = root / '00_sheet'
    ensure_no_links(sheets)
    if not sheets.is_dir():
        raise FileNotFoundError(sheets)
    unexpected = [str(p) for p in sheets.iterdir() if not p.is_file() or p.suffix != '.kicad_sch']
    if unexpected:
        raise ValueError('00_sheet contains non-schematic entries: ' + ', '.join(unexpected))
    for child in sheets.iterdir():
        ensure_no_links(child)
    documents = {file.resolve(): parse(file.read_text(encoding='utf-8-sig'))
                 for file in [*root_schematics, *sheets.glob('*.kicad_sch')]}
    top_paths = []
    for item in tops:
        relative = Path(item.get('filename', ''))
        if relative.is_absolute() or '..' in relative.parts or '${' in str(relative):
            raise ValueError('Top-level schematic must use a project-relative path')
        target = (root / relative).resolve(strict=True)
        if target not in documents or target in top_paths:
            raise ValueError('Unknown or duplicate top-level schematic: ' + str(relative))
        if documents[target].child('uuid').value() != item.get('uuid'):
            raise ValueError('Native project and top-level schematic UUID do not match: ' + str(relative))
        top_paths.append(target)
    visited, active = set(), set()
    def visit(file):
        if file in active:
            raise ValueError('Cyclic schematic hierarchy')
        if file in visited:
            return
        active.add(file)
        for sheet in documents[file].children('sheet'):
            field = sheet.prop('Sheetfile')
            relative = Path(field.value(2)) if field is not None else None
            if relative is None or relative.is_absolute() or '..' in relative.parts or '${' in str(relative):
                raise ValueError('Child sheet must use a project-relative Sheetfile path')
            target = (file.parent / relative).resolve(strict=True)
            if target.parent != sheets or target not in documents:
                raise ValueError('Child sheet must resolve inside 00_sheet: ' + str(relative))
            visit(target)
        active.remove(file)
        visited.add(file)
    for top_path in top_paths:
        visit(top_path)
    if visited != set(documents):
        raise ValueError('Schematic is neither a declared top-level sheet nor a reachable child')
    return path, source


def additional_top_context(project, source, output):
    """Supply project settings/library resolution without real 00_sheet sidecars.

    CLI loads settings beside the requested schematic, rather than traversing
    its parent project's other top-level sheets. Preserve every schematic byte
    and relative child path in an audit-only mirror, then give the requested
    top a same-stem project and tables pointing to the real project libraries.
    """
    root = project.parent
    mirror = Path(tempfile.mkdtemp(prefix='top-context-', dir=str(output)))
    copied = {}
    for original in [*root.glob('*.kicad_sch'), *(root/'00_sheet').glob('*.kicad_sch')]:
        destination = mirror / original.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
        copied[destination.relative_to(mirror).as_posix()] = digest_file(original)
        if digest_file(destination) != digest_file(original):
            raise RuntimeError('Validation mirror differs from original schematic')
    mirrored_source = mirror / source.relative_to(root)
    settings = json.loads(project.read_text(encoding='utf-8-sig'))
    metadata = next(item for item in settings['schematic']['top_level_sheets']
                    if (root/item['filename']).resolve() == source.resolve())
    settings['schematic']['top_level_sheets'] = [{**metadata, 'filename': source.name}]
    settings['sheets'] = [[metadata['uuid'], metadata.get('name', source.stem)]]
    context_project = mirrored_source.with_suffix('.kicad_pro')
    context_project.write_text(json.dumps(settings, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for name in ('sym-lib-table', 'fp-lib-table'):
        original = root / name
        text = original.read_text(encoding='utf-8-sig')
        replacements = []
        for lib in parse(text).children('lib'):
            uri = lib.child('uri')
            if uri is None:
                raise ValueError('Library table entry has no URI: ' + name)
            value = uri.value().replace('${KIPRJMOD}', root.as_posix())
            if '${' not in value and not Path(value).is_absolute():
                value = (root/value).resolve().as_posix()
            replacements.append(replace_atom(uri.items[1], value))
        (mirrored_source.parent/name).write_text(edits(text, replacements), encoding='utf-8')
    return mirrored_source, {'kind': 'byte_preserving_audit_mirror',
                            'directory': str(mirror), 'context_project': str(context_project),
                            'real_library_root': str(root), 'schematic_sha256': copied}


def run_cli(command, logfile):
    started = time.perf_counter()
    result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', timeout=180)
    logfile.write_text(result.stdout, encoding='utf-8')
    if result.returncode != 0:
        raise RuntimeError(f'KiCad exit {result.returncode}; see {logfile}')
    return {'exit_code': result.returncode, 'seconds': round(time.perf_counter()-started, 3),
            'log': str(logfile)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--kicad-cli', required=True, type=Path)
    parser.add_argument('--mode', choices=('validate', 'gui'), required=True)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    project, source = resolve_project(args.project)
    cli = args.kicad_cli.resolve(strict=True)
    if cli.name.lower() != 'kicad-cli.exe':
        raise ValueError('--kicad-cli must point to kicad-cli.exe')
    root = project.parent
    sheets = root / '00_sheet'
    if args.mode == 'validate' and args.output_dir is None:
        raise ValueError('--output-dir is required for validation')
    output = args.output_dir.absolute() if args.output_dir else None
    if output:
        ensure_no_links(output)
        output = output.resolve()
        if output == sheets or sheets in output.parents:
            raise ValueError('Reports must not be written inside 00_sheet')
        output.mkdir(parents=True, exist_ok=True)
    original_schematics = {str(p): digest_file(p) for p in [*root.glob('*.kicad_sch'), *sheets.glob('*.kicad_sch')]}
    report = {'schema_version': 2, 'mode': args.mode, 'project': str(project),
              'real_root_schematic': str(source), 'layout': 'native_top_levels_at_root_with_hierarchical_children',
              'gui_interactive_validation': False}
    operation_error = None
    try:
        if args.mode == 'validate':
            settings = json.loads(project.read_text(encoding='utf-8-sig'))
            tops = settings['schematic']['top_level_sheets']
            report['top_level_schematic_count'] = len(tops)
            report['top_level_results'] = []
            counts = {}
            for index, item in enumerate(tops):
                real_source = (root/item['filename']).resolve(strict=True)
                result_dir = output if index == 0 else output/('top-level-' + str(index+1))
                result_dir.mkdir(parents=True, exist_ok=True)
                cli_source, context = real_source, {'kind': 'real_project'}
                if index:
                    cli_source, context = additional_top_context(project, real_source, result_dir)
                erc_path, net_path = result_dir/'erc.json', result_dir/'netlist.xml'
                erc_run = run_cli([cli, 'sch', 'erc', '--format', 'json', '-o', erc_path, cli_source], result_dir/'erc.log')
                net_run = run_cli([cli, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', net_path, cli_source], result_dir/'netlist.log')
                erc = json.loads(erc_path.read_text(encoding='utf-8'))
                top_counts = {}
                for sheet in erc.get('sheets', []):
                    for violation in sheet.get('violations', []):
                        kind = violation['type']
                        top_counts[kind] = top_counts.get(kind, 0) + 1
                        counts[kind] = counts.get(kind, 0) + 1
                if index:
                    directory = Path(context['directory'])
                    context['schematic_bytes_unchanged'] = all(
                        digest_file(directory/rel) == checksum
                        for rel, checksum in context['schematic_sha256'].items())
                    if not context['schematic_bytes_unchanged']:
                        raise RuntimeError('KiCad modified a schematic in the validation mirror')
                else:
                    report['erc'], report['netlist'] = erc_run, net_run
                report['top_level_results'].append({
                    'filename': real_source.relative_to(root).as_posix(),
                    'netlist': str(net_path), 'erc': str(erc_path),
                    'erc_counts': top_counts, 'erc_run': erc_run,
                    'netlist_run': net_run, 'validation_context': context})
            report['erc_counts'] = counts
            report['all_top_level_schematics_validated'] = len(report['top_level_results']) == len(tops)
            report['schematic_content_unchanged'] = all(Path(p).is_file() and digest_file(Path(p)) == checksum for p, checksum in original_schematics.items())
            if not report['schematic_content_unchanged']:
                raise RuntimeError('A schematic changed during validation')
        else:
            executable = cli.with_name('eeschema.exe')
            if not executable.is_file():
                raise FileNotFoundError(executable)
            print('Opening the project-level schematic with its native root project.', flush=True)
            process = subprocess.Popen([str(executable), str(source)], cwd=str(root))
            report['gui_exit_code'] = process.wait()
            if process.returncode:
                raise RuntimeError('Eeschema exited with code ' + str(process.returncode))
    except BaseException as exc:
        operation_error = exc
    finally:
        report['root_schematic_preserved'] = source.is_file()
        report['00_sheet_contains_only_schematics'] = all(p.is_file() and p.suffix == '.kicad_sch' for p in sheets.iterdir())
        if operation_error:
            report['error'] = str(operation_error)
        if output:
            (output/'session_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if operation_error:
        raise operation_error


if __name__ == '__main__':
    main()
