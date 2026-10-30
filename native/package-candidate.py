# SPDX-License-Identifier: GPL-2.0-only
"""Build a private, activation-blocked candidate from explicitly owned artifacts.

No network or device actions. No firmware, sysroot, libraries or media copied.
The output is a directory suitable for installer.py dry-run, never a release.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from installer import FILES, KERNEL, sha, verify_bundle

SOURCES = {
    'plugins/libgstdtspassthrough.so': 'libgstdtspassthrough.so',
    'c5_es_poc.ko': 'module/c5_es_poc.ko',
    'supervisor.py': 'supervisor.py',
    'configuration.py': 'configuration.py',
    'installer.py': 'installer.py',
}
BLOCKERS = ['P2: target proof of preparation before preroll without added latency is missing']
KNOWN_GOOD_SUPERVISOR='6077f3cf1b4cd815513677947efa850cee84b464f3baedbdc5a620066a04e0fe'


def strip_debug(path, tool):
    subprocess.run([tool, '--strip-debug', str(path)], check=True, timeout=30)


def regular_source(root, name):
    path = root
    if root.is_symlink(): raise ValueError('symlink source root')
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink(): raise ValueError('symlink source component: ' + name)
    if not path.is_file(): raise ValueError('nonregular source: ' + name)
    return path


def validate_clock_evidence(path, bundle):
    proof=json.loads(path.read_text())
    if proof.get('schema')!=1:raise ValueError('wrong clock evidence schema')
    successes=[];timeouts=[]
    for run in proof.get('runs',[]):
        if (run.get('plugin_sha256')!=sha(bundle/'plugins/libgstdtspassthrough.so') or
            run.get('module_sha256')!=sha(bundle/'c5_es_poc.ko') or
            run.get('audio_device_opened') is not False or run.get('kernel_audio_hooks') is not False or
            run.get('model_latency_seconds')!=0 or run.get('untimed_preparation') is not False or run.get('mock_errors') or
            run.get('reported_latency_ns')!=0 or run.get('buffers_before_ready')!=0 or
            run.get('lease_backend') not in ('kernel-api-only','userspace-isolated')):
            raise ValueError('clock evidence does not identify this isolated automatic candidate')
        if run.get('expected_timeout') is True:
            if run.get('pipeline_error') is not True or run.get('captured_original_bytes')!=0 or run.get('ready_delay_seconds',0)<1.5:
                raise ValueError('timeout did not retain every original')
            timeouts.append(run)
        else:
            if (run.get('pipeline_error') is not False or run.get('byte_identity_preserved') is not True or
                run.get('original_pts_unchanged') is not True or run.get('timestamps_valid') is not True or
                run.get('timestamps_monotonic') is not True or
                run.get('captured_original_bytes',0)<=0 or run.get('captured_original_bytes')!=run.get('required_original_bytes') or
                not isinstance(run.get('reference_clock_delta_seconds'),(int,float)) or
                not 0<=run['reference_clock_delta_seconds']<=.05):
                raise ValueError('clock evidence did not preserve original bytes/timeline/reference')
            successes.append(run)
    if not timeouts or not any(r.get('ready_delay_seconds',0)>=1.4 for r in successes) or not any(r.get('fragment_size',0)>0 for r in successes):
        raise ValueError('clock evidence lacks delay/fragmentation/timeout cases')


def build(source, output, objcopy, clock_evidence=None, known_good_supervisor=False):
    if output.exists() or output.is_symlink(): raise FileExistsError('output already exists')
    if set(SOURCES) != FILES: raise ValueError('installer/package allowlists differ')
    audit = json.loads(regular_source(source, 'binary-audit.json').read_text())
    if audit.get('status') != 'PASS' or audit.get('module_sha256') != sha(regular_source(source, SOURCES['c5_es_poc.ko'])):
        raise ValueError('missing/stale successful module audit')
    match = re.fullmatch(r'#define C5_SESSION_BUILD "([a-f0-9]{64})"\s*', regular_source(source, 'module/build-id.h').read_text())
    if not match: raise ValueError('invalid module build identity')
    # Build in the output filesystem. Only publish the directory after all checks.
    temporary = Path(tempfile.mkdtemp(prefix='.c5-package-', dir=output.parent))
    try:
        inputs = {}
        for name, original in sorted(SOURCES.items()):
            path = regular_source(source, original)
            if known_good_supervisor and name=='supervisor.py':
                path=regular_source(Path(__file__).resolve().parent,'known-good-supervisor.py')
                if sha(path)!=KNOWN_GOOD_SUPERVISOR:raise ValueError('accepted supervisor snapshot changed')
            inputs[name] = sha(path)
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            destination.chmod(0o600)
            if name.endswith(('.ko', '.so')): strip_debug(destination, objcopy)
            data = destination.read_bytes()
            if any(value in data for value in (b'/Users/', b'/private/var/folders/', b'192.168.0.')):
                raise ValueError('private path/address in artifact: ' + name)
        if clock_evidence is not None:validate_clock_evidence(clock_evidence,temporary)
        manifest = {
            'schema': 1, 'kernel': KERNEL, 'module_build': match[1],
            'hardware_validated': False, 'boot_enabled': False,
            'supervisor_variant':'known-good-20261027' if known_good_supervisor else 'current',
            'blocking_review_findings': BLOCKERS if clock_evidence is None else [],
            'clock_evidence_sha256':None if clock_evidence is None else sha(clock_evidence),
            'unresolved_acceptance': ['DTS-HD MA and channel identity in original Nuvio',
                'vendor sink and actual video/audio synchronization',
                'DTS core and Dolby preservation', 'pause/seek/track/cancel/reboot stability',
                'native route confirmation before first sink buffer'],
            'input_sha256': inputs,
            'files': {name: sha(temporary/name) for name in sorted(FILES)},
        }
        (temporary/'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
        verify_bundle(temporary)
        temporary.rename(output)
        return manifest
    except BaseException:
        shutil.rmtree(temporary)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--objcopy', default='/usr/local/opt/llvm/bin/llvm-objcopy')
    parser.add_argument('--clock-evidence',type=Path,help='exact candidate target evidence; allows temporary hardware trial only')
    parser.add_argument('--known-good-supervisor',action='store_true',help='freeze the exact receiver-confirmed supervisor snapshot')
    options = parser.parse_args()
    manifest = build(options.source, options.output, options.objcopy,options.clock_evidence,options.known_good_supervisor)
    print(json.dumps({'status': 'private candidate prepared', 'hardware_validated': False,
                      'activation_blocked': bool(manifest['blocking_review_findings']), 'files': manifest['files']}, indent=2))


if __name__ == '__main__': main()
