"""Regressions for relocating tool artifacts without changing project assets."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from project_artifacts import artifact_path, find_artifact, relocate_legacy_artifacts

def checksum(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def verify(root,report):
    for name,value in report['output_hashes'].items():
        if checksum(root/name)!=value: raise RuntimeError('Hash mismatch: '+name)

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ad2kicad-artifacts-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'Board.kicad_pcb').write_bytes(b'unchanged physical project')
        (self.root/'AD2KICAD_MAPPING.json').write_text('{"symbols": []}',encoding='utf-8')
        (self.root/'AD2KICAD_FOOTPRINT_INDEX.md').write_text('[Part](01_Lib/Board.pretty/Part.kicad_mod)\n',encoding='utf-8')
        self.report={'output_hashes':{name:checksum(self.root/name) for name in ('Board.kicad_pcb','AD2KICAD_MAPPING.json')},
                     'run_directory':'_ad2kicad_work/run_saved','input_hashes':{'source':'untouched'}}
        self.original=json.dumps(self.report).encode('utf-8')
        (self.root/'AD2KICAD_REPORT.json').write_bytes(self.original)

    def test_move_rebases_metadata_hashes_and_index_without_design_changes(self):
        moves=relocate_legacy_artifacts(self.root,verify)
        self.assertEqual(len(moves),3)
        self.assertEqual(list(self.root.glob('AD2KICAD_*')),[])
        report=json.loads(artifact_path(self.root,'AD2KICAD_REPORT.json').read_text(encoding='utf-8'))
        self.assertEqual(report['run_directory'],self.report['run_directory'])
        self.assertEqual(report['input_hashes'],self.report['input_hashes'])
        verify(self.root,report)
        self.assertIn('_ad2kicad_work/AD2KICAD_MAPPING.json',report['output_hashes'])
        self.assertIn('](../01_Lib/',artifact_path(self.root,'AD2KICAD_FOOTPRINT_INDEX.md').read_text(encoding='utf-8'))
        self.assertEqual((self.root/report['artifact_relocation']['backup']/'legacy-root/AD2KICAD_REPORT.json').read_bytes(),self.original)
        self.assertEqual((self.root/'Board.kicad_pcb').read_bytes(),b'unchanged physical project')
        self.assertEqual(relocate_legacy_artifacts(self.root,verify),[])

    def test_conflicting_root_and_work_reports_are_not_overwritten(self):
        target=artifact_path(self.root,'AD2KICAD_REPORT.json')
        target.parent.mkdir();target.write_bytes(b'conflicting report')
        with self.assertRaisesRegex(RuntimeError,'Conflicting'):
            relocate_legacy_artifacts(self.root,verify)
        self.assertEqual(target.read_bytes(),b'conflicting report')
        self.assertEqual((self.root/'AD2KICAD_REPORT.json').read_bytes(),self.original)

    def test_verification_mode_does_not_migrate_legacy_files(self):
        with self.assertRaisesRegex(RuntimeError,'normal setup'):
            relocate_legacy_artifacts(self.root,verify,verify_only=True)
        self.assertFalse((self.root/'_ad2kicad_work').exists())
        self.assertEqual(find_artifact(self.root,'AD2KICAD_REPORT.json'),self.root/'AD2KICAD_REPORT.json')

    def test_changed_managed_asset_stops_before_relocation(self):
        (self.root/'Board.kicad_pcb').write_bytes(b'new user edit')
        with self.assertRaisesRegex(RuntimeError,'Hash mismatch'):
            relocate_legacy_artifacts(self.root,verify)
        self.assertFalse((self.root/'_ad2kicad_work').exists())
        self.assertEqual((self.root/'Board.kicad_pcb').read_bytes(),b'new user edit')

    def test_failed_postcopy_verification_restores_legacy_bytes(self):
        calls=[]
        def failing(root,report):
            calls.append(1)
            if len(calls)==2: raise RuntimeError('Injected postcopy failure')
            verify(root,report)
        with self.assertRaisesRegex(RuntimeError,'postcopy'):
            relocate_legacy_artifacts(self.root,failing)
        self.assertEqual((self.root/'AD2KICAD_REPORT.json').read_bytes(),self.original)
        self.assertFalse(artifact_path(self.root,'AD2KICAD_REPORT.json').exists())
        self.assertEqual((self.root/'AD2KICAD_FOOTPRINT_INDEX.md').read_text(encoding='utf-8'),'[Part](01_Lib/Board.pretty/Part.kicad_mod)\n')

if __name__=='__main__': unittest.main()
