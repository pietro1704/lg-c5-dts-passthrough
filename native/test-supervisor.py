import importlib.util,sys,unittest,tempfile,hashlib,subprocess,io
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch,Mock
spec=importlib.util.spec_from_file_location('c5supervisor',Path(__file__).with_name('supervisor.py'))
s=importlib.util.module_from_spec(spec);sys.modules[spec.name]=s;spec.loader.exec_module(s)
DSP='subgraph output { label="output_arc:test"; channels=(int)8 bits-per-sample=(int)16 rate=(int)192000 es=(bool)true codec=(str)dts_hd_ma }'
def graph(index):return f'digraph {{ renderer_{index}_out100 -> mixer_0_in100; mixer_0_out100 -> output_3_in0; }}'
def state(index=0):
    rows=['main 1 '+str(index)+' 1',f'0 123 1 9 {index} 1 1 1 1 1 0 1 1 1']
    rows.extend(f'{n} 0 0 0 4294967295 0 0 0 0 0 0 0 0 0' for n in range(1,4))
    return s.parse_state('\n'.join(rows))
class Formats(unittest.TestCase):
    def test_both_native_indices(self):
        for index in (0,1):
            st=state(index);self.assertTrue(s.route_confirmed(st,st.owner(123),graph(index),DSP))
    def test_other_main_port(self):
        st=state(1);self.assertFalse(s.route_confirmed(replace(st,port=0),st.owner(123),graph(1),DSP))
    def test_wrong_source(self):
        st=state();self.assertFalse(s.route_confirmed(replace(st,source=0),st.owner(123),graph(0),DSP))
    def test_wrong_graph_identity(self):
        st=state(1);self.assertFalse(s.route_confirmed(st,st.owner(123),graph(0),DSP))
    def test_no_release_without_every_gate(self):
        st=state()
        for field,value in [('active',0),('index',4294967295),('renderer',0),('es',0),('captures',0),('routes',0),('hd',0)]:
            self.assertFalse(s.route_confirmed(st,replace(st.owner(123),**{field:value}),graph(0),DSP),field)
    def test_ac3_pcm_and_wrong_carrier(self):
        for bad in (DSP.replace('dts_hd_ma','ac3'),DSP.replace('true','false'),DSP.replace('192000','48000'),DSP.replace('channels=(int)8','channels=(int)2')):
            st=state();self.assertFalse(s.route_confirmed(st,st.owner(123),graph(0),bad))
    def test_malformed_state(self):
        for text in ('','main 1 0 1','other 1 0 1\n'+'0 '*40):
            with self.assertRaises((RuntimeError,ValueError)):s.parse_state(text)
    def test_wrong_epoch_and_missing_owner(self):
        for pid,epoch in ((124,9),(123,8)):
            with self.assertRaises(RuntimeError):state().owner(pid,epoch)
    def test_ambiguous_owner(self):
        st=state();st=replace(st,leases=(st.leases[0],replace(st.leases[0],slot=1),*st.leases[2:]))
        with self.assertRaises(RuntimeError):st.owner(123)
