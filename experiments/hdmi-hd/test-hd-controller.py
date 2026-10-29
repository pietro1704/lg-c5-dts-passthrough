import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock,patch
import hd_controller as h

def state():
    return h.Snapshot(True,True,(1,1),True,100,2,1,True,True,True,100,2,False,(1,8,192000),True,True,True,True)

class Tests(unittest.TestCase):
    def test_reused_source_is_rejected_before_gain_or_decoder_access(self):
        original=state()
        for change in (dict(generation=3),dict(source_pointer=200),dict(native_idle=False),dict(physical_dts=False)):
            with self.subTest(change=change):
                target=object.__new__(h.Target)
                target.snapshot=lambda:replace(original,**change)
                with patch.object(h,'command',side_effect=AssertionError('stale source accessed')) as command:
                    with self.assertRaises(h.ScopeChanged):target.recover(original)
                command.assert_not_called()

    def test_decoder_setup_cancels_on_same_address_new_generation(self):
        original=replace(state(),adec_dts=False,adec_started=False)
        snapshots=iter([original,original,replace(original,generation=3)])
        target=object.__new__(h.Target)
        target.snapshot=lambda:next(snapshots)
        target.property_write=Mock()
        writes=[]
        def values(control,*args):
            writes.append(control)
            if control!=20:raise AssertionError('new source decoder started')
        target.alsa=SimpleNamespace(values=values)
        graph='gain_01234567_1_out0 -> bypass_12345678_2_in0'
        with patch.object(h,'command',return_value=graph):
            with self.assertRaises(h.ScopeChanged):target.recover(original)
        self.assertEqual(writes,[20])

    def test_real_hd_carrier_is_accepted(self):self.assertTrue(state().ready)
    def test_core_carrier_and_intermediate_pcm_cannot_release_hd(self):
        for transport in [(1,2,48000),(0,8,192000),(1,8,48000)]:
            self.assertFalse(replace(state(),transport=transport).ready)
    def test_changed_source_and_decoder_stop_cannot_release_audio(self):
        for change in [dict(foreground=False),dict(native_idle=False),dict(physical_dts=False),dict(adec_started=False),dict(gain_unmuted=False),dict(route_connected=False),dict(input_pointer=200),dict(sink_supported=False)]:
            self.assertFalse(replace(state(),**change).ready)

    def test_receiver_loss_and_native_playback_disarm_hd_recovery(self):
        for change in [dict(sink_supported=False),dict(native_idle=False),dict(physical_dts=False)]:
            with self.subTest(change=change):
                current=replace(state(),**change)
                backend=SimpleNamespace(snapshot=lambda:current,gate=Mock(),route=Mock(),release=Mock(),recover=Mock())
                controller=h.Controller(backend)
                self.assertEqual(controller.tick(),'waiting')
                backend.recover.assert_not_called();backend.route.assert_called_once_with(0)


if __name__=='__main__':unittest.main()
