from pathlib import Path
import hashlib,importlib.util,json,subprocess,sys,tempfile,unittest
from unittest.mock import patch,Mock
spec=importlib.util.spec_from_file_location('global_install',Path(__file__).with_name('global_install.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
build_spec=importlib.util.spec_from_file_location('global_builder',Path(__file__).with_name('build-global-package.py'))
b=importlib.util.module_from_spec(build_spec);build_spec.loader.exec_module(b)


def elf(kind,machine):
    data=bytearray(64);data[:4]=b'\x7fELF';data[4]=kind;data[18:20]=machine.to_bytes(2,'little');return bytes(data)


def bundle(root):
    root.mkdir()
    for name in m.PAYLOAD:
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'owned text\n')
    for name,kind,machine in [('native/c5_es_poc.ko',2,183),('hdmi/broker-gate.ko',2,183),('hdmi/auto-route.ko',2,183),
            ('hdmi_hd/broker-gate.ko',2,183),('hdmi_hd/auto-route.ko',2,183),('native/plugins/libgstdtspassthrough.so',1,40)]:
        (root/name).write_bytes(elf(kind,machine))
    native_files=['installer.py','configuration.py','supervisor.py','c5_es_poc.ko','plugins/libgstdtspassthrough.so']
    (root/'native/manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'module_build':m.NATIVE_BUILD,
        'blocking_review_findings':[],'files':{p:m.sha(root/'native'/p) for p in native_files}}))
    hdmi_files=['broker-gate.ko','auto-route.ko','hdmi_supervisor.py','alsa_controls.py','luna_rpc.py','runtime_settings.py','edid.py','hpd.py']
    (root/'hdmi/hdmi-manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'files':{p:m.sha(root/'hdmi'/p) for p in hdmi_files}}))
    (root/'hdmi_hd/hdmi-manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'files':{p:m.sha(root/'hdmi_hd'/p) for p in m.HD_FILES}}))
    seal(root)
    return root


def seal(root):
    (root/'global-manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'files':{p:m.sha(root/p) for p in m.PAYLOAD}}))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundle=bundle(self.root/'bundle');self.prefix=self.root/'installed';self.hook=self.root/'hook'
    def test_cold_registry_view_is_unmounted_on_success_failure_and_cancellation(self):
        for failure in (None,RuntimeError('probe failure'),KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                native=Mock();native.seed_cold_registry.return_value={'seeded_sha256':'a'*64}
                if failure is not None:native.seed_cold_registry.side_effect=failure
                spec=Mock()
                with patch.object(m,'require_target'),patch.object(m.importlib.util,'spec_from_file_location',return_value=spec),patch.object(m.importlib.util,'module_from_spec',return_value=native):
                    if failure is None:self.assertEqual(m.cold_registry(self.bundle,self.prefix)['cold_registry'],'prepared')
                    else:
                        with self.assertRaises(type(failure)):m.cold_registry(self.bundle,self.prefix)
                commands=[call.args[0] for call in native.command.call_args_list]
                self.assertEqual(commands,['mount','umount'])
    def test_install_and_repeat_preserve_current_session(self):
        first=m.install(self.bundle,self.prefix,self.hook);second=m.install(self.bundle,self.prefix,self.hook)
        self.assertEqual(first['current_audio'],'unchanged');self.assertEqual(second['boot'],'already installed')
        self.assertTrue((self.prefix/'current').is_symlink());self.assertEqual(self.hook.read_bytes(),m.hook_bytes(self.prefix))
        self.assertEqual(m.current_bundle(self.prefix,self.hook)[1]['version'],first['version'])
    def test_changed_artifact_is_rejected_before_staging(self):
        (self.bundle/'hdmi/alsa_controls.py').write_text('changed')
        with self.assertRaises(RuntimeError):m.install(self.bundle,self.prefix,self.hook)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.hook.exists())
    def test_hd_artifact_or_manifest_change_is_rejected(self):
        (self.bundle/'hdmi_hd/hd_controller.py').write_text('changed')
        seal(self.bundle)
        with self.assertRaisesRegex(RuntimeError,'HDMI-HD manifest mismatch'):m.install(self.bundle,self.prefix,self.hook)
        self.assertFalse(self.hook.exists())

    def test_builder_preserves_native_and_includes_hd_boot_controller(self):
        output=self.root/'candidate';donor=self.bundle/'hdmi_hd'
        modules={name:donor/name for name in ('broker-gate.ko','auto-route.ko')}
        with patch.object(b,'verified_modules',return_value=modules),patch.object(b.subprocess,'run'):
            manifest=b.build(self.bundle/'native',output,Path(__file__).resolve().parents[2],Path('objcopy'),donor,donor)
        self.assertEqual(set(manifest['files']),m.PAYLOAD)
        for name in json.loads((self.bundle/'native/manifest.json').read_text())['files']:
            self.assertEqual((output/'native'/name).read_bytes(),(self.bundle/'native'/name).read_bytes())
        # Exercise the real offline entrypoint; no target or root is required.
        result=subprocess.run([sys.executable,str(output/'global_install.py'),'dry-run'],
            capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['boot'],'unchanged')
        self.assertEqual((output/'hdmi_hd/hdmi_hd_supervisor.py').read_bytes(),Path(__file__).with_name('hdmi_hd_supervisor.py').read_bytes())
        with self.assertRaises(RuntimeError):b.build(self.bundle/'native',output,self.root,Path('unused'),donor)

    def test_native_manifest_traversal_cannot_overwrite_outside_staging(self):
        incoming=self.root/'incoming';incoming.mkdir()
        source=bundle(incoming/'bundle')
        replacement=incoming/'outside';replacement.write_text('untrusted replacement')
        outside=self.root/'outside';outside.write_text('keep original')
        manifest=json.loads((source/'native/manifest.json').read_text())
        manifest['files']['../../outside']=m.sha(replacement)
        (source/'native/manifest.json').write_text(json.dumps(manifest))
        donor=source/'hdmi_hd';modules={name:donor/name for name in ('broker-gate.ko','auto-route.ko')}
        with patch.object(b,'verified_modules',return_value=modules),patch.object(b.subprocess,'run') as command:
            with self.assertRaises(RuntimeError):
                b.build(source/'native',self.root/'candidate',Path(__file__).resolve().parents[2],Path('unused'),donor,donor)
            self.assertEqual(outside.read_text(),'keep original')
            command.assert_not_called()
        self.assertFalse((self.root/'candidate').exists())
        self.assertEqual(list(self.root.glob('.global-package-*')),[])

    def test_native_donor_identity_and_blockers_rejected_before_staging(self):
        path=self.bundle/'native/manifest.json';original=path.read_text()
        cases=[('schema',2),('kernel','wrong'),('module_build','0'*64),
            ('blocking_review_findings',['unverified']),('files',{}),
            ('files',dict(json.loads(original)['files'],unexpected='0'*64))]
        for key,value in cases:
            with self.subTest(key=key,value=value):
                manifest=json.loads(original);manifest[key]=value;path.write_text(json.dumps(manifest))
                with patch.object(b.tempfile,'mkdtemp',wraps=tempfile.mkdtemp) as stage,patch.object(b,'verified_modules') as modules:
                    with self.assertRaises(RuntimeError):
                        b.build(self.bundle/'native',self.root/'candidate',self.root,Path('unused'),self.bundle/'hdmi_hd')
                    stage.assert_not_called();modules.assert_not_called()
        path.write_text(original)

    def test_changed_native_donor_rejected_without_staging(self):
        (self.bundle/'native/supervisor.py').write_text('changed')
        with patch.object(b.tempfile,'mkdtemp',wraps=tempfile.mkdtemp) as stage:
            with self.assertRaisesRegex(RuntimeError,'native donor changed'):
                b.build(self.bundle/'native',self.root/'candidate',self.root,Path('unused'),self.bundle/'hdmi_hd')
            stage.assert_not_called()

    def test_native_donor_symlink_components_rejected_before_staging(self):
        native=self.bundle/'native'
        for relative in ('manifest.json','plugins','supervisor.py'):
            with self.subTest(relative=relative):
                path=native/relative;saved=native/(relative+'.saved');path.rename(saved);path.symlink_to(saved.name)
                try:
                    with patch.object(b.tempfile,'mkdtemp',wraps=tempfile.mkdtemp) as stage,patch.object(b,'verified_modules') as modules:
                        with self.assertRaises(RuntimeError):
                            b.build(native,self.root/'candidate',self.root,Path('unused'),self.bundle/'hdmi_hd')
                        stage.assert_not_called();modules.assert_not_called()
                finally:path.unlink();saved.rename(path)
        linked=self.root/'linked-native';linked.symlink_to(native)
        with patch.object(b.tempfile,'mkdtemp',wraps=tempfile.mkdtemp) as stage:
            with self.assertRaises(RuntimeError):
                b.build(linked,self.root/'candidate',self.root,Path('unused'),self.bundle/'hdmi_hd')
            stage.assert_not_called()

    def test_builder_failure_and_cancellation_clean_only_staging(self):
        donor=self.bundle/'hdmi_hd';modules={name:donor/name for name in ('broker-gate.ko','auto-route.ko')}
        keep=self.root/'keep';keep.write_text('user data')
        for error in (OSError('copy failed'),KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch.object(b,'verified_modules',return_value=modules),patch.object(b.shutil,'copy2',side_effect=error):
                    with self.assertRaises(type(error)):
                        b.build(self.bundle/'native',self.root/'candidate',self.root,Path('unused'),donor,donor)
                self.assertEqual(keep.read_text(),'user data')
                self.assertFalse((self.root/'candidate').exists())
                self.assertEqual(list(self.root.glob('.global-package-*')),[])

    def test_module_donor_requires_matching_hashes_and_approved_build(self):
        donor=self.bundle/'hdmi_hd'
        expected={name:m.sha(donor/name) for name in ('broker-gate.ko','auto-route.ko')}
        self.assertEqual(set(b.verified_modules(donor,expected)),set(expected))
        with self.assertRaises(RuntimeError):b.verified_modules(donor,{name:'0'*64 for name in expected})
        (donor/'broker-gate.ko').write_text('changed')
        with self.assertRaises(RuntimeError):b.verified_modules(donor)

    def test_start_controller_requires_live_matching_readiness_and_command_path(self):
        for kind in ['success','wrong pid','dead']:
            with self.subTest(kind=kind):
                run=self.root/kind
                def popen(argv,**kwargs):
                    self.assertEqual(kwargs['env']['PATH'],'/usr/sbin:/usr/bin:/sbin:/bin')
                    ready=run/'hdmi-ready.json'
                    ready.write_text(json.dumps({'pid':10 if kind=='wrong pid' else 20}));ready.chmod(0o600)
                    return Mock(pid=20,poll=Mock(return_value=0 if kind=='dead' else None))
                with patch.object(m.subprocess,'Popen',side_effect=popen) as start:
                    if kind=='success':self.assertEqual(m.start_controller(self.bundle,run,'owned.py'),20)
                    else:
                        with self.assertRaises(RuntimeError):m.start_controller(self.bundle,run,'owned.py')
                    self.assertEqual(start.call_count,1)
                    with self.assertRaises(RuntimeError):m.start_controller(self.bundle,run,'owned.py')
                    self.assertEqual(start.call_count,1)
    def test_wrong_architecture_is_rejected(self):
        (self.bundle/'hdmi/auto-route.ko').write_bytes(elf(2,62));seal(self.bundle)
        with self.assertRaisesRegex(RuntimeError,'architecture'):m.verify(self.bundle)
    def test_foreign_hook_and_user_files_are_preserved(self):
        self.hook.write_text('foreign');self.prefix.mkdir(mode=0o700);note=self.prefix/'note.txt';note.write_text('keep')
        with self.assertRaises(RuntimeError):m.install(self.bundle,self.prefix,self.hook)
        self.assertEqual(self.hook.read_text(),'foreign');self.assertEqual(note.read_text(),'keep')
        self.assertFalse((self.prefix/'current').exists())
    def test_symlink_artifact_is_never_accepted(self):
        target=self.bundle/'hdmi/alsa_controls.py';outside=self.root/'outside';outside.write_bytes(target.read_bytes());target.unlink();target.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError,'symlink'):m.verify(self.bundle)
    def test_state_write_failure_rolls_back_only_owned_hook_and_link(self):
        with patch.object(m,'atomic_file',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):m.install(self.bundle,self.prefix,self.hook)
        self.assertFalse(self.hook.exists());self.assertFalse((self.prefix/'current').exists())
        self.assertFalse((self.prefix/'installed.json').exists())
        self.assertTrue((self.prefix/'versions').is_dir())
    def test_disable_refuses_changed_hook_and_preserves_session(self):
        m.install(self.bundle,self.prefix,self.hook);self.hook.write_text('foreign edit')
        with self.assertRaises(RuntimeError):m.disable(self.prefix,self.hook)
        self.assertTrue((self.prefix/'current').is_symlink())
    def test_atomic_state_never_replaces_foreign_file(self):
        path=self.root/'state.json';path.write_text('foreign')
        with self.assertRaises(FileExistsError):m.atomic_file(path,b'new')
        self.assertEqual(path.read_text(),'foreign');self.assertFalse(path.with_suffix('.next').exists())


if __name__=='__main__':unittest.main()
