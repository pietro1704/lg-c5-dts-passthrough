# SPDX-License-Identifier: GPL-2.0-only
"""Stage owned artifacts and an explicit Homebrew boot hook; never reboot."""
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,platform,re,shutil,stat,subprocess,tempfile,time

PREFIX=Path('/home/root/c5-dts-global')
HOOK=Path('/var/lib/webosbrew/init.d/zz-c5-dts-global')
RUN=Path('/run/c5-dts-global')
NATIVE_RUN=Path('/run/c5-dts')
KERNEL='5.4.268-329.ptl4tv.5'
NATIVE_BUILD='7059bc5a81bebb973b591a4190cba308fa8c26a5a401a512d8e98213b80748c6'
HD_FILES={'hdmi_hd_supervisor.py','hd_controller.py','alsa_controls.py','luna_rpc.py',
    'runtime_settings.py','edid.py','broker-gate.ko','auto-route.ko'}
PAYLOAD={'global_install.py','README.md','NOTICE','LICENSES/GPL-2.0-only.txt','LICENSES/LGPL-2.1-or-later.txt',
    'native/installer.py','native/configuration.py','native/supervisor.py','native/manifest.json',
    'native/c5_es_poc.ko','native/plugins/libgstdtspassthrough.so',
    'hdmi/hdmi_supervisor.py','hdmi/alsa_controls.py','hdmi/luna_rpc.py','hdmi/runtime_settings.py',
    'hdmi/edid.py','hdmi/hpd.py','hdmi/broker-gate.ko','hdmi/auto-route.ko','hdmi/hdmi-manifest.json'}
PAYLOAD.update('hdmi_hd/'+name for name in HD_FILES|{'hdmi-manifest.json'})


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_target():
    if os.geteuid()!=0 or platform.system()!='Linux' or platform.release()!=KERNEL:
        raise RuntimeError('root on the exact C5 kernel is required')


