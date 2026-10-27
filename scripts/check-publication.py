"""Check the public Git index without opening ignored investigation material."""
import ast
import re
import subprocess

ALLOWED = {'.github/workflows/checks.yml',
 '.gitignore',
 'LICENSES/GPL-2.0-only.txt',
 'LICENSES/LGPL-2.1-or-later.txt',
 'NOTICE',
 'README.md',
 'build-plugin.sh',
 'native/audit-module.py',
 'native/build-module.sh',
 'native/checks.py',
 'native/configuration.py',
 'native/installer.py',
 'native/module/Makefile',
 'native/module/break_bridge.h',
 'native/module/build-id.h',
 'native/module/c5_es_poc.c',
 'native/module/lease-policy.h',
 'native/module/lease-state.h',
 'native/module/target.h',
 'native/package-candidate.py',
 'native/storage-preflight.py',
 'native/supervisor.py',
 'native/test-clock-target.py',
 'native/test-configuration.py',
 'native/test-installer.py',
 'native/test-lease-policy.c',
 'native/test-lease-state.c',
 'native/test-package.py',
 'native/test-ready-protocol.c',
 'native/test-release-policy.c',
 'native/test-supervisor.py',
 'scripts/check-publication.py',
 'src/gstdtspassthrough.c',
 'src/native-session.h',
 'src/ready-protocol.h',
 'src/release-policy.h',
 'test-runtime.py',
 'tests/verify-iec.test.mjs',
 'verify-iec.mjs'}
tracked = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
tracked = set(filter(None, tracked))
assert tracked == ALLOWED, f'Unexpected or missing public paths: {tracked ^ ALLOWED}'
patterns = [
    rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
    rb'gh[pousr]_[A-Za-z0-9]{20,}',
    rb'github_pat_[A-Za-z0-9_]{20,}',
    rb'AKIA[A-Z0-9]{16}',
    rb'/Users' rb'/[^/\s]+/',
    rb'\b192\.168\.\d{1,3}\.\d{1,3}\b',
    rb'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
    rb'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b',
]
for name in sorted(tracked):
    mode = subprocess.check_output(['git', 'ls-files', '-s', '--', name]).split()[0]
    assert mode in (b'100644', b'100755'), f'Not a regular file: {name}'
    # Inspect the exact staged bytes that would be committed.
    data = subprocess.check_output(['git', 'show', ':' + name])
    assert len(data) < 100_000 and b'\0' not in data, f'Binary or oversized: {name}'
    data.decode('utf-8')
    for pattern in patterns:
        assert not re.search(pattern, data), f'Potential private content: {name}'
    if name.endswith('.py'):
        ast.parse(data, filename=name)

private_examples = [
    'HANDOFF.md', 'PROMPT.md', 'MANIFEST.json', '.env', 'credentials.json',
    'Image', 'libgstdtspassthrough.so', 'session.log', 'fixtures/sample.dts',
    'sysroot/usr/lib/private.so', 'toolchain/zig', 'http/movie.mkv',
    'src/private.h', 'tests/private.json', 'scripts/access-helper',
    '.github/workflows/unreviewed.yml',
]
for name in private_examples:
    result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', name])
    assert result.returncode == 0, f'Private path not ignored: {name}'
print(f'Publication checks passed: {len(tracked)} reviewed text files; private paths ignored.')
