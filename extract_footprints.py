#!/usr/bin/env python3
"""Extract readable PCB footprints to one portable project library.

Run with the Python bundled beside the user-selected kicad-cli.exe (pcbnew).
The source PCB is read-only. The caller maps the returned UUIDs in the PCB
using a lossless edit; this helper never serializes or changes that board.
Original names remain readable (DIP-2.kicad_mod, without hash suffixes). Keep
true pad variants separate with descriptive suffixes. Archive silkscreen-only
variations and leave their placed board instances unchanged.
Real embedded or external 3D models are copied to one project model directory.
Missing assets and model-free footprints remain explicitly reported; no model
is fabricated and no model transformation is discarded.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

import pcbnew

from model_assets import ModelCollector


def parse_sexpr(text):
    """Parse generated KiCad text, retaining quoted versus unquoted atoms."""
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', text)
    stack = []
    root = None
    for token in tokens:
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            if not stack:
                raise ValueError('Unbalanced KiCad expression')
            node = stack.pop()
            if not stack:
                if root is not None:
                    raise ValueError('Multiple KiCad expression roots')
                root = node
        else:
            if not stack:
                raise ValueError('Atom outside KiCad expression')
            stack[-1].append(token)
    if stack or root is None:
        raise ValueError('Incomplete KiCad expression')
    return root


def atom_text(atom):
    if isinstance(atom, str) and atom.startswith('"'):
        return json.loads(atom)
    return atom


IGNORE_TAGS = {
    'uuid', 'tstamp', 'path', 'sheetname', 'sheetfile', 'net',
    'pinfunction', 'pintype', 'locked', 'unlocked', 'property',
    'version', 'generator', 'generator_version',
}


def canonical(node, root=False):
    """Compare all native geometry; omit board and field identity only.

    All pads, pad numbers, drills, padstacks, layers, offsets, polygons,
    manufacturing margins, zones, and technical drawings remain significant.
    Reference/value/property layout is instance-specific and ignored. Native
    normalization (front side, zero orientation/position) happens first.
    """
    if not isinstance(node, list):
        return node
    if not node:
        return []
    tag = node[0]
    if tag in IGNORE_TAGS:
        return None
    if tag == 'fp_text' and len(node) > 1 and node[1] in ('reference', 'value'):
        return None
    result = [tag]
    for index, child in enumerate(node[1:], 1):
        if root and index == 1:
            result.append('"FOOTPRINT"')
            continue
        normalized = canonical(child)
        if normalized is not None:
            result.append(normalized)
    # Line direction has no geometric significance. Native rotation/flipping
    # can swap these endpoints, which must not invent a separate variant.
    if tag in ('fp_line', 'fp_rect'):
        start = next((item for item in result if isinstance(item, list) and item[0] == 'start'), None)
        end = next((item for item in result if isinstance(item, list) and item[0] == 'end'), None)
        if start is not None and end is not None and repr(start[1:]) > repr(end[1:]):
            start[1:], end[1:] = end[1:], start[1:]
    # A circle's endpoint chooses an arbitrary point on the circumference.
    # Compare exact squared radius, so flipping/rotating the endpoint does not
    # turn an unchanged circle into a distinct footprint definition.
    if tag == 'fp_circle':
        center = next((item for item in result if isinstance(item, list) and item[0] == 'center'), None)
        end = next((item for item in result if isinstance(item, list) and item[0] == 'end'), None)
        if center is not None and end is not None:
            radius2 = sum((Decimal(end[i]) - Decimal(center[i])) ** 2 for i in (1, 2))
            end[:] = ['radius_squared', format(radius2.normalize(), 'f')]
    # Independent drawings/pads can be in different source order. Sorting
    # footprint children is safe; point sequences inside a polygon stay ordered.
    if root:
        result = result[:2] + sorted(result[2:], key=repr)
    return result


def geometry_digest(text):
    tree = canonical(parse_sexpr(text), root=True)
    return sha256(json.dumps(tree, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def tree_digest(tree):
    return sha256(json.dumps(tree, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def functional_tree(tree):
    """Keep pads, numbering, drills, padstacks and all non-silkscreen data.

    The original board retains instance-specific silkscreen. Its variants are
    archived individually; only the reusable library uses a representative.
    No pad, copper, mask, paste, courtyard, fab, zone or model data is omitted.
    """
    result = tree[:2]
    for child in tree[2:]:
        layer = next((x[1] for x in child if isinstance(x, list) and x[0] == 'layer'), None) if isinstance(child, list) else None
        if isinstance(child, list) and child[0].startswith('fp_') and layer in ('"F.SilkS"', '"B.SilkS"'):
            continue
        result.append(child)
    return result


def child(node, tag):
    return next((x for x in node if isinstance(x, list) and x and x[0] == tag), None)


def natural_key(text):
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r'(\d+)', text))


def plain_number(value):
    return format(Decimal(str(value)).normalize(), 'f')


def pads(tree):
    return [x for x in tree[2:] if isinstance(x, list) and x[0] == 'pad']


def without_numbers(items):
    return sorted([x[:1] + ['"PAD"'] + x[2:] for x in items], key=repr)


def variant_suffix(primary, alternate, reference):
    """Name real pad differences in readable engineering terms."""
    a, b = pads(primary), pads(alternate)
    if len(a) == len(b) and without_numbers(a) == without_numbers(b):
        return 'ReversedPins' if len(a) == 2 else 'AlternatePinNumbers'
    if any(child(x, 'padstack') is not None for x in b) and not any(child(x, 'padstack') is not None for x in a):
        return 'LayerPadstack'
    if any(child(x, 'padstack') is not None for x in a) and not any(child(x, 'padstack') is not None for x in b):
        return 'UniformPadstack'
    if len(a) == len(b) == 2:
        apos = [child(x, 'at') for x in a]
        bpos = [child(x, 'at') for x in b]
        if all(apos) and all(bpos):
            ad2 = sum((Decimal(apos[0][i]) - Decimal(apos[1][i])) ** 2 for i in (1, 2))
            bd2 = sum((Decimal(bpos[0][i]) - Decimal(bpos[1][i])) ** 2 for i in (1, 2))
            if ad2 != bd2:
                return 'Pitch' + plain_number(bd2.sqrt()) + 'mm'
    return 'For_' + slug(reference or 'Alternate')


def anonymous_name(tree):
    items = pads(tree)
    if len(items) == 1:
        pad = items[0]
        number = slug(atom_text(pad[1]) or 'Unnumbered')
        name = 'Pad_' + number + ('_THT' if pad[2] == 'thru_hole' else '_SMD' if pad[2] == 'smd' else '_NPTH')
        size = child(pad, 'size')
        if size:
            dimensions = plain_number(size[1]) if size[1] == size[2] else plain_number(size[1]) + 'x' + plain_number(size[2])
            name += ('_D' if pad[3] == 'circle' else '_' + pad[3] + '_') + dimensions + 'mm'
        drill = child(pad, 'drill')
        if drill:
            name += '_Drill' + 'x'.join(plain_number(x) for x in drill[1:] if re.fullmatch(r'-?\d+(\.\d+)?', x)) + 'mm'
        mask = child(pad, 'solder_mask_margin')
        if mask:
            name += '_Mask' + plain_number(mask[1]) + 'mm'
        return name
    return 'Imported_' + str(len(items)) + 'Pads'


def slug(text):
    safe = re.sub(r'[^A-Za-z0-9_.+-]+', '_', text).strip(' ._') or 'Unnamed'
    safe = safe[:72]
    if safe.upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', safe.upper()):
        safe = 'FP_' + safe
    return safe


def normalized_duplicate(source):
    copy = pcbnew.Cast_to_FOOTPRINT(source.Duplicate(False))
    copy.SetLayerAndFlip(pcbnew.F_Cu)
    copy.SetOrientationDegrees(0)
    copy.SetPosition(pcbnew.VECTOR2I(0, 0))
    copy.SetFPIDAsString('AD2Kicad:__candidate__')
    copy.SetReference('REF**')
    copy.SetValue('__candidate__')
    copy.SetPath(pcbnew.KIID_PATH())
    copy.SetSheetfile('')
    copy.SetSheetname('')
    copy.SetLocked(False)
    copy.ClearAllNets()
    for pad in copy.Pads():
        pad.SetNetCode(0)
        pad.SetPinFunction('')
        pad.SetPinType('')
    return copy


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def native_path(path):
    """Short relative paths avoid Windows wx/native API MAX_PATH failures."""
    return os.path.relpath(str(Path(path).resolve()), os.getcwd())


@contextmanager
def native_directory(directory):
    previous = Path.cwd()
    try:
        os.chdir(directory)
        yield
    finally:
        os.chdir(previous)


def extract(pcb, output_root, nickname='AD2Kicad', project_root=None, kicad_cli=None, preserved_models=None):
    """Scope a short-path cwd to extraction and restore the caller's cwd."""
    pcb = Path(pcb).resolve()
    output_root = Path(output_root).resolve()
    previous_cwd = Path.cwd()
    # Keep Python reads/writes and reports absolute. pcbnew/wx calls receive
    # paths relative to the nearest common existing parent of source/output.
    try:
        common = Path(os.path.commonpath((str(pcb.parent), str(output_root))))
    except ValueError:
        # Source and destination may be on different Windows volumes.
        common = pcb.parent
    while not common.is_dir():
        common = common.parent
    try:
        os.chdir(common)
        return _extract(pcb, output_root, nickname, project_root, kicad_cli, preserved_models)
    finally:
        os.chdir(previous_cwd)


