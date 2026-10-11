"""Collect real KiCad model assets without changing model transformations.

Embedded files are base64-encoded Zstandard frames. KiCad uses MurmurHash3
128-bit checksums; both the current and its legacy tail-padding checksum
are accepted, as in KiCad's embedded_files.cpp. No asset is downloaded or
fabricated. Unresolved paths remain intact and are reported explicitly.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import struct
import sys

from sexpr import Atom, parse


MASK = (1 << 64) - 1
MAX_MODEL_BYTES = 1024 * 1024 * 1024


def _rotate(value, count):
    return ((value << count) | (value >> (64 - count))) & MASK


def _mix(value):
    value ^= value >> 33
    value = value * 0xff51afd7ed558ccd & MASK
    value ^= value >> 33
    value = value * 0xc4ceb9fe1a85ec53 & MASK
    return value ^ (value >> 33)


def embedded_checksum(data, legacy=False):
    """MurmurHash3 x64/128 with KiCad's embedded-file seed/display order."""
    h1 = h2 = 0xABBA2345
    c1, c2 = 0x87c37b91114253d5, 0x4cf5ad432745937f
    full = len(data) // 16 * 16
    for k1, k2 in struct.iter_unpack('<QQ', memoryview(data)[:full]):
        h1 ^= _rotate(k1 * c1 & MASK, 31) * c2 & MASK
        h1 = (_rotate(h1, 27) + h2) & MASK
        h1 = (h1 * 5 + 0x52dce729) & MASK
        h2 ^= _rotate(k2 * c2 & MASK, 33) * c1 & MASK
        h2 = (_rotate(h2, 31) + h1) & MASK
        h2 = (h2 * 5 + 0x38495ab5) & MASK
    length = len(data)
    tail = data[full:]
    if legacy and tail:
        padding = 4 - len(tail) % 4
        tail += bytes(padding)
        length += padding
        tail = tail[:length % 16]
    if len(tail) > 8:
        h2 ^= _rotate(int.from_bytes(tail[8:], 'little') * c2 & MASK, 33) * c1 & MASK
    if tail:
        h1 ^= _rotate(int.from_bytes(tail[:8], 'little') * c1 & MASK, 31) * c2 & MASK
    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & MASK
    h2 = (h2 + h1) & MASK
    h1, h2 = _mix(h1), _mix(h2)
    h1 = (h1 + h2) & MASK
    h2 = (h2 + h1) & MASK
    return f'{h1:016X}{h2:016X}'


