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
 'native/build-clock-isolation.sh',
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
 'native/test-clock-isolation.c',
 'native/test-cold-registry-target.py',
 'native/test-configuration.py',
 'native/test-installer.py',
 'native/test-lease-policy.c',
 'native/test-lease-state.c',
 'native/test-package.py',
 'native/test-ready-protocol.c',
 'native/test-ready-receive.py',
 'native/test-latency-target.inc',
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
ALLOWED.update({
    'native/hdmi/alsa_controls.py',
    'native/hdmi/build-global-package.py',
    'native/hdmi/edid.py',
    'native/hdmi/global_install.py',
    'native/hdmi/hdmi_supervisor.py',
    'native/hdmi/hdmi_hd_supervisor.py',
    'native/hdmi/test-hd-supervisor.py',
    'native/hdmi/hpd.py',
    'native/hdmi/luna_rpc.py',
    'native/hdmi/module/Makefile',
    'native/hdmi/module/c5_hdmi_auto_route.c',
    'native/hdmi/module/c5_hdmi_broker_gate.c',
    'native/hdmi/module/gate-policy.h',
    'native/hdmi/module/input-policy.h',
    'native/hdmi/module/route-policy.h',
    'native/hdmi/module/route-target.h',
    'native/hdmi/module/target.h',
    'native/hdmi/runtime_settings.py',
    'native/hdmi/test-alsa-controls.py',
    'native/hdmi/test-global-install.py',
    'native/hdmi/test-hpd.py',
    'native/hdmi/test-input-policy.c',
    'native/hdmi/test-luna-rpc.py',
    'native/hdmi/test-runtime-settings.py',
    'native/hdmi/test-supervisor.py',
    'experiments/hdmi-hd/alsa_controls.py',
    'experiments/hdmi-hd/complete-hbr.py',
    'experiments/hdmi-hd/edid.py',
    'experiments/hdmi-hd/hd_controller.py',
    'experiments/hdmi-hd/luna_rpc.py',
    'experiments/hdmi-hd/module/Makefile',
    'experiments/hdmi-hd/module/c5_hdmi_ma_gate.c',
    'experiments/hdmi-hd/module/c5_hdmi_ma_route.c',
    'experiments/hdmi-hd/module/gate-policy.h',
    'experiments/hdmi-hd/module/input-policy.h',
    'experiments/hdmi-hd/module/route-policy.h',
    'experiments/hdmi-hd/module/route-target.h',
    'experiments/hdmi-hd/module/target.h',
    'experiments/hdmi-hd/run-hd.py',
    'experiments/hdmi-hd/runtime_settings.py',
    'experiments/hdmi-hd/test-hd-controller.py',
    'experiments/hdmi-hd/test-policy.c',
    'native/known-good-supervisor.py',
})
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