def _extract(pcb, output_root, nickname='AD2Kicad', project_root=None, kicad_cli=None, preserved_models=None):
    pcb = Path(pcb).resolve()
    output_root = Path(output_root).resolve()
    if output_root == pcb.parent or pcb.parent in output_root.parents:
        raise ValueError('Output must not be inside the source PCB directory')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', nickname):
        raise ValueError('Library nickname must be alphanumeric, underscore or hyphen')
    source_hash = sha256(pcb.read_bytes()).hexdigest()
    output_root.mkdir(parents=True, exist_ok=True)
    pretty = output_root / (nickname + '.pretty')
    models = output_root / (nickname + '.3dmodels')
    pretty.mkdir(exist_ok=True)
    models.mkdir(exist_ok=True)
    collector = ModelCollector(pcb, output_root, nickname, project_root, kicad_cli, preserved_models)
    board = pcbnew.LoadBoard(native_path(pcb))
    originals = sorted(board.GetFootprints(), key=lambda item: item.m_Uuid.AsString())
    if not originals:
        raise ValueError('Source board contains no footprints')
    # The generic pcbnew.FootprintSave guesses the plugin from an empty
    # directory incorrectly on KiCad 10. Explicit native format is reliable.
    native = pcbnew.PCB_IO_KICAD_SEXPR()
    records = []
    # KiCad's Windows footprint plugin internally expands even relative paths
    # to absolute paths and aborts beyond MAX_PATH. Keep native scratch files
    # under the user's short temporary directory; Python writes the validated
    # bytes to the actual (potentially deeply nested) project library.
    with tempfile.TemporaryDirectory(prefix='ad2fp_') as temporary:
        scratch = Path(temporary) / 'scratch.pretty'
        scratch.mkdir()
        for source in originals:
            old_name = source.GetFPIDAsString()
            ref = source.GetReference()
            source_uuid = source.m_Uuid.AsString()
            model_references = [collector.collect(source_uuid, index, model.m_Filename)
                                for index, model in enumerate(source.Models())]
            copy = normalized_duplicate(source)
            native_models = copy.Models()
            original_transforms = [(str(model.m_Offset), str(model.m_Rotation), str(model.m_Scale))
                                   for model in native_models]
            for index, model in enumerate(native_models):
                model.m_Filename = model_references[index]['target']
                # SWIG iteration yields value copies; assigning the vector slot
                # is required for the native footprint to retain this edit.
                native_models[index] = model
            if [item.m_Filename for item in copy.Models()] != [item['target'] for item in model_references]:
                raise RuntimeError('Native model path rewrite failed: ' + source_uuid)
            if [(str(model.m_Offset), str(model.m_Rotation), str(model.m_Scale))
                    for model in copy.Models()] != original_transforms:
                raise RuntimeError('Model transformation changed during asset relocation: ' + source_uuid)
            with native_directory(scratch.parent):
                native.FootprintSave(native_path(scratch), copy)
            serialized = (scratch / '__candidate__.kicad_mod').read_text(encoding='utf-8')
            tree = canonical(parse_sexpr(serialized), root=True)
            records.append({
                'uuid': source_uuid, 'reference': ref, 'old_name': old_name,
                'path': source.GetPath().AsString(), 'value': source.GetValue(),
                'geometry_sha256': tree_digest(tree), 'pad_count': source.GetPadCount(),
                'functional_sha256': tree_digest(functional_tree(tree)),
                'model_status': ('missing_from_source' if not model_references else
                                 'collected' if all(x['status'] == 'collected' for x in model_references)
                                 else 'missing_assets'),
                'model_references': model_references, '_tree': tree, '_native': copy,
            })

        def record_key(row):
            return natural_key(row['reference']), row['uuid']

        def representative(rows):
            variants = defaultdict(list)
            for row in rows:
                variants[row['geometry_sha256']].append(row)
            chosen = min(variants.values(), key=lambda items: (-len(items), record_key(min(items, key=record_key))))
            return min(chosen, key=record_key)

        by_original = defaultdict(list)
        for row in records:
            by_original[row['old_name']].append(row)
        reserved = {slug(name.split(':')[-1]).casefold() for name in by_original if name}
        used_names = set()

        def allocate(base, original=False):
            name = base
            index = 2
            while name.casefold() in used_names or (not original and name.casefold() in reserved):
                name = base + '_Variant' + str(index)
                index += 1
            used_names.add(name.casefold())
            return name

        groups = []
        true_variants = []
        for original_name, items in sorted(by_original.items(), key=lambda kv: natural_key(kv[0])):
            functional_groups = defaultdict(list)
            for row in items:
                functional_groups[row['functional_sha256']].append(row)
            ordered = sorted(functional_groups.values(), key=lambda rows: (
                -len(rows), len(json.dumps(functional_tree(rows[0]['_tree']))),
                record_key(min(rows, key=record_key))))
            primary = representative(ordered[0])
            for index, rows in enumerate(ordered):
                selected = representative(rows)
                if original_name:
                    base = slug(original_name.split(':')[-1])
                    if index:
                        base += '_' + variant_suffix(primary['_tree'], selected['_tree'], selected['reference'])
                    name = allocate(base, original=(index == 0))
                else:
                    name = allocate(anonymous_name(selected['_tree']))
                groups.append({'name': name, 'rows': rows, 'selected': selected})
                if original_name and index:
                    true_variants.append({
                        'original_name': original_name, 'footprint_name': name,
                        'references': sorted([r['reference'] for r in rows if r['reference']], key=natural_key),
                        'reason': 'Distinct pad geometry, numbering, padstack or other non-silkscreen data',
                    })

        unique = []
        mapping = []
        by_reference = defaultdict(list)
        missing = []
        silkscreen_variants = []
        archive_count = 0

        def save_native(row, folder, name):
            copy = row['_native']
            copy.SetFPIDAsString(nickname + ':' + name)
            copy.SetValue(name)
            with native_directory(scratch.parent):
                native.FootprintSave(native_path(scratch), copy)
            native_saved = scratch / (name + '.kicad_mod')
            if geometry_digest(native_saved.read_text(encoding='utf-8')) != row['geometry_sha256']:
                raise RuntimeError('Geometry changed during library save: ' + name)
            with native_directory(scratch.parent):
                loaded = native.FootprintLoad(native_path(scratch), name)
            if loaded is None or loaded.GetPadCount() != row['pad_count']:
                raise RuntimeError('Native footprint load failed: ' + name)
            if any(pad.GetNetCode() != 0 for pad in loaded.Pads()):
                raise RuntimeError('Board nets leaked into the library: ' + name)
            if loaded.GetPath().AsString():
                raise RuntimeError('Board schematic path leaked into library: ' + name)
            if [item.m_Filename for item in loaded.Models()] != [item['target'] for item in row['model_references']]:
                raise RuntimeError('Model path changed during native library save: ' + name)
            saved = folder / (name + '.kicad_mod')
            if saved != native_saved:
                saved.write_bytes(native_saved.read_bytes())
                if saved.read_bytes() != native_saved.read_bytes():
                    raise RuntimeError('Verified native footprint changed during publish: ' + name)
            return saved

        for group in groups:
            name, rows, selected = group['name'], group['rows'], group['selected']
            saved = save_native(selected, pretty, name)
            full_variants = defaultdict(list)
            for row in rows:
                full_variants[row['geometry_sha256']].append(row)
            archive_paths = {}
            for variant_rows in sorted(full_variants.values(), key=lambda rr: record_key(min(rr, key=record_key))):
                row = min(variant_rows, key=record_key)
                if row['geometry_sha256'] == selected['geometry_sha256']:
                    continue
                archive_name = name + '_For_' + slug(row['reference'] or 'Imported')
                archive_file = save_native(row, scratch, archive_name)
                relative = Path('AD_lib_bak') / 'footprint_variants' / name / (archive_name + '.kicad_mod')
                archive = output_root / relative
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(archive_file.read_bytes())
                archive_paths[row['geometry_sha256']] = relative.as_posix()
                archive_count += 1
            unique.append({
                'footprint_name': name, 'library_id': nickname + ':' + name,
                'geometry_sha256': selected['geometry_sha256'],
                'functional_sha256': selected['functional_sha256'],
                'pad_count': selected['pad_count'], 'library_file': str(saved),
                'source_uuids': [row['uuid'] for row in rows],
                'source_names': sorted({row['old_name'] for row in rows}),
                'references': sorted({row['reference'] for row in rows if row['reference']}, key=natural_key),
                'representative_reference': selected['reference'], 'representative_uuid': selected['uuid'],
                'source_geometry_variant_count': len(full_variants),
                'model_status': selected['model_status'], 'model_references': selected['model_references'],
                'approximate_model': False,
            })
            if len(full_variants) > 1:
                silkscreen_variants.append({
                    'footprint_name': name, 'representative_reference': selected['reference'],
                    'instance_count': len(rows), 'variant_count': len(full_variants),
                    'archive_files': list(archive_paths.values()),
                    'policy': 'Only silkscreen differs; PCB graphics remain unchanged. Library uses most frequent variant, then natural reference order.',
                })
            for row in rows:
                record = {key: value for key, value in row.items() if not key.startswith('_')}
                record.update({
                    'footprint_name': name, 'library_id': nickname + ':' + name,
                    'library_geometry_sha256': selected['geometry_sha256'],
                    'library_functional_sha256': selected['functional_sha256'],
                    'library_graphics_match': row['geometry_sha256'] == selected['geometry_sha256'],
                    'library_functional_match': row['functional_sha256'] == selected['functional_sha256'],
                    'archived_variant': archive_paths.get(row['geometry_sha256']),
                })
                if not record['library_functional_match']:
                    raise RuntimeError('Pad, numbering or other functional data differs from assigned library: ' + row['uuid'])
                mapping.append(record)
                if row['reference']:
                    by_reference[row['reference']].append(record)
                if not row['model_references']:
                    missing.append({
                        'uuid': row['uuid'], 'reference': row['reference'], 'old_name': row['old_name'],
                        'library_id': record['library_id'], 'reason': 'No 3D model entry in original PCB',
                    })
                for model in row['model_references']:
                    if model['status'] == 'missing':
                        missing.append(dict(model, uuid=row['uuid'], reference=row['reference'],
                                            old_name=row['old_name'], library_id=record['library_id']))

    expected = {item['footprint_name'] + '.kicad_mod' for item in unique}
    unexpected = {item.name for item in pretty.iterdir()} - expected
    if unexpected:
        raise ValueError('Destination contains stale/unexpected entries: ' + repr(sorted(unexpected)))
    collector.verify()
    if sha256(pcb.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Source board changed while extracting')
    report = {
        'schema_version': 3, 'kicad_version': pcbnew.GetBuildVersion(),
        'source_pcb': str(pcb), 'source_sha256': source_hash,
        'nickname': nickname, 'pretty_path': str(pretty), 'models_path': str(models),
        'pcb_footprint_count': len(originals), 'unique_footprint_count': len(unique),
        'model_mode': 'collected' if collector.assets else 'empty',
        'generated_model_count': 0, 'approximate_model_count': 0,
        'collected_model_count': len(collector.assets),
        'model_reference_count': sum(len(row['model_references']) for row in mapping),
        'model_assets': collector.assets,
        'missing_model_count': len({row['uuid'] for row in missing}),
        'missing_model_reference_count': sum(row.get('status') == 'missing' for row in missing),
        'model_limitation': 'Real source model bytes were copied and deduplicated. Missing models were not fabricated; unresolved references remain unchanged.',
        'normalization': 'Native duplicate, front side, zero position/orientation; identity metadata excluded; exact circle radius and reversed straight-line direction normalized. Original names retained; distinct pads/numbering/padstacks use readable variant names. Only silkscreen differences share a library representative.',
        'naming_policy': 'Original footprint names without hash suffix; meaningful pad variants retained; anonymous pads named by pad number/type/size/drill/mask.',
        'named_source_family_count': sum(bool(name) for name in by_original),
        'true_pad_variants': true_variants,
        'silkscreen_variant_groups': silkscreen_variants,
        'archived_silkscreen_variant_count': archive_count,
        'instance_library_graphics_differences': sum(not row['library_graphics_match'] for row in mapping),
        'functional_mapping_mismatch_count': sum(not row['library_functional_match'] for row in mapping),
        'footprint_map': mapping,
        'by_reference': dict(by_reference),
        'unreferenced_footprints': [item for item in mapping if not item['reference']],
        'unique_footprints': sorted(unique, key=lambda item: item['footprint_name']),
        'missing_models': missing,
        'verified': {'source_unchanged': True, 'all_native_library_loads': True,
                     'representative_and_archived_geometry_roundtrip': True,
                     'model_asset_checksums_and_native_paths': True,
                     'model_transforms_unchanged_by_asset_relocation': True,
                     'all_instance_pad_numbering_and_non_silkscreen_data_match_library': True,
                     'no_hash_suffixes_in_library_filenames': all(not re.search(r'__[0-9a-fA-F]{8,}$', item['footprint_name']) for item in unique),
                     'no_library_nets_or_paths': True},
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pcb', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--nickname', default='AD2Kicad')
    parser.add_argument('--json', required=True, type=Path)
    parser.add_argument('--project-root', type=Path, help='Original project root for resolving relative external models')
    parser.add_argument('--kicad-cli', type=Path, help='Selected KiCad executable; selects native codecs and standard model libraries')
    parser.add_argument('--preserved-models', type=Path, help='Verified previous model asset manifest for migration')
    parser.add_argument('--model-mode', choices=['auto', 'empty'], default='auto',
                        help='Legacy empty option retained; source model assets are always preserved')
    args = parser.parse_args()
    report = extract(args.pcb, args.output_root, args.nickname, args.project_root, args.kicad_cli, args.preserved_models)
    write_json(args.json, report)
    print(json.dumps({key: report[key] for key in (
        'pcb_footprint_count', 'unique_footprint_count', 'generated_model_count', 'collected_model_count',
        'missing_model_count', 'pretty_path', 'models_path', 'verified')}, indent=2))


if __name__ == '__main__':
    main()
