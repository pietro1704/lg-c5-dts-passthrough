# SPDX-License-Identifier: GPL-2.0-only
"""Package reviewed scripts and exact accepted owned binaries; no device access."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUTER = ('install.sh', 'remove.sh', 'tv-package.py', 'INSTALL.md')
BINARY_SHA256 = {
    'native/plugins/libgstdtspassthrough.so': '2de753cfd465cf20a82719228194cf3f90bf5907fa98c5df1b5b216169f708a4',
    'native/c5_es_poc.ko': '0c33db06e37a824fcd2b5317c06bf97d5548989212aa3154f82e3481c34bacfc',
    'hdmi/auto-route.ko': '0b27120c8b8a91273351f7880be04363de5cf6d21f76c3967beb7d3ad160dfd3',
    'hdmi/broker-gate.ko': '81e2e8f4f5f7ddecf671efd534548fc593ad219415c500612ac767cf893fcc9a',
    'hdmi_hd/auto-route.ko': 'f93ab4e48d71519c3c6ba82b645c4494e7bd5226339c82741048b5b0939b0cd0',
    'hdmi_hd/broker-gate.ko': 'c4496174d7733b9f1bf7819b9688ffd2974340f70a5df337acb4e2aff69301fc',
}
# Firmware/library/sysroot/media inputs never enter this mapping.
PRIVATE = [rb'/Users' rb'/[^/\s]+/', rb'/private/var/folders/',
           rb'\b192\.168\.\d{1,3}\.\d{1,3}\b',
           rb'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
           rb'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b',
           rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
           rb'gh[pousr]_[A-Za-z0-9]{20,}', rb'github_pat_[A-Za-z0-9_]{20,}']


def backend():
    spec = importlib.util.spec_from_file_location('release_global_install', ROOT / 'native/hdmi/global_install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_for(name):
    if name == 'global_install.py':
        return ROOT / 'native/hdmi/global_install.py'
    if name.startswith('hdmi_hd/'):
        directory = 'native/hdmi' if name.endswith('/hdmi_hd_supervisor.py') else 'experiments/hdmi-hd'
        return ROOT / directory / Path(name).name
    return ROOT / ('native/' + name if name.startswith('hdmi/') else name)


def regular_bytes(root, name):
    path = root
    if root.is_symlink():
        raise ValueError('symlink package root')
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink package component: ' + name)
    if not path.is_file():
        raise ValueError('nonregular package component: ' + name)
    return path.read_bytes()


def build(bundle, output, version):
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('invalid release version')
    module = backend()
    module.verify(bundle)
    files = {name: regular_bytes(ROOT, name) for name in OUTER}
    for name in sorted(module.PAYLOAD | {'global-manifest.json'}):
        data = regular_bytes(bundle, name)
        if name in BINARY_SHA256 and hashlib.sha256(data).hexdigest() != BINARY_SHA256[name]:
            raise ValueError('not the accepted binary: ' + name)
        if name.endswith('.py') and data != source_for(name).read_bytes():
            raise ValueError('stale runtime source: ' + name)
        files['bundle/' + name] = data
    # Replace the historical candidate README, preserving every runtime byte.
    files['bundle/README.md'] = files['INSTALL.md']
    manifest = json.loads(files['bundle/global-manifest.json'])
    manifest['files']['README.md'] = hashlib.sha256(files['INSTALL.md']).hexdigest()
    files['bundle/global-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    for name, data in files.items():
        if any(re.search(pattern, data) for pattern in PRIVATE):
            raise ValueError('private content in ' + name)
    # Exclusive create, and remove only our partial output on failure/cancel.
    with output.open('xb') as stream:
        try:
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                for name, data in sorted(files.items()):
                    entry = tarfile.TarInfo('lg-c5-dts-passthrough-' + version + '/' + name)
                    entry.size = len(data)
                    entry.mode = 0o700 if name.endswith('.sh') else 0o600
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(data))
        except BaseException:
            owned = os.fstat(stream.fileno())
            if output.exists() or output.is_symlink():
                current = output.lstat()
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    output.unlink()
            raise
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    print(build(args.bundle, args.output, args.version) + '  ' + args.output.name)


if __name__ == '__main__':
    main()
