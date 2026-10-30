"""Exercise real installers on temporary files; only hardware/probing is replaced."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manager = load('tv_manager', ROOT / 'tv-package.py')
fixture = load('global_fixture', ROOT / 'native/hdmi/test-global-install.py')
release = load('release_builder', ROOT / 'scripts/build-tv-release.py')
import installer as native


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = fixture.bundle(self.root / 'bundle')
        self.backend = fixture.m
        self.prefix = self.root / 'installed'
        hooks = self.root / 'init.d'; hooks.mkdir()
        self.hook = hooks / 'zz-c5-dts-global'
        self.registry = self.root / 'registry.bin'; self.registry.write_bytes(b'original registry')
        self.unrelated = hooks / 'dts_restore'; self.unrelated.write_text('keep restore hook')
        self.patches = [patch.object(self.backend.subprocess, 'Popen', side_effect=AssertionError('unexpected external/device command')),
                        patch.object(self.backend, 'require_target'),
                        patch.object(self.backend, 'cold_registry', side_effect=self.cold)]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

    def cold(self, bundle, prefix, restore=False):
        # Real backup, journal, checks and restoration. No mounts or device I/O.
        if restore:
            native.restore_cold_registry(self.registry, prefix / 'cold-registry')
            return {}
        def inspect(*args, **kwargs):
            Path(kwargs['env']['GST_REGISTRY_1_0']).write_bytes(b'seeded registry')
            return str(bundle / 'native/plugins/libgstdtspassthrough.so') + '\nRank (310)'
        with patch.object(native, 'command', side_effect=inspect):
            return native.seed_cold_registry(bundle / 'native', self.registry, prefix / 'cold-registry')

    def run_action(self, action='install', check=False):
        return manager.operate(action, check, self.backend, self.bundle, self.prefix, self.hook)

    def assert_inactive(self):
        self.assertFalse(self.hook.exists())
        self.assertFalse((self.prefix / 'current').is_symlink())
        self.assertEqual(self.registry.read_bytes(), b'original registry')
        self.assertEqual(self.unrelated.read_text(), 'keep restore hook')

    def test_install_repeat_remove_repeat_restores_original_and_preserves_versions(self):
        self.assertIn('concluída', self.run_action())
        self.assertEqual(self.registry.read_bytes(), b'seeded registry')
        versions = list((self.prefix / 'versions').iterdir())
        self.run_action()
        self.assertEqual(list((self.prefix / 'versions').iterdir()), versions)
        self.assertIn('desativada', self.run_action('remove'))
        self.assert_inactive()
        self.assertTrue(versions[0].is_dir())
        self.assertIn('já está desativada', self.run_action('remove'))
        self.assert_inactive()
        self.assertIn('concluída', self.run_action())

    def test_checks_do_not_create_installation_or_remove_existing_one(self):
        self.run_action(check=True)
        self.assertFalse(self.prefix.exists()); self.assert_inactive()
        self.run_action()
        before = self.hook.read_bytes()
        self.run_action('remove', check=True)
        self.assertEqual(self.hook.read_bytes(), before)
        self.assertEqual(self.registry.read_bytes(), b'seeded registry')

    def test_wrong_target_has_no_mutations(self):
        for action in ('install', 'remove'):
            with self.subTest(action=action), patch.object(self.backend, 'require_target', side_effect=RuntimeError('wrong kernel')):
                with self.assertRaisesRegex(RuntimeError, 'wrong kernel'):
                    self.run_action(action)
                self.assertFalse(self.prefix.exists()); self.assert_inactive()

    def test_modified_bundle_refused_before_any_installation(self):
        (self.bundle / 'native/supervisor.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'artifact changed'):
            self.run_action()
        self.assertFalse(self.prefix.exists()); self.assert_inactive()

    def test_existing_different_installation_is_preserved(self):
        self.run_action()
        original = (self.prefix / 'current').resolve()
        (self.bundle / 'README.md').write_text('different release'); fixture.seal(self.bundle)
        with self.assertRaisesRegex(RuntimeError, 'different existing installation'):
            self.run_action()
        self.assertEqual((self.prefix / 'current').resolve(), original)
        self.assertTrue(self.hook.is_file())
        self.assertEqual(self.registry.read_bytes(), b'seeded registry')

    def test_legacy_native_hook_is_not_migrated_implicitly(self):
        legacy = self.hook.with_name('zz-c5-dts-passthrough'); legacy.write_text('keep legacy')
        with self.assertRaisesRegex(RuntimeError, 'antiga'):
            self.run_action()
        self.assertEqual(legacy.read_text(), 'keep legacy'); self.assert_inactive()

    def test_failed_or_cancelled_preparation_rolls_back_new_installation(self):
        for error in (OSError('probe failed'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def fail(bundle, prefix, restore=False):
                    if restore:
                        return self.cold(bundle, prefix, True)
                    self.cold(bundle, prefix)
                    raise error
                with patch.object(self.backend, 'cold_registry', side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, 'boot novo foi desfeito'):
                        self.run_action()
                self.assert_inactive()

    def test_empty_unjournaled_directory_is_recoverable(self):
        def fail(bundle, prefix, restore=False):
            (prefix / 'cold-registry').mkdir(mode=0o700)
            raise OSError('inspection failed')
        with patch.object(self.backend, 'cold_registry', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'boot novo foi desfeito'):
                self.run_action()
        self.assert_inactive()

    def test_unjournaled_backup_is_preserved_and_error_is_actionable(self):
        def fail(bundle, prefix, restore=False):
            state = prefix / 'cold-registry'; state.mkdir(mode=0o700)
            (state / 'original.bin').write_bytes(b'evidence')
            raise OSError('disk failure')
        with patch.object(self.backend, 'cold_registry', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Não reinicie ainda'):
                self.run_action()
        self.assertEqual((self.prefix / 'cold-registry/original.bin').read_bytes(), b'evidence')
        self.assertTrue(self.hook.exists())

    def test_failed_repeat_never_disables_existing_working_installation(self):
        self.run_action()
        with patch.object(self.backend, 'cold_registry', side_effect=OSError('uncertain')):
            with self.assertRaises(OSError):
                self.run_action()
        self.assertTrue(self.hook.exists())
        self.assertEqual(self.registry.read_bytes(), b'seeded registry')

    def test_changed_hook_or_registry_blocks_removal_without_overwrite(self):
        self.run_action()
        original = self.hook.read_bytes(); self.hook.write_text('foreign hook')
        with self.assertRaisesRegex(RuntimeError, 'hook identity'):
            self.run_action('remove')
        self.assertEqual(self.hook.read_text(), 'foreign hook')
        self.hook.write_bytes(original); self.registry.write_bytes(b'foreign registry')
        with self.assertRaises(RuntimeError):
            self.run_action('remove')
        self.assertEqual(self.registry.read_bytes(), b'foreign registry')
        self.assertTrue(self.hook.exists())
        self.assertTrue((self.prefix / 'cold-registry/original.bin').exists())

    def test_concurrent_operation_refused_without_changing_registry(self):
        with manager.management_lock(self.backend, self.prefix):
            with self.assertRaises(BlockingIOError):
                self.run_action()
        self.assert_inactive()

    def test_foreign_lock_symlink_is_never_followed(self):
        self.prefix.mkdir(mode=0o700)
        (self.prefix / 'management.lock').symlink_to(self.registry)
        with self.assertRaises(OSError):
            self.run_action()
        self.assert_inactive()


class ReleaseAndShell(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='c5 shell test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = fixture.bundle(self.root / 'input')
        # Real public Python programs, synthetic ELF fixtures only.
        for name in fixture.m.PAYLOAD:
            if name.endswith('.py'):
                (self.bundle / name).write_bytes(release.source_for(name).read_bytes())
        for component, filename in [('native', 'manifest.json'), ('hdmi', 'hdmi-manifest.json'), ('hdmi_hd', 'hdmi-manifest.json')]:
            path = self.bundle / component / filename
            manifest = json.loads(path.read_text())
            manifest['files'] = {name: fixture.m.sha(path.parent / name) for name in manifest['files']}
            path.write_text(json.dumps(manifest))
        fixture.seal(self.bundle)
        self.pins = {name: fixture.m.sha(self.bundle / name) for name in release.BINARY_SHA256}
        self.archive = self.root / 'release.tar.gz'

    def build(self):
        with patch.object(release, 'BINARY_SHA256', self.pins):
            return release.build(self.bundle, self.archive, 'v0.4.0')

    def extract(self):
        self.build()
        with tarfile.open(self.archive) as archive:
            archive.extractall(self.root, filter='data')
        return self.root / 'lg-c5-dts-passthrough-v0.4.0'

    def shell(self, root, script, *args):
        return subprocess.run(['sh', str(root / script), *args], cwd=self.root,
                              capture_output=True, text=True, timeout=10)

    def test_packaged_shells_work_from_another_directory_with_spaces(self):
        output = self.extract()
        result = self.shell(output, 'install.sh', '--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Nada foi instalado', result.stdout)
        for script in ('install.sh', 'remove.sh'):
            help_result = self.shell(output, script, '--help')
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            result = self.shell(output, script, '--invalid')
            self.assertNotEqual(result.returncode, 0)
            # Host has no supported C5 kernel, even when Linux CI runs as root.
            result = self.shell(output, script)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('exact C5 kernel', result.stderr)
        self.assertFalse(list(output.rglob('__pycache__')))

    def test_source_download_explains_missing_binary_bundle(self):
        for script in ('install.sh', 'remove.sh'):
            result = self.shell(ROOT, script, '--check')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('-tv.tar.gz', result.stderr)

    def test_tampered_package_fails_offline_shell_check(self):
        output = self.extract()
        (output / 'bundle/native/c5_es_poc.ko').write_bytes(b'changed')
        result = self.shell(output, 'install.sh', '--check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('artifact changed', result.stderr)

    def test_archive_contains_only_reviewed_files_and_exact_binaries(self):
        (self.bundle / 'private.log').write_text('must not ship')
        self.build()
        with tarfile.open(self.archive) as archive:
            expected = {'lg-c5-dts-passthrough-v0.4.0/' + name for name in release.OUTER}
            expected.update('lg-c5-dts-passthrough-v0.4.0/bundle/' + name for name in fixture.m.PAYLOAD | {'global-manifest.json'})
            self.assertEqual(set(archive.getnames()), expected)
            for name in release.BINARY_SHA256:
                data = archive.extractfile('lg-c5-dts-passthrough-v0.4.0/bundle/' + name).read()
                self.assertEqual(data, (self.bundle / name).read_bytes())
            self.assertTrue(all(entry.isfile() for entry in archive.getmembers()))

    def test_changed_binary_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'accepted binary'):
            release.build(self.bundle, self.archive, 'v0.4.0')
        self.assertFalse(self.archive.exists())

    def test_existing_output_preserved(self):
        self.archive.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(self.archive.read_bytes(), b'existing')

    def test_interrupted_archive_never_publishes_partial_output(self):
        for error in (OSError('disk full'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch.object(tarfile.TarFile, 'addfile', side_effect=error):
                with self.assertRaises(type(error)):
                    self.build()
                self.assertFalse(self.archive.exists())

    def test_failure_never_deletes_replaced_output(self):
        def replace(*args, **kwargs):
            self.archive.rename(self.root / 'owned-partial')
            self.archive.write_bytes(b'foreign replacement')
            raise OSError('interrupted')
        with patch.object(tarfile.TarFile, 'addfile', side_effect=replace):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual(self.archive.read_bytes(), b'foreign replacement')

    def test_private_content_and_symlink_manifest_refused(self):
        (self.bundle / 'NOTICE').write_text('-----BEGIN ' + 'OPENSSH PRIVATE KEY-----')
        fixture.seal(self.bundle)
        with self.assertRaisesRegex(ValueError, 'private content'):
            self.build()
        self.assertFalse(self.archive.exists())
        (self.bundle / 'NOTICE').write_text('safe'); fixture.seal(self.bundle)
        path = self.bundle / 'global-manifest.json'; saved = self.root / 'manifest'
        path.rename(saved); path.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build()
        self.assertFalse(self.archive.exists())


if __name__ == '__main__':
    unittest.main()
