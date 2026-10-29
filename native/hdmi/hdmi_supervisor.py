"""Exact-C5 HDMI core controller. Requires explicit target activation."""
from pathlib import Path
from dataclasses import dataclass
import argparse,hashlib,json,os,re,struct,subprocess,time
from alsa_controls import AlsaControls
from luna_rpc import luna
from runtime_settings import setup_model,receiver_sad,apply_edid

KERNEL='5.4.268-329.ptl4tv.5'
NATIVE_BUILD='7059bc5a81bebb973b591a4190cba308fa8c26a5a401a512d8e98213b80748c6'
NATIVE=Path('/sys/module/c5_es_poc/parameters')
GATE=Path('/sys/module/c5_hdmi_broker_gate/parameters')
ROUTE=Path('/sys/module/c5_hdmi_auto_route/parameters')


class ScopeChanged(RuntimeError):pass
class UncertainOperation(RuntimeError):pass


def command(argv,timeout=2):
    p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        env=dict(os.environ,PATH='/usr/sbin:/usr/bin:/sbin:/bin'))
    try:out,err=p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.terminate();raise UncertainOperation('vendor operation deadline; no further writes')
    if p.returncode:raise RuntimeError('device operation failed: '+err[:160])
    return out


def properties(text):
    return {m[1]:m[2] for line in text.splitlines()
            if (m:=re.fullmatch(r'([^=]+)=\([^)]*\)(.*)',line))}


def connected(graph):
    return all(re.search(r'\b'+a+r'\s*->\s*'+b+r'\b',graph)
        for a,b in [('renderer_1_out100','mixer_0_in100'),('mixer_0_out100','output_3_in0')])


@dataclass(frozen=True)
class Snapshot:
    foreground:bool
    native_idle:bool
    main:tuple
    physical_dts:bool
    source_pointer:int
    source_type:int
    source_index:int
    adec_open:bool
    adec_dts:bool
    adec_started:bool
    input_pointer:int
    generation:int
    invalidated:bool
    transport:tuple
    flowing:bool
    route_connected:bool=False
    gain_unmuted:bool=False
    sink_supported:bool=True

    @property
    def selected(self):
        return self.foreground and self.native_idle and self.main==(1,1) and self.sink_supported

    @property
    def source_ready(self):
        return (self.selected and self.physical_dts and self.adec_open and
            self.source_pointer!=0 and (self.source_type,self.source_index)==(2,1))

    @property
    def bound(self):
        return self.input_pointer==self.source_pointer and not self.invalidated

    @property
    def ready(self):
        return (self.source_ready and self.bound and self.adec_dts and self.adec_started and
            self.transport==(1,2,48000) and self.flowing and self.route_connected and self.gain_unmuted)


class Controller:
    def __init__(self,backend):
        self.backend=backend;self.failures=0;self.active=False;self.halted=False

    def tick(self):
        if self.halted:return 'halted'
        try:
            state=self.backend.snapshot()
            self.backend.gate(30 if state.selected else 0)
        except Exception:
            self.halted=True;raise
        if not state.source_ready:
            self.disarm()
            try:self.backend.release(state)
            except ScopeChanged:self.active=False;return 'source changed'
            except Exception:self.halted=True;raise
            self.active=False;self.failures=0
            return 'waiting'
        if state.ready:
            self.active=True;self.failures=0;return 'active'
        try:
            self.backend.recover(state)
        except ScopeChanged:
            self.disarm();self.active=False;return 'source changed'
        except UncertainOperation:
            self.halted=True;raise
        except (OSError,RuntimeError):
            self.active=False;self.failures+=1
            if self.failures>=3:self.halted=True
            raise
        self.active=True;self.failures=0
        return 'recovered'

    def disarm(self):
        try:self.backend.route(0)
        except Exception:self.halted=True;raise


