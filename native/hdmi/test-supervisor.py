import importlib.util
from dataclasses import replace
from pathlib import Path
import sys,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
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
    def test_recovery_rejects_reused_source_before_any_decoder_write(self):
        original=state()
        for change in (dict(generation=2),dict(source_pointer=200),dict(native_idle=False),dict(physical_dts=False)):
            with self.subTest(change=change):
                target=object.__new__(m.Target)
                target.snapshot=lambda:replace(original,**change)
                target.alsa=SimpleNamespace(values=Mock(side_effect=AssertionError('stale decoder write')))
                with self.assertRaises(m.ScopeChanged):target.recover(original)
                target.alsa.values.assert_not_called()

    def test_decoder_setup_cancels_if_source_generation_changes(self):
        original=state()
        snapshots=iter([original,replace(original,generation=2)])
        target=object.__new__(m.Target)
        target.snapshot=lambda:next(snapshots)
        writes=[]
        def values(control,*args):
            writes.append(control)
            if control!=20:raise AssertionError('new source decoder started')
        target.alsa=SimpleNamespace(values=values)
        with self.assertRaises(m.ScopeChanged):target.recover(original)
        self.assertEqual(writes,[20])

    def test_bind_accepts_only_its_own_generation_advance(self):
        for advance in (1,2):
            with self.subTest(advance=advance):
                original=state(adec_dts=True,adec_started=True)
                bound=replace(original,input_pointer=100,generation=original.generation+advance,
                    transport=(1,2,48000))
                current=[original]
                target=object.__new__(m.Target)
                target.snapshot=lambda:current[0]
                target.input=Path('/test/hdmi/prop')
                target.route=Mock()
                target.alsa=SimpleNamespace(values=Mock(return_value=[2,1,0,0,0]))
                writes=[]
                def property_write(path,key,value,pointer,generation):
                    target.scope(pointer,generation);writes.append((key,value))
                target.property_write=property_write
                bind=Mock()
                bind.write_text.side_effect=lambda _:current.__setitem__(0,bound)
                route=Mock();route.__truediv__=Mock(side_effect=lambda name:
                    bind if name=='bind_input' else SimpleNamespace(read_text=lambda:'1'))
                graph=('renderer_1_out100 -> mixer_0_in100\n'
                    'mixer_0_out100 -> output_3_in0\n'
                    'gain_01234567_1_out0 -> bypass_12345678_2_in0')
                with patch.object(m,'ROUTE',route),patch.object(m,'command',return_value=graph),patch.object(Path,'is_file',return_value=True):
                    if advance==1:
                        target.recover(original)
                        self.assertEqual(target.owned,(100,2))
                        self.assertEqual(writes[-2:],[('mute-input','false'),('mute-output','false')])
                    else:
                        with self.assertRaises(m.ScopeChanged):target.recover(original)
                        target.route.assert_not_called();target.alsa.values.assert_not_called()
                        self.assertEqual(writes,[])

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