def verify(bundle):
    bundle=Path(bundle)
    if bundle.is_symlink():raise RuntimeError('symlink bundle')
    manifest=json.loads((bundle/'global-manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('kernel')!=KERNEL or set(manifest.get('files',{}))!=PAYLOAD:
        raise RuntimeError('unexpected global package')
    for name,digest in manifest['files'].items():
        cursor=bundle
        for part in Path(name).parts:
            cursor=cursor/part
            if cursor.is_symlink():raise RuntimeError('symlink package component')
        if not cursor.is_file() or sha(cursor)!=digest:raise RuntimeError('package artifact changed: '+name)
    for name,kind,machine in [('native/c5_es_poc.ko',2,183),('hdmi/broker-gate.ko',2,183),
        ('hdmi/auto-route.ko',2,183),('hdmi_hd/broker-gate.ko',2,183),
        ('hdmi_hd/auto-route.ko',2,183),('native/plugins/libgstdtspassthrough.so',1,40)]:
        data=(bundle/name).read_bytes()
        if data[:4]!=b'\x7fELF' or data[4]!=kind or int.from_bytes(data[18:20],'little')!=machine:
            raise RuntimeError('wrong target architecture')
    native=json.loads((bundle/'native/manifest.json').read_text())
    native_files={'installer.py','configuration.py','supervisor.py','c5_es_poc.ko','plugins/libgstdtspassthrough.so'}
    if (native.get('schema')!=1 or native.get('kernel')!=KERNEL or native.get('module_build')!=NATIVE_BUILD or
        native.get('blocking_review_findings') or set(native.get('files',{}))!=native_files):
        raise RuntimeError('unverified native prerequisite')
    for name,digest in native['files'].items():
        if name not in native_files:
            raise RuntimeError('unexpected native component')
        if sha(bundle/'native'/name)!=digest:raise RuntimeError('native manifest mismatch')
    hdmi=json.loads((bundle/'hdmi/hdmi-manifest.json').read_text())
    hdmi_files={'broker-gate.ko','auto-route.ko','hdmi_supervisor.py','alsa_controls.py','luna_rpc.py','runtime_settings.py','edid.py','hpd.py'}
    if hdmi.get('schema')!=1 or hdmi.get('kernel')!=KERNEL or set(hdmi.get('files',{}))!=hdmi_files:
        raise RuntimeError('bad HDMI manifest')
    for name,digest in hdmi['files'].items():
        if 'hdmi/'+name not in PAYLOAD or sha(bundle/'hdmi'/name)!=digest:raise RuntimeError('HDMI manifest mismatch')
    hd=json.loads((bundle/'hdmi_hd/hdmi-manifest.json').read_text())
    if hd.get('schema')!=1 or hd.get('kernel')!=KERNEL or set(hd.get('files',{}))!=HD_FILES:
        raise RuntimeError('bad HDMI-HD manifest')
    for name,digest in hd['files'].items():
        if sha(bundle/'hdmi_hd'/name)!=digest:raise RuntimeError('HDMI-HD manifest mismatch')
    return manifest


def secure_directory(path):
    if path.is_symlink():raise RuntimeError('symlink installation directory')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=path.stat()
    if info.st_uid!=os.geteuid() or info.st_mode&0o077:raise RuntimeError('unsafe installation directory')


def atomic_file(path,data,mode=0o600):
    next_path=path.with_suffix('.next')
    fd=os.open(next_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    inode=os.fstat(fd).st_ino;linked=False
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        # Never replace a state file created by another actor between checks.
        os.link(next_path,path,follow_symlinks=False);linked=True;next_path.unlink()
    except BaseException:
        if linked and path.lstat().st_ino==inode:path.unlink()
        if next_path.exists() and next_path.lstat().st_ino==inode:next_path.unlink()
        raise


def hook_bytes(prefix):
    return ('#!/bin/sh\nexport PATH=/usr/sbin:/usr/bin:/sbin:/bin\nexec /usr/bin/python3 '+str(prefix)+'/current/global_install.py boot\n').encode()


def install(bundle,prefix=PREFIX,hook=HOOK):
    manifest=verify(bundle);secure_directory(prefix)
    with (prefix/'install.lock').open('a') as lock:
        os.fchmod(lock.fileno(),0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        version=sha(Path(bundle)/'global-manifest.json')[:20]
        versions=prefix/'versions';secure_directory(versions);destination=versions/version
        if destination.is_symlink():raise RuntimeError('foreign version link')
        if not destination.exists():
            temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=versions))
            try:
                for name in sorted(PAYLOAD|{'global-manifest.json'}):
                    target=temporary/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    shutil.copy2(Path(bundle)/name,target);target.chmod(0o600 if name.endswith('.py') else 0o644)
                verify(temporary);temporary.rename(destination)
            except BaseException:shutil.rmtree(temporary);raise
        verify(destination)
        current=prefix/'current';state_path=prefix/'installed.json'
        expected={'version':version,'hook_sha256':hashlib.sha256(hook_bytes(prefix)).hexdigest(),'reboot_verified':False}
        if state_path.exists():
            state=json.loads(state_path.read_text())
            if state!=expected or not current.is_symlink() or current.resolve()!=destination.resolve() or hook.is_symlink() or sha(hook)!=expected['hook_sha256']:
                raise RuntimeError('different existing installation; no overwrite')
            return dict(expected,boot='already installed',current_audio='unchanged')
        if any(p.exists() or p.is_symlink() for p in [current,state_path,hook]):raise RuntimeError('foreign installation path')
        created=[]
        try:
            os.symlink('versions/'+version,current);created.append((current,current.lstat().st_ino))
            fd=os.open(hook,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o700)
            created.append((hook,os.fstat(fd).st_ino))
            with os.fdopen(fd,'wb') as stream:stream.write(hook_bytes(prefix))
            atomic_file(state_path,(json.dumps(expected,indent=2)+'\n').encode());created.append((state_path,state_path.lstat().st_ino))
        except BaseException:
            for path,inode in reversed(created):
                if path.lstat().st_ino!=inode:raise RuntimeError('foreign change during rollback; stop')
                path.unlink()
            raise
        return dict(expected,boot='installed',current_audio='unchanged')


def current_bundle(prefix=PREFIX,hook=HOOK):
    secure_directory(prefix);state=json.loads((prefix/'installed.json').read_text());current=prefix/'current'
    if not current.is_symlink() or current.resolve()!=(prefix/'versions'/state['version']).resolve():
        raise RuntimeError('current package identity changed')
    if hook.is_symlink() or sha(hook)!=state['hook_sha256']:raise RuntimeError('boot hook identity changed')
    verify(current.resolve());return current.resolve(),state


def disable(prefix=PREFIX,hook=HOOK):
    bundle,state=current_bundle(prefix,hook)
    hook.unlink();(prefix/'current').unlink();(prefix/'installed.json').unlink()
    return {'boot':'disabled','current_audio':'unchanged; resident modules retained until reboot'}


def start_controller(bundle,run,script):
    secure_directory(run)
    ready=run/'hdmi-ready.json';log=run/'controller.log'
    if ready.exists() or ready.is_symlink() or log.is_symlink():
        raise RuntimeError('existing or foreign controller endpoint; do not stack')
    with log.open('a') as output:
        os.fchmod(output.fileno(),0o600)
        process=subprocess.Popen(['/usr/bin/python3',str(bundle/script),'--activate',
            '--bundle',str(bundle),'--run',str(run)],stdout=output,stderr=subprocess.STDOUT,
            start_new_session=True,env=dict(os.environ,PATH='/usr/sbin:/usr/bin:/sbin:/bin'))
    deadline=time.monotonic()+12
    while not ready.exists():
        if process.poll() is not None or time.monotonic()>deadline:
            raise RuntimeError('controller did not initialize; inspect log; no replacement attempted')
        time.sleep(.05)
    if (ready.is_symlink() or ready.stat().st_uid!=os.geteuid() or ready.stat().st_mode&0o077 or
        json.loads(ready.read_text()).get('pid')!=process.pid or process.poll() is not None):
        raise RuntimeError('foreign or dead readiness endpoint')
    return process.pid


def boot():
    require_target();bundle,state=current_bundle();secure_directory(RUN)
    with (RUN/'boot.lock').open('a') as lock:
        os.fchmod(lock.fileno(),0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (RUN/'started.json').exists():raise RuntimeError('global activation already recorded; do not stack')
        import sys
        sys.path.insert(0,str(bundle/'native'))
        native_spec=importlib.util.spec_from_file_location('native_installer',bundle/'native/installer.py')
        native=importlib.util.module_from_spec(native_spec);native_spec.loader.exec_module(native)
        deadline=time.monotonic()+30
        pairs=[('/home/root/dts_restore-2.0/gst/libgstlibav.so','/usr/lib/gstreamer-1.0/libgstlibav.so'),
            ('/tmp/gst_1_0_registry.arm.bin','/mnt/flash/data/gst_1_0_registry.arm.bin'),('/tmp/gstcool.conf','/etc/gst/gstcool.conf')]
        while not all(Path(a).is_file() and native.identity(a)==native.identity(b) for a,b in pairs):
            if time.monotonic()>deadline:raise RuntimeError('original restore hook not ready')
            time.sleep(.1)
        sys.path.insert(0,str(bundle/'hdmi'))
        from luna_rpc import luna
        deadline=time.monotonic()+60
        while True:
            try:
                luna('luna://com.webos.applicationManager/getForegroundAppInfo',{})
                luna('luna://com.webos.service.audiooutput/quality/getSettings',{'category':'external_arc'})
                break
            except (RuntimeError,TimeoutError):
                if time.monotonic()>deadline:raise
                time.sleep(.5)
        if not Path('/sys/module/c5_es_poc').exists():
            native.command('insmod',str(bundle/'native/c5_es_poc.ko'),'selftest_only=0',timeout=5)
        params=Path('/sys/module/c5_es_poc/parameters')
        if (params/'session_build').read_text().strip()!=NATIVE_BUILD or (params/'lease_api_only').read_text().strip() not in ('N','0'):
            raise RuntimeError('different native driver; no activation')
        hdmi_pid=start_controller(bundle/'hdmi',RUN/'hdmi','hdmi_supervisor.py')
        hd_pid=None;hd_error=None
        try:hd_pid=start_controller(bundle/'hdmi_hd',RUN/'hdmi-hd','hdmi_hd_supervisor.py')
        except (OSError,RuntimeError) as error:hd_error=str(error)
        native_result=native.activate(bundle/'native',NATIVE_RUN,True)
        result={'version':state['version'],'hdmi_pid':hdmi_pid,'hdmi_hd_pid':hd_pid,
            'hdmi_hd_error':hd_error,'native':native_result,'reboot_audio_verified':False}
        atomic_file(RUN/'started.json',(json.dumps(result,indent=2)+'\n').encode())
        print(json.dumps(result),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['dry-run','install-boot','disable-boot','boot'])
    parser.add_argument('--bundle',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args()
    if args.action=='dry-run':
        verify(args.bundle);print(json.dumps({'package':'verified','boot':'unchanged','reboot_audio_verified':False}));return
    require_target()
    if args.action=='install-boot':
        if Path('/var/lib/webosbrew/init.d/zz-c5-dts-passthrough').exists():raise RuntimeError('older native boot hook still active; disable owned old installation first')
        print(json.dumps(install(args.bundle)))
    elif args.action=='disable-boot':print(json.dumps(disable()))
    else:boot()


if __name__=='__main__':main()
