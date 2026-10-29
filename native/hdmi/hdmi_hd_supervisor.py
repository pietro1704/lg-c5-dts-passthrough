# SPDX-License-Identifier: GPL-2.0-only
"""Boot controller for the separately fingerprinted, accepted HDMI-HD bridge."""
import argparse,hashlib,json,os,time
from pathlib import Path
from hd_controller import Target,Controller,KERNEL
from luna_rpc import luna

FILES={'hdmi_hd_supervisor.py','hd_controller.py','alsa_controls.py','luna_rpc.py',
       'runtime_settings.py','edid.py','broker-gate.ko','auto-route.ko'}
RUN=Path('/run/c5-dts-global/hdmi-hd')


def supports_hd(response):
    records=[x.get('value',{}) for x in response.get('settings',[]) if x.get('category')=='external_arc']
    if len(records)!=1 or records[0].get('arcActivation')!='eARC':return False
    for item in records[0].get('advancedEarcCapability',[]):
        if item.get('audioCodingType')!='DTSHD_MA':continue
        try:sad=bytes.fromhex(item['hexSAD'])
        except (KeyError,TypeError,ValueError):continue
        if len(sad)==3 and sad[0]>>3==11 and sad[0]&7==7 and sad[1]&64:return True
    return False


def verify(bundle):
    bundle=Path(bundle)
    if bundle.is_symlink():raise RuntimeError('symlink HDMI-HD bundle')
    manifest=json.loads((bundle/'hdmi-manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('kernel')!=KERNEL or set(manifest.get('files',{}))!=FILES:
        raise RuntimeError('unexpected HDMI-HD manifest')
    for name,digest in manifest['files'].items():
        path=bundle/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('HDMI-HD artifact changed: '+name)
    return manifest


def receiver_check():
    return supports_hd(luna('luna://com.webos.service.audiooutput/quality/getSettings',{'category':'external_arc'}))


def run(controller,emit,sleep):
    previous=None
    while True:
        try:result=controller.tick()
        except (OSError,RuntimeError) as error:
            if controller.halted:raise
            emit({'hdmi_hd':'recovery pending','error':str(error),'time':time.time()})
            sleep(2);continue
        if result!=previous:emit({'hdmi_hd':result,'time':time.time()});previous=result
        if controller.halted:raise RuntimeError('HDMI-HD controller latched')
        sleep(.5)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args(argv)
    if not args.activate:raise SystemExit('Explicit --activate required; no device access performed')
    if args.run!=RUN or os.geteuid()!=0 or os.uname().release!=KERNEL:
        raise RuntimeError('unexpected HDMI-HD target or runtime directory')
    manifest=verify(args.bundle)
    for path in (args.run.parent,args.run):
        if path.is_symlink():raise RuntimeError('symlink runtime directory')
        path.mkdir(mode=0o700,exist_ok=True)
        stat=path.stat()
        if stat.st_uid!=0 or stat.st_mode&0o077:raise RuntimeError('unsafe runtime directory')
    ready=args.run/'hdmi-ready.json'
    if ready.exists() or ready.is_symlink():raise RuntimeError('existing HDMI-HD activation; do not stack')
    target=Target(args.bundle,manifest,args.run,sink_check=receiver_check)
    try:
        temporary=ready.with_suffix('.next')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump({'pid':os.getpid(),'schema':1},stream);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,ready);temporary.unlink()
        run(Controller(target),lambda event:print(json.dumps(event),flush=True),time.sleep)
    finally:target.close()


if __name__=='__main__':main()