class ModelCollector:
    def __init__(self, pcb, output_root, nickname, project_root=None, kicad_cli=None, preserved_models=None):
        self.pcb = Path(pcb).resolve()
        self.project_root = Path(project_root or self.pcb.parent).resolve()
        self.output_root = Path(output_root).resolve()
        self.nickname = nickname
        self.folder = self.output_root / (nickname + '.3dmodels')
        self.folder.mkdir(parents=True, exist_ok=True)
        self.kicad_bin = Path(kicad_cli).resolve().parent if kicad_cli else Path(sys.executable).resolve().parent
        self.variables = dict(os.environ)
        self._load_variables()
        document = parse(self.pcb.read_text(encoding='utf-8'))
        self.board_embedded = self._embedded(document)
        self.footprint_embedded = {}
        for footprint in document.children('footprint'):
            uuid = footprint.child('uuid') or footprint.child('tstamp')
            if uuid:
                self.footprint_embedded[uuid.value()] = self._embedded(footprint)
        self.assets = []
        self._by_hash = {}
        self._cache = {}
        self._zstd = None
        self._preserved = self._load_preserved(preserved_models)

    @staticmethod
    def _load_preserved(manifest):
        if manifest is None:
            return {}
        document = json.loads(Path(manifest).read_text(encoding='utf-8'))
        if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('entries'), list):
            raise ValueError('Invalid preserved-model manifest schema')
        result = {}
        for entry in document['entries']:
            if not isinstance(entry, dict):
                raise ValueError('Invalid preserved-model manifest entry')
            if (not isinstance(entry.get('uuid'), str) or not entry['uuid'] or
                    not isinstance(entry.get('original'), str) or
                    not isinstance(entry.get('index'), int) or isinstance(entry['index'], bool) or entry['index'] < 0 or
                    not isinstance(entry.get('path'), str) or not Path(entry['path']).is_absolute() or
                    not isinstance(entry.get('sha256'), str) or not re.fullmatch(r'[0-9a-fA-F]{64}', entry['sha256'])):
                raise ValueError('Invalid preserved-model identity, absolute path or SHA-256')
            key = (entry['uuid'], entry['index'], entry['original'])
            if key in result:
                raise ValueError('Duplicate preserved-model identity: ' + repr(key))
            result[key] = entry
        return result

    def _load_variables(self):
        # Read only user-defined paths. Do not change KiCad's global settings.
        appdata = os.environ.get('APPDATA')
        if appdata:
            root = Path(appdata) / 'kicad'
            native = sys.modules.get('pcbnew')
            major = re.match(r'\d+', native.GetBuildVersion()) if native else None
            if root.is_dir() and major:
                for config in [root / (major[0] + '.0') / 'kicad_common.json']:
                    try:
                        entries = json.loads(config.read_text(encoding='utf-8')).get('environment', {}).get('vars', {})
                        if isinstance(entries, dict):
                            for key, value in entries.items():
                                if isinstance(value, str):
                                    self.variables.setdefault(key, value)
                    except (OSError, ValueError):
                        pass
        standard = self.kicad_bin.parent / 'share' / 'kicad' / '3dmodels'
        if standard.is_dir():
            for version in range(6, 20):
                self.variables.setdefault('KICAD' + str(version) + '_3DMODEL_DIR', str(standard))
            self.variables.setdefault('KISYS3DMOD', str(standard))
        project_file = self.project_root / (self.pcb.stem + '.kicad_pro')
        if project_file.is_file():
            try:
                entries = json.loads(project_file.read_text(encoding='utf-8')).get('text_variables', {})
                self.variables.update({key: value for key, value in entries.items() if isinstance(value, str)})
            except (OSError, ValueError, AttributeError):
                pass
        self.variables['KIPRJMOD'] = str(self.project_root)

    @staticmethod
    def _embedded(node):
        container = node.child('embedded_files')
        result = {}
        if container:
            for item in container.children('file'):
                name = item.child('name')
                if name:
                    if name.value() in result:
                        raise ValueError('Duplicate embedded asset name: ' + name.value())
                    result[name.value()] = item
        return result

    def _decompress(self, packed):
        if self._zstd is None:
            candidates = [self.kicad_bin / filename for filename in ('zstd.dll', 'libzstd.dll', 'libzstd.so', 'libzstd.dylib')]
            library = next((str(path) for path in candidates if path.is_file()), None)
            library = library or ctypes.util.find_library('zstd')
            if not library:
                raise RuntimeError('KiCad Zstandard library missing beside the selected KiCad executable')
            self._zstd = ctypes.CDLL(library)
            self._zstd.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            self._zstd.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
            self._zstd.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            self._zstd.ZSTD_decompress.restype = ctypes.c_size_t
            self._zstd.ZSTD_isError.argtypes = [ctypes.c_size_t]
            self._zstd.ZSTD_isError.restype = ctypes.c_uint
        size = self._zstd.ZSTD_getFrameContentSize(packed, len(packed))
        if size > MAX_MODEL_BYTES:
            raise ValueError('Embedded model has unknown or excessive uncompressed size')
        target = ctypes.create_string_buffer(max(1, size))
        actual = self._zstd.ZSTD_decompress(target, size, packed, len(packed))
        if self._zstd.ZSTD_isError(actual) or actual != size:
            raise ValueError('Embedded model Zstandard decompression failed')
        return target.raw[:actual]

    def _embedded_bytes(self, uuid, name):
        local = self.footprint_embedded.get(uuid, {}).get(name)
        shared = self.board_embedded.get(name)
        node = local if local is not None and local.child('data') is not None else shared
        if node is None or node.child('data') is None:
            return None, 'Embedded model payload is absent from footprint and PCB'
        encoded = ''.join(item.value for item in node.child('data').items[1:] if isinstance(item, Atom))
        if not encoded.startswith('|') or not encoded.endswith('|'):
            raise ValueError('Invalid embedded model data delimiters: ' + name)
        data = self._decompress(base64.b64decode(encoded[1:-1], validate=True))
        checksum = node.child('checksum')
        if checksum is None:
            raise ValueError('Embedded model has no checksum: ' + name)
        expected = checksum.value().upper()
        valid = (sha256(data).hexdigest().upper() == expected if len(expected) == 64 else
                 embedded_checksum(data) == expected or embedded_checksum(data, legacy=True) == expected)
        if not valid:
            raise ValueError('Embedded model checksum mismatch: ' + name)
        if local is not None and local.child('checksum') is not None and local.child('checksum').value().upper() != expected:
            raise ValueError('Footprint and PCB embedded model checksum disagree: ' + name)
        return data, None

    def _external(self, reference):
        expanded = reference
        pattern = re.compile(r'\$\{([^}]+)\}|\$\(([^)]+)\)|%([^%]+)%')
        for _ in range(10):
            updated = pattern.sub(lambda match: self.variables.get(next(x for x in match.groups() if x is not None), match[0]), expanded)
            if updated == expanded:
                break
            expanded = updated
        if pattern.search(expanded):
            return None, 'Unresolved KiCad/environment variable', []
        if '://' in expanded:
            return None, 'Unsupported model URI scheme', []
        path = Path(expanded.replace('\\', '/')).expanduser()
        candidates = [path] if path.is_absolute() else [self.project_root / path, self.pcb.parent / path]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve(), None, [str(x) for x in candidates]
        return None, 'Referenced model file is missing', [str(x) for x in candidates]

    @staticmethod
    def _filename(name):
        name = Path(name.replace('\\', '/')).name
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'model'
        if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
            name = 'model_' + name
        path = Path(name)
        return path.stem[:140] + path.suffix[:12]

    def _publish(self, data, name, source_kind, source):
        digest = sha256(data).hexdigest()
        if digest in self._by_hash:
            return self._by_hash[digest]
        filename = self._filename(name)
        occupied = {item.name.casefold() for item in self.folder.iterdir()}
        destination = self.folder / filename
        if filename.casefold() in occupied and (not destination.is_file() or sha256(destination.read_bytes()).hexdigest() != digest):
            base = Path(filename)
            destination = self.folder / (base.stem + '_' + digest[:16] + base.suffix)
        if destination.exists() and destination.read_bytes() != data:
            raise ValueError('Model destination collision: ' + str(destination))
        destination.write_bytes(data)
        if sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Model changed during copy: ' + str(destination))
        relative = '01_Lib/' + self.folder.name + '/' + destination.name
        asset = {'path': relative, 'sha256': digest, 'size_bytes': len(data), 'source_kind': source_kind, 'source': source}
        self.assets.append(asset)
        self._by_hash[digest] = asset
        return asset

    def collect(self, uuid, index, reference):
        # A preserved model override belongs to one exact instance and index.
        # Sharing cache entries by external path could substitute another
        # instance's asset or accidentally skip its checksum verification.
        cache_key = (uuid, index, reference)
        if cache_key in self._cache:
            return dict(self._cache[cache_key], index=index)
        row = {'index': index, 'original': reference, 'target': reference, 'status': 'missing'}
        if reference.startswith('kicad-embed://'):
            name = reference[len('kicad-embed://'):]
            data, reason = self._embedded_bytes(uuid, name)
            source = str(self.pcb) + '#kicad-embed://' + name
            kind = 'embedded'
        elif cache_key in self._preserved:
            preserved = self._preserved[cache_key]
            path = Path(preserved['path'])
            if not path.is_file():
                raise ValueError('Preserved model is missing: ' + str(path))
            data = path.read_bytes()
            if sha256(data).hexdigest() != preserved['sha256'].lower():
                raise ValueError('Preserved model checksum mismatch: ' + str(path))
            name, source, kind = path.name, str(path), 'preserved'
        else:
            path, reason, candidates = self._external(reference)
            data = path.read_bytes() if path else None
            name = path.name if path else reference
            source = str(path) if path else reference
            kind = 'file'
            if candidates:
                row['searched_paths'] = candidates
        row['source'] = source
        if data is None:
            row['reason'] = reason
        else:
            asset = self._publish(data, name, kind, source)
            row.update(status='collected', target='${KIPRJMOD}/' + asset['path'], sha256=asset['sha256'], asset_path=asset['path'])
        self._cache[cache_key] = row
        return dict(row)

    def verify(self):
        expected = {Path(item['path']).name for item in self.assets}
        unexpected = {item.name for item in self.folder.iterdir()} - expected
        if unexpected:
            raise ValueError('Destination contains stale/unexpected model entries: ' + repr(sorted(unexpected)))
        for asset in self.assets:
            if sha256((self.folder / Path(asset['path']).name).read_bytes()).hexdigest() != asset['sha256']:
                raise RuntimeError('Model asset checksum changed: ' + asset['path'])
