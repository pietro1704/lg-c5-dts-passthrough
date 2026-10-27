"""Complete only the already-bound, muted HD PoC with documented DSP HBR mode."""
from pathlib import Path
import json,os,re,struct,time
from hd_controller import command,properties
from luna_rpc import luna

ROOT=Path('/sys/module/c5_hdmi_ma_route/parameters')
INPUT=Path('/proc/aaudio/processors/dsp0/hdmi1/021c0001/prop')

def main():
    assert os.geteuid()==0 and os.uname().release=='5.4.268-329.ptl4tv.5'
    mem=os.open('/dev/mem',os.O_RDONLY)
    pointer=int((ROOT/'input_cookie').read_text());owner=(ROOT/'owner_pid').read_text().strip()
    start=Path('/proc/'+owner+'/stat').read_text().rsplit(')',1)[1].split()[19]
    def scope():
        assert Path('/proc/'+owner+'/exe').resolve().name=='extinput-integration'
        assert Path('/proc/'+owner+'/stat').read_text().rsplit(')',1)[1].split()[19]==start
        assert luna('luna://com.webos.applicationManager/getForegroundAppInfo',{}).get('appId')=='com.webos.app.hdmi3'
        assert (ROOT/'invalidated').read_text().strip()=='N'
        assert struct.unpack('<Q',os.pread(mem,8,0x262a228))[0]==pointer
        raw=Path('/proc/lgtv-driver/audio/hdmi').read_text()
        assert 'Codec=DTS_HD\n' in raw and 'SamplingRate=192000\n' in raw
        raw=Path('/proc/lgtv-driver/audio/adec.p1').read_text()
        assert 'UserCodec=DTS_HD\n' in raw and 'Start=1\n' in raw
        rows=Path('/sys/module/c5_es_poc/parameters/session_state').read_text().splitlines()
        # The last header field records prior native audio use; it is not
        # source selection. Require ADEC1 and no active native lease.
        assert rows[0].split()[:3]==['main','1','1'] and all(x.split()[2]=='0' and x.split()[11]=='0' for x in rows[1:])
    def write(path,key,value):
        scope()
        command(['/usr/bin/python3','-c',
            'import os,sys;f=os.open(sys.argv[1],os.O_WRONLY);d=(sys.argv[2]+"\\n").encode();assert os.write(f,d)==len(d);os.close(f)',str(path),key+' '+str(value)])
    def transport():
        raw=os.pread(mem,0x70,0x22f8a58)
        return (struct.unpack('<I',os.pread(mem,4,0x262a9bc))[0],struct.unpack_from('<I',raw)[0],struct.unpack_from('<I',raw,0x68)[0])
    try:
        scope();graph=command(['/bin/cat','/proc/aaudio/dot_module_connection'])
        ids=set(re.findall(r'gain_([0-9a-f]{8})_\w+_out0\s*->\s*bypass_[0-9a-f]{8}_\w+_in0',graph));assert len(ids)==1
        gain=Path('/proc/aaudio/processors/dsp2/gain')/ids.pop()/'prop'
        write(gain,'mute-input','true');write(gain,'mute-output','true')
        for key,value in [('drop',1),('start',0),('codec',6),('mode',4),('sampling-freq',192000),('channel-num',8),
            ('data-drop-mode',1),('drop-output-data','false'),('start',1),('drop',0)]:write(INPUT,key,value)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            scope()
            if transport()==(1,8,192000) and int((ROOT/'status_hits').read_text())>0:break
            time.sleep(.1)
        else:
            print(json.dumps({'phase':'UNCONFIRMED_STILL_MUTED','transport':transport(),'status_hits':(ROOT/'status_hits').read_text().strip()}),flush=True)
            raise RuntimeError('HBR transport not proven')
        write(gain,'mute-input','false');write(gain,'mute-output','false')
        print(json.dumps({'phase':'HBR_CONFIRMED_UNMUTED','transport':transport(),'receiver_confirmation_pending':True}),flush=True)
    finally:os.close(mem)

if __name__=='__main__':main()
