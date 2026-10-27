import tempfile,unittest,json,hashlib,os,shutil,subprocess
from pathlib import Path
from unittest.mock import patch
import installer as i

def elf(kind,machine):
    value=bytearray(64);value[:4]=b'\x7fELF';value[4]=kind;value[5]=1;value[18:20]=machine.to_bytes(2,'little');return value

def bundle(root):
    root.mkdir();(root/'plugins').mkdir()
    for name in i.FILES:
        path=root/name;path.write_bytes(elf(1,40) if name.endswith('.so') else elf(2,183) if name.endswith('.ko') else b'# own source\n')
    manifest={'schema':1,'kernel':i.KERNEL,'module_build':'a'*64,'files':{name:i.sha(root/name) for name in sorted(i.FILES)},'unresolved_acceptance':['hardware pending']}
    (root/'manifest.json').write_text(json.dumps(manifest))
    return root

class Installer(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.bundle=bundle(self.root/'bundle')
    def tearDown(self):self.temp.cleanup()
    def test_boot_command_finds_sbin_tool_with_restricted_inherited_path(self):
        tools=self.root/'sbin';tools.mkdir();tool=tools/'insmod'
        tool.write_text('#!/bin/sh\nprintf "%s" "$C5_EXEC_TEST"\n');tool.chmod(0o700)
        with patch.object(i,'COMMAND_PATH',str(tools),create=True),patch.dict(os.environ,{'PATH':'/missing','C5_EXEC_TEST':'inherited'}):
            self.assertEqual(i.command('insmod'),'inherited')
            self.assertEqual(i.command('insmod',env={'PATH':'/missing','C5_EXEC_TEST':'isolated','GST_REGISTRY_1_0':'/isolated'}),'isolated')
    def test_actual_spawned_supervisor_inherits_system_path(self):
        script=self.bundle/'supervisor.py'
        script.write_text('import os\nprint(os.environ["PATH"])\n')
        manifest=json.loads((self.bundle/'manifest.json').read_text())
        with patch.dict(os.environ,{'PATH':'/missing'}):
            process=i.spawn_supervisor(self.bundle,manifest,self.root/'run',subprocess.PIPE)
            output,_=process.communicate(timeout=5)
        self.assertEqual(process.returncode,0)
        self.assertEqual(output.decode().strip(),i.COMMAND_PATH)
    def test_offline_dryrun_has_no_mutation_or_device_commands(self):
        before=sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*'))
        with patch.object(i,'command',side_effect=AssertionError('device command')):
            output=i.plan(self.bundle,Path('/home/root/c5-dts-passthrough'))
        self.assertFalse(output['hardware_validated']);self.assertIn('disabled',output['boot'])
        self.assertEqual(before,sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*')))
    def test_tampered_artifact(self):
        (self.bundle/'supervisor.py').write_text('tampered')
        with self.assertRaises(ValueError):i.verify_bundle(self.bundle)
    def test_manifest_extra_or_traversal_rejected(self):
        data=json.loads((self.bundle/'manifest.json').read_text());data['files']['../private']='0'*64
        (self.bundle/'manifest.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError):i.verify_bundle(self.bundle)
    def test_symlink_file_rejected(self):
        source=self.bundle/'supervisor.py';source.unlink();source.symlink_to(self.bundle/'installer.py')
        with self.assertRaises(ValueError):i.verify_bundle(self.bundle)
    def test_symlink_parent_rejected(self):
        (self.bundle/'plugins').rename(self.root/'outside');(self.bundle/'plugins').symlink_to(self.root/'outside',target_is_directory=True)
        with self.assertRaises(ValueError):i.verify_bundle(self.bundle)
    def test_wrong_architecture(self):
        p=self.bundle/'c5_es_poc.ko';p.write_bytes(elf(1,40))
        manifest=json.loads((self.bundle/'manifest.json').read_text());manifest['files']['c5_es_poc.ko']=i.sha(p)
        (self.bundle/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):i.verify_bundle(self.bundle)
    def test_registry_environment_overrides_versioned_inheritance(self):
        with patch.dict(os.environ,{'GST_REGISTRY_1_0':'/foreign/registry','GST_PLUGIN_PATH_1_0':'/foreign/plugins','GST_REGISTRY_UPDATE':'no'}):
            env=i.registry_environment(self.bundle,self.root/'registry')
        self.assertEqual(env['GST_REGISTRY_1_0'],str(self.root/'registry'))
        self.assertEqual(env['GST_REGISTRY'],env['GST_REGISTRY_1_0'])
        self.assertEqual(env['GST_PLUGIN_PATH'],env['GST_PLUGIN_PATH_1_0'])
        self.assertIn('/mnt/lg/res/lglib/gstreamer-1.0',env['GST_PLUGIN_PATH_1_0'])
        self.assertEqual(env['GST_REGISTRY_UPDATE'],'yes')
    def test_activation_defaults_to_refusal(self):
        with patch.object(i,'require_target'):
            with self.assertRaises(RuntimeError):i.activate(self.bundle,self.root/'run')
        self.assertFalse((self.root/'run').exists())
    def test_review_blocker_prevents_activation_even_as_trial(self):
        data=json.loads((self.bundle/'manifest.json').read_text());data['blocking_review_findings']=['timing pending'];(self.bundle/'manifest.json').write_text(json.dumps(data))
        with patch.object(i,'require_target'):
            with self.assertRaises(RuntimeError):i.activate(self.bundle,self.root/'run',True)
        self.assertFalse((self.root/'run').exists())
    def rollback_case(self,bound=True,foreign=False,changed=False):
        original=self.root/'original';original.write_bytes(b'original')
        source=self.root/'source';source.write_bytes(b'new config')
        target=self.root/'target';os.link(source if bound else original,target)
        item={'source':str(source),'target':str(target),'source_identity':i.identity(source),'before_identity':i.identity(original),'before_sha256':i.sha(original),'phase':'planned'}
        if foreign:
            target.unlink();target.write_bytes(b'foreign')
        if changed:original.write_bytes(b'user changed')
        state={'bindings':[item]};statefile=self.root/'state.json';i.save(state,statefile)
        def unmount(command,path):
            self.assertEqual(command,'umount');Path(path).unlink();os.link(original,path)
        with patch.object(i,'command',side_effect=unmount) as command:
            if foreign or changed:
                with self.assertRaises(RuntimeError):i.rollback_bindings(state,statefile)
                if foreign:command.assert_not_called();self.assertEqual(target.read_bytes(),b'foreign')
            else:
                i.rollback_bindings(state,statefile);self.assertEqual(state['bindings'],[])
                self.assertEqual(target.read_bytes(),b'original');self.assertEqual(command.call_count,1 if bound else 0)
    def test_owned_bind_rollback(self):self.rollback_case()
    def test_journal_before_mount_is_recoverable(self):self.rollback_case(bound=False)
    def test_foreign_inode_is_preserved(self):self.rollback_case(foreign=True)
    def test_changed_original_content_not_overwritten(self):self.rollback_case(bound=False,changed=True)

class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.original=self.root/'original';self.original.write_bytes(b'original')
        self.source=self.root/'source';self.source.write_bytes(b'generated')
        self.target=self.root/'target';os.link(self.original,self.target)
        self.state={'bindings':[]};self.statefile=self.root/'state.json';i.save(self.state,self.statefile)
    def tearDown(self):self.temp.cleanup()
    def device(self,*args,**kwargs):
        if args[0]=='mount':
            self.target.unlink();os.link(self.source,self.target)
        elif args[0]=='umount':
            self.target.unlink();os.link(self.original,self.target)
        else:raise AssertionError('unexpected device command')
        return ''
    def test_successful_transaction_retains_its_journal(self):
        with patch.object(i,'command',side_effect=self.device):
            self.assertEqual(i.activation_transaction(self.state,self.statefile,[(self.source,self.target)],lambda:'ready'),'ready')
        self.assertEqual(self.target.read_bytes(),b'generated');self.assertEqual(self.state['bindings'][0]['phase'],'bound')
    def test_log_spawn_and_pid_save_failures_roll_back_bindings(self):
        for error in (OSError('log open failed'),FileNotFoundError('spawn failed'),OSError('save PID failed'),RuntimeError('readiness failed')):
            with self.subTest(error=str(error)):
                def start():raise error
                with patch.object(i,'command',side_effect=self.device):
                    with self.assertRaises(type(error)):
                        i.activation_transaction(self.state,self.statefile,[(self.source,self.target)],start)
                self.assertEqual(self.target.read_bytes(),b'original');self.assertEqual(self.state['bindings'],[])
    def test_uncertain_mutation_is_not_followed_by_more_mutations(self):
        with patch.object(i,'command',side_effect=i.UncertainCommand('mount deadline')) as command:
            with self.assertRaises(i.UncertainCommand):i.activation_transaction(self.state,self.statefile,[(self.source,self.target)],lambda:None)
            self.assertEqual(command.call_count,1)
        saved=json.loads(self.statefile.read_text())
        self.assertTrue(saved['uncertain_operation']);self.assertEqual(len(saved['bindings']),1)
    def test_non_timeout_failed_mount_is_recovered_by_identity(self):
        with patch.object(i,'command',side_effect=RuntimeError('mount failed')) as command:
            with self.assertRaises(RuntimeError):i.activation_transaction(self.state,self.statefile,[(self.source,self.target)],lambda:None)
            self.assertEqual(command.call_count,1)
        self.assertEqual(self.target.read_bytes(),b'original');self.assertEqual(self.state['bindings'],[])
class Boot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.bundle=bundle(self.root/'bundle')
        self.prefix=self.root/'install';self.prefix.mkdir()
        versions=self.prefix/'versions';versions.mkdir()
        self.version=versions/i.sha(self.bundle/'manifest.json')[:20]
        shutil.copytree(self.bundle,self.version)
        self.hook=self.root/'boot-hook'
        self.patches=[patch.object(i,'require_target'),patch.object(i,'stage',return_value=self.version),patch.object(i,'secure_prefix'),patch.object(i,'BOOT_HOOK',self.hook)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def install(self):return i.install_boot(self.bundle,self.prefix,self.hook)
    def test_install_and_idempotency_preserve_active_playback(self):
        with patch.object(i,'command',side_effect=AssertionError('device mutation')):
            self.assertEqual(self.install()['boot'],'configured')
            self.assertEqual(self.install()['boot'],'already configured')
        self.assertEqual(os.readlink(self.prefix/'current'),'versions/'+self.version.name)
        self.assertEqual(self.hook.read_bytes(),i.boot_hook(self.prefix))
        self.assertFalse((self.prefix/'persistent-transaction.json').exists())
    def test_failure_rolls_back_only_created_paths(self):
        original=i.create_owned_file
        def fail(path,data,mode=0o600):
            if path==self.hook:raise OSError('hook write failed')
            return original(path,data,mode)
        with patch.object(i,'create_owned_file',side_effect=fail):
            with self.assertRaises(OSError):self.install()
        self.assertFalse(self.hook.exists());self.assertFalse((self.prefix/'current').exists())
        self.assertFalse((self.prefix/'persistent-state.json').exists())
    def test_foreign_hook_preserved(self):
        self.hook.write_text('foreign')
        with self.assertRaises(RuntimeError):self.install()
        self.assertEqual(self.hook.read_text(),'foreign')
    def test_current_pointer_cannot_escape_version(self):
        self.install();(self.prefix/'current').unlink();(self.prefix/'current').symlink_to(self.root)
        with self.assertRaises(RuntimeError):i.current_bundle(self.prefix)
    def test_modified_artifact_rejected(self):
        self.install();(self.version/'supervisor.py').write_text('changed')
        with self.assertRaises(ValueError):i.current_bundle(self.prefix)
    def test_existing_runtime_refused_before_commands(self):
        self.install();run=self.root/'run';run.mkdir();(run/'installation-state.json').write_text('{}')
        with patch.object(i,'command',side_effect=AssertionError('device mutation')):
            with self.assertRaises(RuntimeError):i.start_boot(self.prefix,run)
    def test_boot_waits_for_all_restore_bindings_then_activates_exact_bundle(self):
        self.install();run=self.root/'run'
        with patch.object(i.Path,'is_file',return_value=True),patch.object(i,'identity',return_value=[1,2]),patch.object(i,'activate',return_value={'ready':True}) as activate:
            self.assertEqual(i.start_boot(self.prefix,run),{'ready':True})
        activate.assert_called_once_with(self.version,run,True)
    def test_restore_hook_timeout_never_activates_or_mutates(self):
        self.install();run=self.root/'run'
        with patch.object(i.time,'monotonic',side_effect=[0,11]),patch.object(i,'activate') as activate,patch.object(i,'command') as command:
            with self.assertRaises(RuntimeError):i.start_boot(self.prefix,run)
        activate.assert_not_called();command.assert_not_called();self.assertFalse(run.exists())
    def test_disable_boot_preserves_session_and_version(self):
        self.install()
        with patch.object(i,'command',side_effect=AssertionError('device mutation')):i.disable_boot(self.prefix)
        self.assertFalse(self.hook.exists());self.assertTrue(self.version.is_dir())
    def test_foreign_changed_hook_not_removed(self):
        self.install();self.hook.write_text('user change')
        with self.assertRaises(RuntimeError):i.disable_boot(self.prefix)
        self.assertEqual(self.hook.read_text(),'user change')
    def test_disable_respects_installation_lock(self):
        import fcntl
        self.install()
        with (self.prefix/'installation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):i.disable_boot(self.prefix)
        self.assertTrue(self.hook.exists())

class Platform(unittest.TestCase):
    def test_wrong_kernel_refused_before_boot_or_staging(self):
        with patch.object(i.os,'geteuid',return_value=0),patch.object(i.platform,'system',return_value='Linux'),patch.object(i.platform,'release',return_value='wrong'),patch.object(i,'stage') as stage:
            with self.assertRaises(RuntimeError):i.install_boot(Path('/unused'),Path('/unused'),i.BOOT_HOOK)
        stage.assert_not_called()

if __name__=='__main__':unittest.main()
