"""TARGET-ONLY isolated clock bench. Appsrc/appsink; no audio device or BRK hooks.

Prepare privately owned fixtures first. This script is intentionally NOT run by
host checks. A nonzero result is meaningful: originals were lost by the strict
GstBaseSink clock model. It does not claim the vendor uses that lateness limit.
"""
import argparse,ctypes as C,os,platform,socket,struct,subprocess,threading,time,json,hashlib
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--module',type=Path,required=True)
    parser.add_argument('--module-sha256',required=True)
    parser.add_argument('--plugin',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--canonical',type=Path,required=True)
    parser.add_argument('--ready-delay',type=float,default=.4)
    parser.add_argument('--model-latency',type=float,default=0)
    parser.add_argument('--untimed-preparation',action='store_true')
    parser.add_argument('--fragment-size',type=int,default=0)
    parser.add_argument('--expect-timeout',action='store_true')
    options=parser.parse_args()
    if os.geteuid()!=0 or platform.system()!='Linux' or platform.release()!='5.4.268-329.ptl4tv.5' or C.sizeof(C.c_void_p)!=4:
        raise SystemExit('requires exact target, ARM32 Python, root; no host execution')
    if Path('/sys/module/c5_es_poc').exists():raise SystemExit('a driver is already loaded; do not unload a resident driver for this test')
    endpoint=Path('/run/c5-dts/control.sock');endpoint.parent.mkdir(mode=0o700,exist_ok=True)
    if endpoint.exists():raise SystemExit('another supervisor exists')
    module_data=options.module.read_bytes()
    if hashlib.sha256(module_data).hexdigest()!=options.module_sha256 or b'parmtype=lease_api_only:bool' not in module_data:
        raise SystemExit('module identity/API-only mode not verified; no load attempted')
    registry=options.fixture.parent/'clock-registry.bin'
    os.environ['GST_PLUGIN_PATH']=str(options.plugin.parent)
    os.environ['GST_REGISTRY']=str(registry)
    fixture=options.fixture.read_bytes();canonical=options.canonical.read_bytes()
    gst=C.CDLL('libgstreamer-1.0.so.0');app=C.CDLL('libgstapp-1.0.so.0');obj=C.CDLL('libgobject-2.0.so.0')
    ptr=C.c_void_p;u64=C.c_uint64
    def api(lib,name,result,*arguments):
        fn=getattr(lib,name);fn.restype=result;fn.argtypes=list(arguments);return fn
    init=api(gst,'gst_init',None,ptr,ptr);init(None,None)
    parse=api(gst,'gst_parse_launch',ptr,C.c_char_p,ptr)
    find=api(gst,'gst_bin_get_by_name',ptr,ptr,C.c_char_p)
    state=api(gst,'gst_element_set_state',C.c_int,ptr,C.c_int)
    bus=api(gst,'gst_element_get_bus',ptr,ptr)
    pop=api(gst,'gst_bus_timed_pop_filtered',ptr,ptr,u64,C.c_uint)
    buffer_new=api(gst,'gst_buffer_new_allocate',ptr,ptr,C.c_size_t,ptr)
    fill=api(gst,'gst_buffer_fill',C.c_size_t,ptr,C.c_size_t,ptr,C.c_size_t)
    push=api(app,'gst_app_src_push_buffer',C.c_int,ptr,ptr)
    eos=api(app,'gst_app_src_end_of_stream',C.c_int,ptr)
    pull=api(app,'gst_app_sink_try_pull_sample',ptr,ptr,u64)
    sample_buffer=api(gst,'gst_sample_get_buffer',ptr,ptr)
    size=api(gst,'gst_buffer_get_size',C.c_size_t,ptr)
    extract=api(gst,'gst_buffer_extract',C.c_size_t,ptr,C.c_size_t,ptr,C.c_size_t)
    unref=api(gst,'gst_object_unref',None,ptr)
    mini_unref=api(gst,'gst_mini_object_unref',None,ptr)
    pipeline_latency=api(gst,'gst_pipeline_set_latency',None,ptr,u64)
    recalculate_latency=api(gst,'gst_bin_recalculate_latency',C.c_int,ptr)
    get_pad=api(gst,'gst_element_get_static_pad',ptr,ptr,C.c_char_p)
    probe_buffer=api(gst,'gst_pad_probe_info_get_buffer',ptr,ptr)
    probe_callback=C.CFUNCTYPE(C.c_int,ptr,ptr,ptr)
    add_probe=api(gst,'gst_pad_add_probe',C.c_ulong,ptr,C.c_uint,probe_callback,ptr,ptr)
    sample_callback=C.CFUNCTYPE(C.c_int,ptr,ptr)
    connect_signal=api(obj,'g_signal_connect_data',C.c_ulong,ptr,C.c_char_p,sample_callback,ptr,ptr,C.c_uint)
    arrivals=[];reference_arrivals=[]
    @sample_callback
    def audio_sample(element,user):
        arrivals.append(time.monotonic());return 0
    @sample_callback
    def reference_sample(element,user):
        reference_arrivals.append(time.monotonic());return 0
    @probe_callback
    def adjust_preparation(pad,info,user):
        buffer=probe_buffer(info)
        if buffer and options.untimed_preparation:
            length=size(buffer);payload=C.create_string_buffer(length)
            extract(buffer,0,payload,length)
            if not any(payload.raw):
                C.c_uint64.from_address(buffer+40).value=(1<<64)-1
                C.c_uint64.from_address(buffer+48).value=(1<<64)-1
                C.c_uint64.from_address(buffer+56).value=0
        return 1
    server_ready=threading.Event();stopped=threading.Event();errors=[]
    def receive_line(connection):
        line=bytearray()
        while len(line)<48:
            byte=connection.recv(1)
            if not byte:return None
            line+=byte
            if byte==b'\n':return bytes(line)
        raise AssertionError('oversized test protocol')
    def server():
        try:
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(endpoint));listener.listen(1);listener.settimeout(5);server_ready.set()
                with listener.accept()[0] as peer:
                    pid,uid,_=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    assert pid==os.getpid() and uid==0,'only this appsink process may use the mock'
                    peer.settimeout(5);assert receive_line(peer)==b'C5HD2\n'
                    Path('/sys/module/c5_es_poc/parameters/grant_pid').write_text(str(pid)+'\n')
                    peer.sendall(b'ARMED\n')
                    while not stopped.is_set():
                        line=receive_line(peer)
                        if line is None:return
                        if line.startswith(b'CANCEL '):continue
                        assert line.startswith(b'PREROLL ')
                        epoch=int(line.split()[1]);assert epoch>0
                        if stopped.wait(options.ready_delay):return
                        peer.sendall(f'READY {epoch}\n'.encode())
        except BaseException as error:errors.append(type(error).__name__+': '+str(error))
        finally:
            if endpoint.exists():endpoint.unlink()
    pipeline=source=sink=pipeline_bus=reference_source=reference_sink=None;thread=None;loaded=False;api_mode_confirmed=False
    try:
        subprocess.run(['insmod',str(options.module),'selftest_only=0','lease_api_only=1'],check=True)
        loaded=True
        assert Path('/sys/module/c5_es_poc/parameters/lease_api_only').read_text().strip()=='Y'
        api_mode_confirmed=True
        thread=threading.Thread(target=server,daemon=True);thread.start();assert server_ready.wait(2)
        pipeline=parse(b'appsrc name=input format=time caps=audio/x-dts ! dtspassthrough ! appsink name=output emit-signals=true sync=true max-lateness=20000000 qos=true wait-on-eos=false appsrc name=reference format=time caps=application/x-clock-reference ! appsink name=reference-output emit-signals=true sync=true max-lateness=20000000 wait-on-eos=false',None)
        assert pipeline
        source=find(pipeline,b'input');sink=find(pipeline,b'output');pipeline_bus=bus(pipeline)
        reference_source=find(pipeline,b'reference');reference_sink=find(pipeline,b'reference-output')
        connect_signal(sink,b'new-sample',audio_sample,None,None,0)
        connect_signal(reference_sink,b'new-sample',reference_sample,None,None,0)
        if options.model_latency:pipeline_latency(pipeline,int(options.model_latency*1_000_000_000))
        sink_pad=get_pad(sink,b'sink');add_probe(sink_pad,16,adjust_preparation,None,None);unref(sink_pad)
        started=time.monotonic()
        assert state(pipeline,4)!=0
        reference_buffer=buffer_new(None,4,None)
        assert fill(reference_buffer,0,C.create_string_buffer(b'ref!'),4)==4
        C.c_uint64.from_address(reference_buffer+40).value=512_000_000
        C.c_uint64.from_address(reference_buffer+56).value=10_666_666
        assert push(reference_source,reference_buffer)==0
        assert eos(reference_source)==0
        # Use canonical full frames and valid advancing PTS. Fragmented input
        # can lose the first parser timestamp and silently disable clock sync.
        frames=[];position=0
        while position<len(canonical):
            assert canonical[position:position+4]==bytes.fromhex('72f81f4e')
            pc=int.from_bytes(canonical[position+4:position+6],'little')
            assert pc&31==17
            period=(512,1024,2048,4096,8192,16384)[(pc>>8)&7]
            header=bytearray(canonical[position+8:position+20])
            header[::2],header[1::2]=header[1::2],header[::2]
            length=int.from_bytes(header[10:12],'big')
            payload=bytearray(canonical[position+20:position+20+((length+1)&~1)])
            payload[::2],payload[1::2]=payload[1::2],payload[::2]
            frames.append(bytes(payload[:length]));position+=period*4
        assert b''.join(frames)==fixture
        for number,data in enumerate(frames):
            step=options.fragment_size or len(data)
            assert step>0
            for offset in range(0,len(data),step):
                chunk=data[offset:offset+step];buffer=buffer_new(None,len(chunk),None)
                payload=C.create_string_buffer(chunk);assert fill(buffer,0,payload,len(chunk))==len(chunk)
                if offset==0:C.c_uint64.from_address(buffer+40).value=number*512*1_000_000_000//48000
                assert push(source,buffer)==0
        assert eos(source)==0
        deadline=time.monotonic()+6
        pipeline_error=False
        while True:
            message=pop(pipeline_bus,1_000_000_000,3|(1<<19))
            assert message or time.monotonic()<deadline,'EOS/error deadline'
            if not message:continue
            kind=C.c_uint.from_address(message+36).value
            mini_unref(message)
            if kind==1:break
            if kind==2:pipeline_error=True;break
            assert recalculate_latency(pipeline),'latency negotiation failed'
        elapsed=time.monotonic()-started
        actual=bytearray();silence=0;samples=0;timestamps=[];durations=[];original_timestamps=[]
        while True:
            sample=pull(sink,0)
            if not sample:break
            buffer=sample_buffer(sample);length=size(buffer);payload=C.create_string_buffer(length)
            extract(buffer,0,payload,length);data=payload.raw;samples+=1
            timestamps.append(C.c_uint64.from_address(buffer+40).value)
            durations.append(C.c_uint64.from_address(buffer+56).value)
            if any(data):
                actual.extend(data);original_timestamps.append(timestamps[-1])
            else:silence+=1
            mini_unref(sample)
        canonicalized=bytearray(actual)
        canonicalized[::2],canonicalized[1::2]=actual[1::2],actual[::2]
        reference_delta=None
        if reference_arrivals:
            candidates=[i for i,t in enumerate(timestamps) if t==512_000_000 and durations[i]>0]
            if candidates and candidates[-1]<len(arrivals):reference_delta=abs(arrivals[candidates[-1]]-reference_arrivals[0])
        result={'test':'strict synchronized appsink','audio_device_opened':False,'kernel_audio_hooks':False,
                'plugin_sha256':hashlib.sha256(options.plugin.read_bytes()).hexdigest(),
                'module_sha256':options.module_sha256,'expected_timeout':options.expect_timeout,
                'ready_delay_seconds':options.ready_delay,'model_latency_seconds':options.model_latency,
                'pipeline_error':pipeline_error,'fragment_size':options.fragment_size,
                'untimed_preparation':options.untimed_preparation,'samples':samples,'silent_buffers':silence,
                'elapsed_seconds':elapsed,'pts_ns':timestamps,'durations_ns':durations,
                'captured_original_bytes':len(actual),'required_original_bytes':len(canonical),
                'byte_identity_preserved':canonicalized==canonical,
                'timestamps_valid':bool(original_timestamps) and all(t!=(1<<64)-1 for t in original_timestamps),
                'timestamps_monotonic':all(b>a for a,b in zip(original_timestamps,original_timestamps[1:])),
                'original_pts_unchanged':original_timestamps==[n*512*1_000_000_000//48000 for n in range(len(frames))],
                'reference_clock_delta_seconds':reference_delta,
                'mock_errors':errors}
        print(json.dumps(result,indent=2))
        if options.expect_timeout:
            if not pipeline_error or actual:raise SystemExit(1)
        elif pipeline_error or canonicalized!=canonical or errors or not result['timestamps_valid'] or not result['timestamps_monotonic'] or not result['original_pts_unchanged'] or reference_delta is None or reference_delta>.05:raise SystemExit(1)
    finally:
        if pipeline:state(pipeline,1)
        for value in (source,sink,reference_source,reference_sink,pipeline_bus,pipeline):
            if value:unref(value)
        stopped.set()
        if thread:thread.join(6)
        if loaded and api_mode_confirmed:
            # API-only never registered any audio probe or BRK bridge. It is
            # unloadable after closing these file leases, unlike real use.
            subprocess.run(['rmmod','c5_es_poc'],check=True)
if __name__=='__main__':main()