class Driver(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.module=self.root/'driver.ko';self.module.write_bytes(b'owned module')
        self.sys=self.root/'sys';self.params=self.sys/'parameters';self.params.mkdir(parents=True)
        (self.sys/'initstate').write_text('live\n')
        (self.params/'session_protocol').write_text('3\n')
        (self.params/'selftest_only').write_text('N\n')
        (self.params/'lease_api_only').write_text('N\n')
        (self.params/'session_build').write_text('expected\n')
        (self.params/'session_state').write_text('main 1 0 0\n0 123 0 9 4294967295 0 0 0 0 0 0 0 0 0\n'+'\n'.join(f'{n} 0 0 0 4294967295 0 0 0 0 0 0 0 0 0' for n in range(1,4)))
        self.kernel=s.Kernel(self.module,hashlib.sha256(self.module.read_bytes()).hexdigest(),'expected',self.sys)
    def tearDown(self):self.temp.cleanup()
    def test_grant_without_reload(self):
        with patch.object(s,'command') as cmd:
            self.assertEqual(self.kernel.grant(123),9);cmd.assert_not_called()
        self.assertEqual((self.params/'grant_pid').read_text(),'123\n')
    def test_wrong_loaded_revision(self):
        (self.params/'session_build').write_text('old')
        with self.assertRaises(RuntimeError):self.kernel.grant(123)
        self.assertFalse((self.params/'grant_pid').exists())
    def test_wrong_protocol(self):
        (self.params/'session_protocol').write_text('2')
        with self.assertRaises(RuntimeError):self.kernel.grant(123)
    def test_changed_artifact(self):
        self.module.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):self.kernel.grant(123)
    def test_control_abi_rejected_before_write(self):
        with patch.object(s,'command',return_value='unrecognized') as cmd:
            with self.assertRaises(RuntimeError):self.kernel.refresh()
            self.assertEqual(cmd.call_count,1)
    def test_control_same_tuple(self):
        with patch.object(s,'command',side_effect=['  : values=2,1,2,0,0\n','ok']) as cmd:
            self.kernel.refresh();self.assertEqual(cmd.call_args.args[-1],'2,1,2,0,0')
    def test_cancel_between_control_read_and_write_does_not_latch(self):
        def cancelled():raise s.RouteChanged('paused')
        with patch.object(s,'command',return_value='  : values=2,1,2,0,0\n') as cmd:
            with self.assertRaises(s.RouteChanged):self.kernel.refresh(cancelled)
            self.assertEqual(cmd.call_count,1)
        self.assertFalse(self.kernel.io_failed.is_set())
        with patch.object(s,'command',side_effect=['  : values=2,1,2,0,0\n','ok']) as cmd:
            self.kernel.refresh();self.assertEqual(cmd.call_count,2)
    def test_blocked_vendor_child_never_waited_again(self):
        child=Mock();child.stdout=io.StringIO();child.stderr=io.StringIO()
        child.communicate.side_effect=subprocess.TimeoutExpired('amixer',2)
        with patch.object(s.subprocess,'Popen',return_value=child):
            with self.assertRaises(RuntimeError):s.command('amixer')
        self.assertEqual(child.communicate.call_count,1);child.terminate.assert_called_once();child.wait.assert_not_called()

class Protocol(unittest.TestCase):
    def test_request_epochs(self):
        self.assertEqual(s.parse_request(b'PREROLL 42\n'),(b'PREROLL',42))
        self.assertEqual(s.parse_request(b'CANCEL 43\n'),(b'CANCEL',43))
        self.assertEqual(s.parse_request(b'PREROLL 4294967295\n')[1],2**32-1)
    def test_bad_requests(self):
        for line in (b'PREROLL\n',b'PREROLL 0\n',b'PREROLL -1\n',b'CANCEL 01\n',b'READY 1\n',b'PREROLL 18446744073709551616\n',b'PREROLL 1\r\n'):
            with self.assertRaises(RuntimeError):s.parse_request(line)
    def test_fragmented_command(self):
        connection=Mock();connection.recv.side_effect=[bytes([n]) for n in b'PREROLL 42\n']
        self.assertEqual(s.receive_line(connection),b'PREROLL 42\n')
    def test_partial_timeout_is_rejected(self):
        connection=Mock();connection.recv.side_effect=[b'P',s.socket.timeout()]
        with self.assertRaises(RuntimeError):s.receive_line(connection)
    def test_oversized_command(self):
        connection=Mock();connection.recv.return_value=b'P'
        with self.assertRaises(RuntimeError):s.receive_line(connection)
    def test_inactive_open_handle_cannot_confirm(self):
        st=state();self.assertFalse(s.route_confirmed(st,replace(st.owner(123),active=0,opened=1),graph(0),DSP))
    def test_active_closed_handle_rejected_by_state_parser(self):
        text='main 1 0 0\n0 123 1 9 0 1 1 1 1 1 0 0 1 1\n'+'\n'.join(f'{n} 0 0 0 4294967295 0 0 0 0 0 0 0 0 0' for n in range(1,4))
        with self.assertRaises(RuntimeError):s.parse_state(text)

