import hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT.parent.parent/'experiments/hdmi-hd'))
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('hd_boot',ROOT/'hdmi_hd_supervisor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def capabilities(**changes):
    value={'arcActivation':'eARC','advancedEarcCapability':[
        {'audioCodingType':'DTSHD_MA','hexSAD':'5f7e01'}]}
    value.update(changes)
    return {'settings':[{'category':'external_arc','value':value}]}


class Tests(unittest.TestCase):
    def test_receiver_requires_eight_channel_192k_ma_over_earc(self):
        self.assertTrue(m.supports_hd(capabilities()))
        for response in [capabilities(arcActivation='ARC'),capabilities(advancedEarcCapability=[]),
                capabilities(advancedEarcCapability=[{'audioCodingType':'DTS','hexSAD':'3e1fc0'}]),
                capabilities(advancedEarcCapability=[{'audioCodingType':'DTSHD_MA','hexSAD':'5d7e01'}]),
                capabilities(advancedEarcCapability=[{'audioCodingType':'DTSHD_MA','hexSAD':'5f0401'}]),
                capabilities(advancedEarcCapability=[{'audioCodingType':'DTSHD_MA','hexSAD':'invalid'}]),
                {'settings':[]}, {'settings':capabilities()['settings']*2}]:
            with self.subTest(response=response):self.assertFalse(m.supports_hd(response))

    def test_manifest_rejects_changed_missing_and_symlink_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in m.FILES:(root/name).write_text('owned')
            manifest={'schema':1,'kernel':m.KERNEL,'files':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in m.FILES}}
            (root/'hdmi-manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(m.verify(root),manifest)
            target=root/'hd_controller.py';target.write_text('changed')
            with self.assertRaises(RuntimeError):m.verify(root)
            target.unlink()
            with self.assertRaises(RuntimeError):m.verify(root)
            other=root/'foreign';other.write_text('owned');target.symlink_to(other)
            with self.assertRaises(RuntimeError):m.verify(root)

    def test_no_activation_flag_never_opens_hardware(self):
        with patch.object(m,'Target') as target:
            with self.assertRaises(SystemExit):m.main(['--bundle','unused','--run','unused'])
            target.assert_not_called()

    def test_loop_records_recovery_and_cancellation_then_stops_on_latched_error(self):
        controller=Mock(halted=False)
        def tick():
            if controller.tick.call_count==1:return 'recovered'
            if controller.tick.call_count==2:return 'source changed'
            controller.halted=True;raise RuntimeError('unconfirmed vendor operation')
        controller.tick.side_effect=tick;messages=[]
        with self.assertRaises(RuntimeError):m.run(controller,messages.append,lambda _:None)
        self.assertEqual([s['hdmi_hd'] for s in messages],['recovered','source changed'])
        self.assertEqual(controller.tick.call_count,3)


if __name__=='__main__':unittest.main()
