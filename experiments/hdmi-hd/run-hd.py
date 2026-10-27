"""Explicit bounded HDMI-HD PoC. No boot changes or controller replacement."""
from pathlib import Path
import hashlib,json,os,time
from hd_controller import Target,Controller,command
from luna_rpc import luna

BASE=Path(__file__).resolve().parent

def main():
    assert os.geteuid()==0 and os.uname().release=='5.4.268-329.ptl4tv.5'
    capabilities=luna('luna://com.webos.service.audiooutput/quality/getSettings',{'category':'external_arc'})
    assert any(s.get('category')=='external_arc' and s.get('value',{}).get('arcActivation')=='eARC' and
        any(c.get('audioCodingType')=='DTSHD_MA' for c in s['value'].get('advancedEarcCapability',[])) for s in capabilities.get('settings',[]))
    assert luna('luna://com.webos.applicationManager/getForegroundAppInfo',{}).get('appId')=='com.webos.app.hdmi3'
    physical=Path('/proc/lgtv-driver/audio/hdmi').read_text()
    assert 'Codec=DTS_HD\n' in physical and 'SamplingRate=192000\n' in physical
    manifest=json.loads((BASE/'hdmi-manifest.json').read_text())
    for name,digest in manifest['files'].items():assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==digest,name
    run=BASE/'run';run.mkdir(mode=0o700)
    target=Target(BASE,manifest,run);controller=Controller(target)
    print(json.dumps({'phase':'HD_POC_READY','pid':os.getpid(),'input':'DTS_HD/192000'}),flush=True)
    previous=None;deadline=time.monotonic()+600
    try:
        while time.monotonic()<deadline:
            result=controller.tick()
            if result!=previous:
                print(json.dumps({'phase':result,'time':time.time()}),flush=True);previous=result
                if result=='active':
                    print(json.dumps({'phase':'HBR_TRANSPORT_CONFIRMED','transport':[1,8,192000],
                        'status_hits':Path('/sys/module/c5_hdmi_ma_route/parameters/status_hits').read_text().strip()}),flush=True)
            time.sleep(1)
        print(json.dumps({'phase':'BOUNDED_POC_FINISHED'}),flush=True)
    except BaseException as error:
        print(json.dumps({'phase':'STOP_NEW_WRITES','reason':str(error),'audio_verified':False}),flush=True)
        raise
    finally:target.close()

if __name__=='__main__':main()