class RouteLoop(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.kernel=Mock();self.kernel.state.return_value=state(1)
        self.kernel.io_failed.is_set.return_value=False
        self.kernel.vendor_read.side_effect=[graph(1),DSP]
        self.service=s.Supervisor(self.kernel,Path('/unused'),'unused',Path(self.temp.name)/'STOP')
    def tearDown(self):self.temp.cleanup()
    def test_lease_revoked_while_waiting_refresh_lock(self):
        active=state(1)
        active=replace(active,leases=(replace(active.leases[0],routes=0,hd=0),*active.leases[1:]))
        revoked=replace(active,leases=(replace(active.leases[0],active=0),*active.leases[1:]))
        self.kernel.state.side_effect=[active,revoked]
        with patch.object(s,'process_start',return_value='start'):
            self.assertIsNone(self.service.route(123,'start',9))
        self.kernel.refresh.assert_not_called()
    def test_renderer_one_releases_without_control_write(self):
        with patch.object(s,'process_start',return_value='start'),patch.object(s,'verify_plugin') as verify,patch.object(s,'command',side_effect=[graph(1),DSP]):
            self.assertEqual(self.service.route(123,'start',9),1)
            self.kernel.refresh.assert_not_called();verify.assert_called_once()
    def test_cancellation_before_vendor_access(self):
        left,right=s.socket.socketpair()
        try:
            right.sendall(b'CANCEL 42\n');cursor=[41]
            self.assertIsNone(self.service.route(123,'start',9,left,cursor))
            self.assertEqual(cursor,[42,b'CANCEL']);self.kernel.state.assert_not_called();self.kernel.refresh.assert_not_called()
        finally:left.close();right.close()
    def test_new_epoch_replaces_pending_request(self):
        left,right=s.socket.socketpair()
        try:
            right.sendall(b'PREROLL 42\n');cursor=[41]
            st=state(1);self.kernel.state.return_value=replace(st,leases=(replace(st.leases[0],epoch=42,proof_epoch=42),*st.leases[1:]))
            with patch.object(s,'process_start',return_value='start'),patch.object(s,'verify_plugin'),patch.object(s,'command',side_effect=[graph(1),DSP]):
                self.assertEqual(self.service.route(123,'start',9,left,cursor),42)
        finally:left.close();right.close()
    def test_peer_eof_aborts_before_vendor_access(self):
        left,right=s.socket.socketpair();right.close()
        try:
            with self.assertRaises(EOFError):self.service.route(123,'start',9,left,[41])
            self.kernel.refresh.assert_not_called()
        finally:left.close()
    def test_stop_signal_prevents_all_device_access(self):
        self.service.stop.set()
        with self.assertRaises(RuntimeError):self.service.route(123,'start',9)
        self.kernel.state.assert_not_called();self.kernel.refresh.assert_not_called()
    def test_duplicate_pid_does_not_release_existing_owner(self):
        connection=Mock();connection.getsockopt.return_value=s.struct.pack('3i',123,0,0)
        connection.recv.side_effect=[bytes([n]) for n in b'C5HD2\n']
        self.service.pids.add(123);self.service.clients.add(connection);self.service.limit.acquire()
        with patch.object(s.socket,'SO_PEERCRED',17,create=True),patch.object(Path,'read_text',return_value='starfish-media-\n'),patch.object(s,'process_start',return_value='start'),patch.object(s,'verify_plugin'),patch('builtins.print'):
            self.service.serve(connection)
        self.assertIn(123,self.service.pids);self.kernel.grant.assert_not_called()

class ReviewRegressions(unittest.TestCase):
    def test_codec_prefix_is_not_ma(self):
        self.assertFalse(s.dsp_hd_format(DSP.replace('dts_hd_ma','dts_hd_ma_extra')))
    def test_conflicting_codec_is_rejected(self):
        self.assertFalse(s.dsp_hd_format(DSP.replace('codec=(str)dts_hd_ma','codec=(str)ac3 codec=(str)dts_hd_ma')))
    def test_duplicate_carrier_fields_are_rejected(self):
        self.assertFalse(s.dsp_hd_format(DSP.replace('channels=(int)8','channels=(int)2 channels=(int)8')))
    def test_two_active_owners_of_port_are_ambiguous(self):
        st=state();st=replace(st,leases=(st.leases[0],replace(st.leases[0],slot=1,pid=124),*st.leases[2:]))
        self.assertFalse(s.route_confirmed(st,st.owner(123),graph(0),DSP))
    def test_old_epoch_snapshot_cannot_release_new_epoch(self):
        st=state();self.assertFalse(s.route_confirmed(st,st.owner(123),graph(0),DSP,2))
    def test_resume_needs_fresh_kernel_route_observation(self):
        st=state();lease=replace(st.owner(123),epoch=2,proof_epoch=0,routes=0)
        st=replace(st,leases=(lease,*st.leases[1:]))
        self.assertFalse(s.route_confirmed(st,lease,graph(0),DSP,2))
        lease=replace(lease,proof_epoch=2,routes=1);st=replace(st,leases=(lease,*st.leases[1:]))
        self.assertTrue(s.route_confirmed(st,lease,graph(0),DSP,2))
    def test_vendor_read_failure_blocks_new_writes(self):
        kernel=s.Kernel(Path('/unused'),'unused','unused')
        with patch.object(s,'command',side_effect=RuntimeError('device command failed')) as cmd:
            with self.assertRaises(RuntimeError):kernel.vendor_read('/proc/aaudio/dot_block_connection')
            with self.assertRaises(RuntimeError):kernel.refresh()
            with self.assertRaises(RuntimeError):kernel.grant(123)
            self.assertEqual(cmd.call_count,1)

class EpochRules(unittest.TestCase):
    def test_cancel_then_preroll_same_epoch_once(self):
        cursor=[4,b'PREROLL'];s.advance_cursor(cursor,b'CANCEL',5)
        s.advance_cursor(cursor,b'PREROLL',5)
        self.assertEqual(cursor,[5,b'PREROLL'])
        with self.assertRaises(RuntimeError):s.advance_cursor(cursor,b'PREROLL',5)
    def test_old_command_cannot_replace_new_epoch(self):
        cursor=[5,b'PREROLL']
        with self.assertRaises(RuntimeError):s.advance_cursor(cursor,b'CANCEL',4)

class CommandFailureLatch(unittest.TestCase):
    def test_oserror_blocks_future_vendor_commands_and_grants(self):
        kernel=s.Kernel(Path('/unused'),'unused','unused')
        with patch.object(s,'command',side_effect=OSError('spawn failed')) as cmd:
            with self.assertRaises(OSError):kernel.vendor_read('/proc/aaudio/dot_block_connection')
            with self.assertRaises(RuntimeError):kernel.refresh()
            with self.assertRaises(RuntimeError):kernel.grant(123)
            self.assertEqual(cmd.call_count,1)
    def test_failed_insmod_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);module=root/'module.ko';module.write_bytes(b'module')
            kernel=s.Kernel(module,hashlib.sha256(module.read_bytes()).hexdigest(),'build',root/'missing')
            with patch.object(s,'command',side_effect=RuntimeError('insmod failed')) as cmd:
                with self.assertRaises(RuntimeError):kernel.grant(123)
                with self.assertRaises(RuntimeError):kernel.grant(123)
                self.assertEqual(cmd.call_count,1)
if __name__=='__main__':unittest.main()
