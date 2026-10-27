from pathlib import Path
import hashlib,importlib.util,json,tempfile,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('global_install',Path(__file__).with_name('global_install.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def elf(kind,machine):
    data=bytearray(64);data[:4]=b'\x7fELF';data[4]=kind;data[18:20]=machine.to_bytes(2,'little');return bytes(data)


def bundle(root):
    root.mkdir()
    for name in m.PAYLOAD:
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'owned text\n')
    for name,kind,machine in [('native/c5_es_poc.ko',2,183),('hdmi/broker-gate.ko',2,183),('hdmi/auto-route.ko',2,183),('native/plugins/libgstdtspassthrough.so',1,40)]:
        (root/name).write_bytes(elf(kind,machine))
    native_files=['installer.py','configuration.py','supervisor.py','c5_es_poc.ko','plugins/libgstdtspassthrough.so']
    (root/'native/manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'module_build':m.NATIVE_BUILD,
        'blocking_review_findings':[],'files':{p:m.sha(root/'native'/p) for p in native_files}}))
    hdmi_files=['broker-gate.ko','auto-route.ko','hdmi_supervisor.py','alsa_controls.py','luna_rpc.py','runtime_settings.py','edid.py','hpd.py']
    (root/'hdmi/hdmi-manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'files':{p:m.sha(root/'hdmi'/p) for p in hdmi_files}}))
    seal(root)
    return root


def seal(root):
    (root/'global-manifest.json').write_text(json.dumps({'schema':1,'kernel':m.KERNEL,'files':{p:m.sha(root/p) for p in m.PAYLOAD}}))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundle=bundle(self.root/'bundle');self.prefix=self.root/'installed';self.hook=self.root/'hook'
    def test_install_and_repeat_preserve_current_session(self):
        first=m.install(self.bundle,self.prefix,self.hook);second=m.install(self.bundle,self.prefix,self.hook)
        self.assertEqual(first['current_audio'],'unchanged');self.assertEqual(second['boot'],'already installed')
        self.assertTrue((self.prefix/'current').is_symlink());self.assertEqual(self.hook.read_bytes(),m.hook_bytes(self.prefix))
        self.assertEqual(m.current_bundle(self.prefix,self.hook)[1]['version'],first['version'])
    def test_changed_artifact_is_rejected_before_staging(self):
        (self.bundle/'hdmi/alsa_controls.py').write_text('changed')
        with self.assertRaises(RuntimeError):m.install(self.bundle,self.prefix,self.hook)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.hook.exists())
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