class Target:
    def __init__(self,bundle,manifest,run):
        assert os.geteuid()==0 and os.uname().release==KERNEL
        self.bundle=Path(bundle);self.manifest=manifest;self.run=Path(run);self.alsa=AlsaControls()
        self.mem=os.open('/dev/mem',os.O_RDONLY);self.foreground_until=0;self.gain=None;self.owned=None
        self.settings_until=0;self.sink_supported=False
        candidates=[]
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():continue
            try:
                if (path/'exe').resolve().name=='extinput-integration':candidates.append(int(path.name))
            except OSError:pass
        if len(candidates)!=1:raise RuntimeError('ambiguous HDMI service identity')
        self.owner=candidates[0];self.start=self.process_start(self.owner)
        if (NATIVE/'session_build').read_text().strip()!=NATIVE_BUILD:raise RuntimeError('different native bridge')
        if (NATIVE/'session_protocol').read_text().strip()!='3' or (NATIVE/'lease_api_only').read_text().strip() not in ('N','0'):
            raise RuntimeError('native bridge is not the production lease protocol')
        self.load('c5_hdmi_broker_gate','broker-gate.ko')
        self.load('c5_hdmi_auto_route','auto-route.ko')
        setup_model(self.run)

    @staticmethod
    def process_start(pid):return Path('/proc/%s/stat'%pid).read_text().rsplit(')',1)[1].split()[19]

    def load(self,name,filename):
        binary=self.bundle/filename
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=self.manifest['files'][filename]:raise RuntimeError('module artifact changed')
        root=Path('/sys/module')/name
        if root.exists():
            if (root/'parameters/control_pid').read_text().strip()!=str(os.getpid()):
                raise RuntimeError('another broker owns the resident driver; reboot required')
        else:command(['/sbin/insmod',str(binary),'owner_pid='+str(self.owner),'control_pid='+str(os.getpid())],timeout=4)
        if (root/'parameters/control_pid').read_text().strip()!=str(os.getpid()):raise RuntimeError('broker identity mismatch')

    def read_u32(self,address):return struct.unpack('<I',os.pread(self.mem,4,address))[0]

    def snapshot(self):
        if self.process_start(self.owner)!=self.start:raise RuntimeError('HDMI service identity changed; stop mutations')
        now=time.monotonic()
        if now>=self.foreground_until:
            record=luna('luna://com.webos.applicationManager/getForegroundAppInfo',{})
            self.is_foreground=record.get('appId')=='com.webos.app.hdmi3';self.foreground_until=now+.2
        native=(NATIVE/'session_state').read_text().splitlines()
        head=native[0].split();main=(int(head[1]),int(head[2]))
        idle=all(l.split()[2]=='0' and l.split()[11]=='0' for l in native[1:])
        physical=Path('/proc/lgtv-driver/audio/hdmi').read_text()
        adec=Path('/proc/lgtv-driver/audio/adec.p1').read_text()
        if self.is_foreground and idle and main==(1,1) and now>=self.settings_until:
            self.sink_supported=receiver_sad() is not None
            if self.sink_supported:apply_edid(self.run,notify_source=True)
            self.settings_until=now+5
        pointer=struct.unpack('<Q',os.pread(self.mem,8,0x262a228))[0];kind=index=-1
        if 0xffffff8000000000<=pointer<0xffffffc000000000:
            kind,index=struct.unpack('<2I',os.pread(self.mem,8,pointer-0xffffff8000000000+0x2c))
        hal=(self.read_u32(0x262a9bc),self.read_u32(0x22f8a58),self.read_u32(0x22f8ac0))
        paths=list(Path('/proc/aaudio/processors/dsp0/hdmi1').glob('*/prop'))
        self.input=paths[0] if len(paths)==1 else None
        eligible=(self.is_foreground and idle and self.sink_supported and main==(1,1) and 'Codec=DTS\n' in physical)
        props=properties(command(['/bin/cat',str(self.input)])) if self.input and eligible else {}
        flowing=all(props.get(k)==v for k,v in [('codec','5'),('mode','1'),('sampling-freq','48000'),('channel-num','2'),('start','1'),('drop','false')])
        route_ready=connected(command(['/bin/cat','/proc/aaudio/dot_block_connection'])) if eligible else False
        gain_ready=False
        if not eligible:self.gain=None
        if eligible and self.gain and self.gain.is_file():
            gain_props=properties(command(['/bin/cat',str(self.gain)]))
            gain_ready=gain_props.get('mute-input')=='false' and gain_props.get('mute-output')=='false'
        return Snapshot(self.is_foreground,idle,main,'Codec=DTS\n' in physical and 'SamplingRate=48000\n' in physical,
            pointer,kind,index,'Open=1\n' in adec and 'Connect=HDMI_PORT1\n' in adec,
            'UserCodec=DTS\n' in adec,'Start=1\n' in adec,int((ROUTE/'input_cookie').read_text()),
            int((ROUTE/'input_generation').read_text()),(ROUTE/'invalidated').read_text().strip() not in ('N','0'),hal,flowing,route_ready,gain_ready,self.sink_supported)

    def gate(self,seconds):(GATE/'arm_seconds').write_text(str(seconds))
    def route(self,seconds):(ROUTE/'arm_seconds').write_text(str(seconds))

    def scope(self,pointer=None,generation=None):
        state=self.snapshot()
        if not state.source_ready or (pointer is not None and state.source_pointer!=pointer) or (generation is not None and state.generation!=generation):
            raise ScopeChanged('selected source identity changed')
        return state

    def property_write(self,path,key,value,pointer,generation):
        self.scope(pointer,generation)
        command(['/usr/bin/python3','-c',
            'import os,sys;f=os.open(sys.argv[1],os.O_WRONLY);d=(sys.argv[2]+"\\n").encode();assert os.write(f,d)==len(d);os.close(f)',
            str(path),key+' '+str(value)])

    def recover(self,original):
        pointer,generation=original.source_pointer,original.generation
        state=self.scope(pointer,generation)
        if not state.adec_dts:
            self.alsa.values(20,4,[1,48,0,0]);state=self.scope(pointer,generation)
        if not state.adec_started:
            self.alsa.values(21,4,[1,0,0,0]);state=self.scope(pointer,generation)
        if not state.bound:
            (ROUTE/'bind_input').write_text('%d %x'%(state.generation,state.source_pointer))
            # Only our successful bind may advance this recovery's identity.
            state=self.scope(pointer,generation+1)
            if not state.bound:raise ScopeChanged('input bind changed')
        pointer,generation=state.source_pointer,state.generation
        self.route(120);self.scope(pointer,generation)
        values=self.alsa.values(92,5)
        if values[:2]!=[2,1]:raise ScopeChanged('eARC control selection changed')
        self.alsa.values(92,5,values);self.scope(pointer,generation)
        graph=command(['/bin/cat','/proc/aaudio/dot_block_connection'])
        if not connected(graph):raise RuntimeError('compressed route was not connected')
        graph=command(['/bin/cat','/proc/aaudio/dot_module_connection'])
        ids=set(re.findall(r'gain_([0-9a-f]{8})_\w+_out0\s*->\s*bypass_[0-9a-f]{8}_\w+_in0',graph))
        if len(ids)!=1:raise RuntimeError('ambiguous compressed gain')
        gain=Path('/proc/aaudio/processors/dsp2/gain')/ids.pop()/'prop'
        if not gain.is_file():raise RuntimeError('compressed gain vanished')
        def write(path,key,value):self.property_write(path,key,value,pointer,generation)
        write(gain,'mute-input','true');write(gain,'mute-output','true')
        for key,value in [('drop',1),('start',0),('codec',5),('mode',1),('sampling-freq',48000),('channel-num',2),
            ('data-drop-mode',1),('drop-output-data','false'),('start',1),('drop',0)]:write(self.input,key,value)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            state=self.scope(pointer,generation)
            if state.transport==(1,2,48000) and int((ROUTE/'status_hits').read_text())>0:break
            time.sleep(.1)
        else:raise RuntimeError('core non-PCM transport unproven; remain muted')
        write(gain,'mute-input','false');write(gain,'mute-output','false')
        self.gain=gain;self.owned=(pointer,generation)

    def release(self,state):
        if self.owned is None:return
        if (state.source_pointer,state.generation)!=self.owned or state.invalidated:
            self.owned=None;return
        # Do not replay controls over a native app or a source still changing.
        # Firmware's cached PCM codec can outlive our explicit DSP override.
        # Replaying its CURRENT non-DTS codec through ALSA restores its own
        # channel/rate policy rather than inventing a PCM/Dolby DSP format.
        if not state.selected or state.physical_dts or not state.adec_open:return
        text=Path('/proc/lgtv-driver/audio/hdmi').read_text()
        if 'AudioData=1\n' not in text or 'Codec=DTS\n' in text:return
        props=properties(command(['/bin/cat',str(self.input)]))
        if props.get('codec')!='5':self.owned=None;return
        values=self.alsa.values(20,4)
        if values[2]!=1 or not 1<=values[3]<48:return
        fresh=self.snapshot()
        if (fresh.source_pointer,fresh.generation)!=self.owned or not fresh.selected or fresh.physical_dts:
            raise ScopeChanged('source changed before vendor-format restoration')
        self.alsa.values(22,4,[1,0,0,0])
        values2=self.alsa.values(20,4)
        if values2!=values:raise ScopeChanged('vendor codec changed during restoration')
        self.alsa.values(20,4,[1,values[3],0,0])
        self.alsa.values(21,4,[1,0,0,0]);self.owned=None;self.gain=None

    def close(self):self.alsa.close();os.close(self.mem)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    if not args.activate:raise SystemExit('Explicit --activate is required; no device access performed')
    manifest=json.loads((args.bundle/'hdmi-manifest.json').read_text())
    if args.run!=Path('/run/c5-dts-global/hdmi'):raise RuntimeError('unexpected runtime directory')
    for directory in [args.run.parent,args.run]:
        if directory.is_symlink():raise RuntimeError('symlink runtime directory')
        directory.mkdir(mode=0o700,exist_ok=True)
        info=directory.stat()
        if info.st_uid!=0 or info.st_mode&0o077:raise RuntimeError('unsafe runtime directory')
    ready=args.run/'hdmi-ready.json'
    if ready.exists() or ready.is_symlink():raise RuntimeError('existing HDMI activation; no replacement')
    target=Target(args.bundle,manifest,args.run);controller=Controller(target)
    temporary=ready.with_suffix('.next')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump({'pid':os.getpid(),'schema':1},stream);stream.flush();os.fsync(stream.fileno())
    os.link(temporary,ready);temporary.unlink()
    previous=None
    try:
        while True:
            try:result=controller.tick()
            except (OSError,RuntimeError) as error:
                if controller.halted:raise
                print(json.dumps({'hdmi':'recovery pending','error':str(error),'time':time.time()}),flush=True)
                time.sleep(2);continue
            if result!=previous:print(json.dumps({'hdmi':result,'time':time.time()}),flush=True);previous=result
            if controller.halted:raise RuntimeError('controller latched after failures')
            time.sleep(.5)
    finally:target.close()


if __name__=='__main__':main()
