"""Targeted regressions for real multi-top/XML import failures; no design edits."""
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from netlist_compat import read_netlist
from organize_project import schematic_topology


class ImportCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ad2kicad-compat-')
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_illegal_xml_control_is_restored_and_file_unchanged(self):
        path = self.root/'input.xml'
        content = '<export><comp ref="U1"><value>A\x03B</value></comp><net name="+5V_D\x03"/></export>'
        path.write_text(content, encoding='utf-8')
        with self.assertRaises(ET.ParseError):
            ET.parse(path)
        doc = read_netlist(path)
        self.assertEqual(doc.find('net').attrib['name'], '+5V_D\x03')
        self.assertEqual(doc.find('comp/value').text, 'A\x03B')
        self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_literal_escape_token_does_not_alias_actual_control(self):
        path = self.root/'collision.xml'
        literal = '__AD2KICAD_XML_CONTROL_000003__'
        path.write_text('<root a="'+literal+'">x\x03y</root>', encoding='utf-8')
        doc = read_netlist(path)
        self.assertEqual(doc.attrib['a'], literal)
        self.assertEqual(doc.text, 'x\x03y')

    def test_other_malformed_xml_is_still_rejected(self):
        path = self.root/'bad.xml'
        path.write_text('<root a="\x03"><broken></root>', encoding='utf-8')
        with self.assertRaises(ET.ParseError):
            read_netlist(path)

    def make_project(self, declared):
        (self.root/'Main.kicad_pro').write_text(json.dumps({'schematic':{'top_level_sheets':declared}}), encoding='utf-8')
        (self.root/'Main.kicad_sch').write_text('(kicad_sch (uuid "root-main"))', encoding='utf-8')
        (self.root/'MCU.kicad_sch').write_text('(kicad_sch (uuid "root-mcu"))', encoding='utf-8')

    def test_independent_top_circuits_keep_distinct_ownership(self):
        self.make_project([{'filename':'Main.kicad_sch','uuid':'stale-main'},
                           {'filename':'MCU.kicad_sch','uuid':'stale-mcu'}])
        tops, owners = schematic_topology(self.root, 'Main')
        self.assertEqual([row['filename'] for row in tops], ['Main.kicad_sch','MCU.kicad_sch'])
        self.assertEqual([row['uuid'] for row in tops], ['root-main','root-mcu'])
        self.assertEqual(owners, {'Main.kicad_sch':'Main.kicad_sch','MCU.kicad_sch':'MCU.kicad_sch'})

    def test_undeclared_orphan_is_rejected(self):
        self.make_project([{'filename':'Main.kicad_sch'}])
        with self.assertRaisesRegex(ValueError, 'orphan'):
            schematic_topology(self.root, 'Main')


if __name__ == '__main__':
    unittest.main()
