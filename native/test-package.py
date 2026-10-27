import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import installer

spec = importlib.util.spec_from_file_location('package_candidate', Path(__file__).with_name('package-candidate.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class Package(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'module').mkdir()
        (self.source / 'module/build-id.h').write_text('#define C5_SESSION_BUILD "' + 'a'*64 + '"\n')
        for name, source in package.SOURCES.items():
            data = bytearray(64)
            if name.endswith(('.so', '.ko')):
                data[:6] = b'\x7fELF' + bytes([1 if name.endswith('.so') else 2, 1])
                data[18:20] = (40 if name.endswith('.so') else 183).to_bytes(2, 'little')
            else:
                data = b'# own source\n'
            (self.source / source).write_bytes(data)
        self.audit()
        self.output = self.root / 'bundle'

    def tearDown(self):
        self.tmp.cleanup()

    def audit(self):
        (self.source / 'binary-audit.json').write_text(json.dumps({
            'status': 'PASS', 'module_sha256': installer.sha(self.source / 'module/c5_es_poc.ko')}))

    def build(self, evidence=None):
        with patch.object(package, 'strip_debug', side_effect=lambda p, tool: None):
            return package.build(self.source, self.output, 'unused',evidence)

    def evidence(self, change=None):
        success={'plugin_sha256':installer.sha(self.source/'libgstdtspassthrough.so'),
                 'module_sha256':installer.sha(self.source/'module/c5_es_poc.ko'),
                 'audio_device_opened':False,'kernel_audio_hooks':False,
                 'model_latency_seconds':0,'untimed_preparation':False,'mock_errors':[],
                 'expected_timeout':False,'pipeline_error':False,'ready_delay_seconds':1.4,
                 'fragment_size':337,'byte_identity_preserved':True,'original_pts_unchanged':True,
                 'timestamps_valid':True,'timestamps_monotonic':True,'captured_original_bytes':123,
                 'required_original_bytes':123,'reference_clock_delta_seconds':.001}
        timeout=dict(success,expected_timeout=True,pipeline_error=True,captured_original_bytes=0,ready_delay_seconds=1.7)
        data={'schema':1,'runs':[success,timeout]}
        if change:change(data)
        path=self.root/'evidence.json';path.write_text(json.dumps(data));return path

    def test_exact_target_evidence_allows_only_hardware_trial(self):
        manifest=self.build(self.evidence())
        self.assertEqual(manifest['blocking_review_findings'],[])
        self.assertFalse(manifest['hardware_validated']);self.assertFalse(manifest['boot_enabled'])
        with patch.object(installer,'require_target'):
            with self.assertRaises(RuntimeError):installer.activate(self.output,self.root/'run')

    def test_evidence_for_different_plugin_rejected(self):
        evidence=self.evidence(lambda d:d['runs'][0].update(plugin_sha256='0'*64))
        with self.assertRaises(ValueError):self.build(evidence)
        self.assertFalse(self.output.exists())

    def test_timeout_releasing_original_is_rejected(self):
        evidence=self.evidence(lambda d:d['runs'][1].update(captured_original_bytes=1))
        with self.assertRaises(ValueError):self.build(evidence)

    def test_manual_latency_override_is_not_automatic_proof(self):
        evidence=self.evidence(lambda d:d['runs'][0].update(model_latency_seconds=2))
        with self.assertRaises(ValueError):self.build(evidence)

    def test_missing_fragmentation_case_rejected(self):
        evidence=self.evidence(lambda d:d['runs'][0].update(fragment_size=0))
        with self.assertRaises(ValueError):self.build(evidence)

    def test_exact_payload_and_blocked_activation(self):
        self.build()
        manifest = installer.verify_bundle(self.output)
        self.assertEqual({p.relative_to(self.output).as_posix() for p in self.output.rglob('*') if p.is_file()}, installer.FILES | {'manifest.json'})
        self.assertFalse(manifest['hardware_validated'])
        self.assertTrue(manifest['blocking_review_findings'])
        dry_run = installer.plan(self.output, Path('/home/root/c5-dts-passthrough'))
        self.assertTrue(dry_run['activation_blocked'])
        self.assertEqual(dry_run['blocking_review_findings'], manifest['blocking_review_findings'])
        with patch.object(installer, 'require_target'):
            with self.assertRaises(RuntimeError):
                installer.activate(self.output, self.root/'run', True)
        self.assertFalse((self.root/'run').exists())

    def test_stale_binary_audit_rejected(self):
        (self.source/'module/c5_es_poc.ko').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.build()
        self.assertFalse(self.output.exists())

    def test_private_path_rejected_without_partial_bundle(self):
        (self.source/'supervisor.py').write_bytes(b'# /Users' + b'/private/file\n')
        with self.assertRaises(ValueError): self.build()
        self.assertFalse(self.output.exists())

    def test_symlink_input_rejected(self):
        path = self.source/'supervisor.py'
        path.unlink();path.symlink_to(self.source/'installer.py')
        with self.assertRaises(ValueError): self.build()
        self.assertFalse(self.output.exists())

    def test_existing_output_preserved(self):
        self.output.mkdir();(self.output/'keep').write_text('keep')
        with self.assertRaises(FileExistsError): self.build()
        self.assertEqual((self.output/'keep').read_text(), 'keep')

    def test_strip_failure_cleans_temporary_output(self):
        with patch.object(package, 'strip_debug', side_effect=RuntimeError('strip failed')):
            with self.assertRaises(RuntimeError): package.build(self.source,self.output,'unused')
        self.assertFalse(self.output.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['source'])


if __name__ == '__main__': unittest.main()
