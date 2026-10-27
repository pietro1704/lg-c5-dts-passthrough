import unittest
from dataclasses import replace
import hd_controller as h

def state():
    return h.Snapshot(True,True,(1,1),True,100,2,1,True,True,True,100,2,False,(1,8,192000),True,True,True,True)

class Tests(unittest.TestCase):
    def test_real_hd_carrier_is_accepted(self):self.assertTrue(state().ready)
    def test_core_carrier_and_intermediate_pcm_cannot_release_hd(self):
        for transport in [(1,2,48000),(0,8,192000),(1,8,48000)]:
            self.assertFalse(replace(state(),transport=transport).ready)
    def test_changed_source_and_decoder_stop_cannot_release_audio(self):
        for change in [dict(foreground=False),dict(native_idle=False),dict(physical_dts=False),dict(adec_started=False),dict(gain_unmuted=False),dict(route_connected=False),dict(input_pointer=200)]:
            self.assertFalse(replace(state(),**change).ready)

if __name__=='__main__':unittest.main()
