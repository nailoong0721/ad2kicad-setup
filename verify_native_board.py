"""Load the final PCB with its actual same-stem project before publishing.

Run in a bounded subprocess: KiCad native assertions can otherwise hang the
calling process. Never remove or substitute the real project sidecar.
"""
import argparse
import json
from pathlib import Path
import pcbnew
from extract_footprints import native_directory


def verify(project, board_name, mapping):
    root = Path(project).resolve(strict=True)
    if Path(board_name).name != board_name:
        raise ValueError('Board name must be a basename')
    settings = json.loads((root / Path(board_name).with_suffix('.kicad_pro')).read_text(encoding='utf-8-sig'))
    tops = settings.get('schematic', {}).get('top_level_sheets', [])
    if not tops or any(Path(t['filename']).name != t['filename'] or not (root/t['filename']).is_file() for t in tops):
        raise ValueError('Native project top-level filenames must resolve at the project root')
    expected = {row['uuid']: row for row in mapping['footprint_map']}
    with native_directory(root):
        board = pcbnew.LoadBoard(board_name)
    if board is None:
        raise RuntimeError('KiCad failed to load the final board and its real project')
    actual = {fp.m_Uuid.AsString(): fp for fp in board.GetFootprints()}
    if set(actual) != set(expected):
        raise RuntimeError('Native board footprint UUIDs differ from the recorded mapping')
    model_count = 0
    for uuid, fp in actual.items():
        row = expected[uuid]
        if fp.GetFPIDAsString() != row['library_id'] or fp.GetReference() != row['reference']:
            raise RuntimeError('Native footprint mapping changed: ' + uuid)
        models = list(fp.Models())
        references = row.get('model_references', [])
        if len(models) != len(references):
            raise RuntimeError('Native footprint model count changed: ' + uuid)
        for model, reference in zip(models, references):
            if model.m_Filename != reference['target']:
                raise RuntimeError('Native board model path changed: ' + uuid)
        model_count += len(models)
    return {'passed': True, 'same_stem_project_loaded': True, 'footprints': len(actual),
            'model_references': model_count, 'top_level_schematics': len(tops)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--board', required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--json', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.project, args.board, json.loads(args.mapping.read_text(encoding='utf-8')))
    args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))
