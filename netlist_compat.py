"""Read KiCad XML exports without deleting imported control characters.

Some imported labels contain characters forbidden by XML 1.0. KiCad writes
them literally. Protect only the parser input and restore every text value
after parsing; the exported file and design are never rewritten.
"""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

INVALID_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def read_netlist(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    if not INVALID_XML.search(text):
        return ET.fromstring(text)
    prefix = '__AD2KICAD_XML_CONTROL_'
    while prefix in text:
        prefix += '_'
    substitutions = {char: prefix + format(ord(char), '06X') + '__'
                     for char in set(INVALID_XML.findall(text))}
    protected = INVALID_XML.sub(lambda match: substitutions[match[0]], text)
    document = ET.fromstring(protected)
    def restore(value):
        if value is None:
            return None
        for char, token in substitutions.items():
            value = value.replace(token, char)
        return value
    for node in document.iter():
        node.text, node.tail = restore(node.text), restore(node.tail)
        node.attrib.update({key: restore(value) for key, value in node.attrib.items()})
    return document
