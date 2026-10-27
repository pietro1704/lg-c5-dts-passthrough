import json,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
import runtime_settings as r
from edid import constrain_dts

def original():
    data=bytearray(512);data[:8]=bytes.fromhex('00ffffffffffff00');data[126]=1
    data[128:135]=bytes([2,3,7,0,0x22,0x09,0x07])
    # A legal three-byte audio descriptor with LPCM only.
    data[132:136]=bytes([0x23,0x09,0x07,0x07]);data[130]=8
    for start in (0,128):data[start+127]=(-sum(data[start:start+127]))&255
    return bytes(data)

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.run=Path(self.temp.name);self.hw=original();self.sad=bytes.fromhex('3d04c0');self.calls=[]
    def popen(self,argv,**kwargs):
        outer=self
        class Process:
            returncode=0
            def communicate(self,data=None,timeout=None):
                if data is not None:
                    outer.hw=bytes(int(x,16) for x in data.decode().split());outer.calls.append('edid')
                else:outer.calls.append('hpd')
                return b'',b''
        return Process()
    def apply(self,**kwargs):
        with patch.object(r,'ps5_connected',return_value=True),patch.object(r,'receiver_sad',return_value=self.sad),\
             patch.object(r,'hardware_edid',side_effect=lambda:self.hw),patch.object(r.time,'sleep'),patch.object(r.subprocess,'Popen',side_effect=self.popen):
            return r.apply_edid(self.run,notify_source=True,**kwargs)
    def test_standby_loses_edid_repair_requests_new_handshake_each_time(self):
        self.assertTrue(self.apply());self.assertEqual(self.calls,['edid','hpd'])
        self.hw=original()
        self.assertTrue(self.apply());self.assertEqual(self.calls,['edid','hpd','edid','hpd'])
    def test_unchanged_edid_never_restarts_working_ps5(self):
        self.hw=constrain_dts(self.hw,self.sad)
        self.assertTrue(self.apply());self.assertEqual(self.calls,[])
    def test_disconnected_or_unsupported_source_never_writes_or_restarts(self):
        for source,receiver in [(False,self.sad),(True,None)]:
            with patch.object(r,'ps5_connected',return_value=source),patch.object(r,'receiver_sad',return_value=receiver),patch.object(r.subprocess,'Popen') as process:
                self.assertFalse(r.apply_edid(self.run,notify_source=True));process.assert_not_called()
    def test_restart_timeout_leaves_uncertainty_and_never_retries(self):
        with patch.object(r.subprocess,'Popen') as process:
            process.return_value.communicate.side_effect=subprocess.TimeoutExpired('hpd',3)
            with self.assertRaises(RuntimeError):r.renegotiate(self.run)
            with self.assertRaises(RuntimeError):r.renegotiate(self.run)
            self.assertEqual(process.call_count,1);process.return_value.terminate.assert_called_once()
    def test_restart_rate_limit_stops_before_fourth_pulse(self):
        with patch.object(r.subprocess,'Popen',side_effect=self.popen):
            for _ in range(3):r.renegotiate(self.run)
            with self.assertRaises(RuntimeError):r.renegotiate(self.run)
        self.assertEqual(self.calls,['hpd']*3)
    def test_uncertain_previous_restart_blocks_edid_writes_too(self):
        (self.run/'hpd-policy.json').write_text(json.dumps({'phase':'prepared','requests':[0]}))
        with self.assertRaises(RuntimeError):self.apply()
        self.assertEqual(self.calls,[]);self.assertEqual(self.hw,original())

if __name__=='__main__':unittest.main()
