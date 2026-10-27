import importlib.util
from dataclasses import replace
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).parent))
spec=importlib.util.spec_from_file_location('hdmi',Path(__file__).with_name('hdmi_supervisor.py'))
m=importlib.util.module_from_spec(spec);sys.modules['hdmi']=m;spec.loader.exec_module(m)


def state(**changes):
    values=dict(foreground=True,native_idle=True,main=(1,1),physical_dts=True,
        source_pointer=100,source_type=2,source_index=1,adec_open=True,adec_dts=False,
        adec_started=False,input_pointer=0,generation=1,invalidated=False,
        transport=(0,2,48000),flowing=False,route_connected=False,gain_unmuted=False)
    values.update(changes);return m.Snapshot(**values)


class Backend:
    def __init__(self,snapshot):self.state=snapshot;self.calls=[];self.error=None
    def snapshot(self):return self.state
    def gate(self,seconds):self.calls.append(('gate',seconds))
    def route(self,seconds):self.calls.append(('route',seconds))
    def release(self,state):self.calls.append(('release',state.source_pointer,state.generation))
    def recover(self,original):
        self.calls.append(('recover',original.source_pointer,original.generation))
        if self.error:raise self.error
        self.state=replace(self.state,adec_dts=True,adec_started=True,input_pointer=100,
            generation=2,transport=(1,2,48000),flowing=True,route_connected=True,gain_unmuted=True)


class Tests(unittest.TestCase):
    def test_initial_unknown_decoder_is_recovered(self):
        b=Backend(state());c=m.Controller(b)
        self.assertEqual(c.tick(),'recovered');self.assertTrue(c.active)
        self.assertEqual(c.tick(),'active')
        self.assertEqual(sum(x[0]=='recover' for x in b.calls),1)
    def test_real_pattern_format_loss_waits_and_then_recovers_again(self):
        b=Backend(state());c=m.Controller(b);c.tick()
        b.state=replace(b.state,physical_dts=False,adec_dts=False,adec_started=False,
            flowing=False,transport=(0,2,48000))
        self.assertEqual(c.tick(),'waiting');self.assertFalse(c.active)
        b.state=replace(b.state,physical_dts=True)
        self.assertEqual(c.tick(),'recovered');self.assertTrue(c.active)
    def test_unrelated_app_native_owner_source_port_and_disconnected_input_never_recover(self):
        for changes in [dict(foreground=False),dict(native_idle=False),dict(main=(1,0)),
            dict(source_type=3),dict(source_index=0),dict(source_pointer=0),dict(adec_open=False)]:
            b=Backend(state(**changes));c=m.Controller(b)
            self.assertEqual(c.tick(),'waiting',changes)
            self.assertFalse(any(x[0]=='recover' for x in b.calls),changes)
    def test_stale_pointer_invalidation_and_source_generation_require_new_proof(self):
        for changes in [dict(input_pointer=90),dict(invalidated=True),dict(gain_unmuted=False),dict(route_connected=False)]:
            b=Backend(state(adec_dts=True,adec_started=True,input_pointer=100,transport=(1,2,48000),
                flowing=True,route_connected=True,gain_unmuted=True))
            b.state=replace(b.state,**changes);c=m.Controller(b)
            self.assertEqual(c.tick(),'recovered',changes)
    def test_transport_format_alone_is_not_acceptance(self):
        for changes in [dict(transport=(1,8,192000)),dict(transport=(0,2,48000)),dict(flowing=False)]:
            b=Backend(state(adec_dts=True,adec_started=True,input_pointer=100,transport=(1,2,48000),
                flowing=True,route_connected=True,gain_unmuted=True))
            b.state=replace(b.state,**changes);self.assertFalse(b.state.ready)
    def test_source_change_during_recovery_cancels_new_routes(self):
        b=Backend(state());b.error=m.ScopeChanged('generation changed');c=m.Controller(b)
        self.assertEqual(c.tick(),'source changed');self.assertEqual(b.calls[-1],('route',0))
        self.assertFalse(c.active)
    def test_uncertain_write_latches_without_more_commands(self):
        b=Backend(state());b.error=m.UncertainOperation('deadline');c=m.Controller(b)
        with self.assertRaises(m.UncertainOperation):c.tick()
        calls=list(b.calls);self.assertEqual(c.tick(),'halted');self.assertEqual(b.calls,calls)
    def test_repeated_known_failure_stops_after_three_attempts(self):
        b=Backend(state());b.error=RuntimeError('route not ready');c=m.Controller(b)
        for _ in range(3):
            with self.assertRaises(RuntimeError):c.tick()
        self.assertTrue(c.halted);self.assertEqual(c.tick(),'halted')
    def test_snapshot_health_failure_latches_immediately(self):
        b=Backend(state());b.snapshot=lambda:(_ for _ in ()).throw(RuntimeError('no health'))
        c=m.Controller(b)
        with self.assertRaises(RuntimeError):c.tick()
        self.assertTrue(c.halted);self.assertEqual(b.calls,[])
    def test_settings_menu_roundtrip_waits_then_recovers_without_exiting(self):
        b=Backend(state());c=m.Controller(b);self.assertEqual(c.tick(),'recovered')
        b.state=replace(b.state,foreground=False)
        self.assertEqual(c.tick(),'waiting');self.assertFalse(c.halted)
        self.assertEqual(b.calls[-2:], [('route',0),('release',100,2)])
        b.state=replace(b.state,foreground=True,adec_dts=False,adec_started=False,
            flowing=False,transport=(0,2,48000))
        self.assertEqual(c.tick(),'recovered');self.assertFalse(c.halted)
        self.assertEqual(c.tick(),'active')


if __name__=='__main__':unittest.main()
