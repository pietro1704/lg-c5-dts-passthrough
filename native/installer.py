# SPDX-License-Identifier: GPL-2.0-only
"""Verified staging, temporary activation and explicit boot installation.

Never unload the resident driver or restore controls over another session.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
from configuration import native_configuration

FILES={'plugins/libgstdtspassthrough.so','c5_es_poc.ko','supervisor.py','configuration.py','installer.py'}
KERNEL='5.4.268-329.ptl4tv.5'
BOOT_HOOK=Path('/var/lib/webosbrew/init.d/zz-c5-dts-passthrough')
COMMAND_PATH='/usr/sbin:/usr/bin:/sbin:/bin'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def registry_environment(bundle,registry):
    plugins=str(bundle/'plugins')+':/usr/lib/gstreamer-1.0:/mnt/lg/res/lglib/gstreamer-1.0'
    return dict(os.environ,GST_PLUGIN_PATH=plugins,GST_PLUGIN_PATH_1_0=plugins,
                GST_REGISTRY=str(registry),GST_REGISTRY_1_0=str(registry),GST_REGISTRY_UPDATE='yes')

def identity(path):
    info=Path(path).stat();return [info.st_dev,info.st_ino]


def rewrite_mounted_registry(path,data,expected,info):
    """A bind-source dentry cannot be renamed on webOS; retain its inode.

    The caller has already fsynced the backup/journal. This bounded write does
    not affect the separately bound live registry. Power loss can leave an
    incomplete disk cache; GStreamer can regenerate it, and the backup remains.
    """
    original=path.read_bytes()
    fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    with os.fdopen(fd,'r+b',buffering=0) as output:
        current=os.fstat(output.fileno())
        if (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino) or sha(path)!=expected:
            raise RuntimeError('mounted registry identity changed')
        try:
            offset=0
            while offset<len(data):
                written=output.write(data[offset:])
                if not written:raise OSError('short registry write')
                offset+=written
            output.truncate(len(data));os.fsync(output.fileno())
            if sha(path)!=hashlib.sha256(data).hexdigest():raise RuntimeError('cold registry readback mismatch')
        except BaseException:
            if identity(path)!=[info.st_dev,info.st_ino]:raise RuntimeError('foreign registry during write rollback')
            output.seek(0);offset=0
            while offset<len(original):
                written=output.write(original[offset:])
                if not written:raise OSError('short registry rollback')
                offset+=written
            output.truncate(len(original));os.fsync(output.fileno())
            raise


def replace_registry(path,data,expected):
    """Replace a regular, owned registry only while its identity still matches."""
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or sha(path)!=expected:
        raise RuntimeError('foreign cold registry; no overwrite')
    fd,name=tempfile.mkstemp(prefix='.c5-registry-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            os.fchmod(output.fileno(),stat.S_IMODE(info.st_mode))
            os.fchown(output.fileno(),info.st_uid,info.st_gid)
            output.write(data);output.flush();os.fsync(output.fileno())
        current=path.lstat()
        if ((current.st_dev,current.st_ino,current.st_mtime_ns,current.st_ctime_ns)!=
                (info.st_dev,info.st_ino,info.st_mtime_ns,info.st_ctime_ns) or sha(path)!=expected):
            raise RuntimeError('cold registry changed during preparation')
        try:os.replace(name,path)
        except OSError as error:
            if error.errno!=errno.EBUSY:raise
            rewrite_mounted_registry(path,data,expected,info)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


def seed_cold_registry(bundle,registry,state_dir):
    """Seed the disk registry before next boot's preloaded media workers start.

    Caller supplies an unoverlaid view; current runtime bindings are untouched.
    A prepared journal allows retry after interruption on either side of replace.
    """
    verify_bundle(bundle)
    if state_dir.is_symlink():raise RuntimeError('symlink cold registry state')
    state_dir.mkdir(mode=0o700,exist_ok=True)
    info=state_dir.stat()
    if info.st_uid!=os.geteuid() or info.st_mode&0o077:raise RuntimeError('unsafe cold registry state')
    state_path=state_dir/'state.json';backup=state_dir/'original.bin';candidate=state_dir/'seeded.bin'
    if any(p.is_symlink() for p in (registry,state_path,backup,candidate)):
        raise RuntimeError('symlink cold registry component')
    plugin=bundle/'plugins/libgstdtspassthrough.so'
    if state_path.exists():
        state=json.loads(state_path.read_text())
        if state['plugin']!=str(plugin) or state['plugin_sha256']!=sha(plugin):
            raise RuntimeError('different cold registry installation')
        if sha(backup)!=state['original_sha256'] or sha(candidate)!=state['seeded_sha256']:
            raise RuntimeError('cold registry backup or candidate changed')
        if sha(registry)==state['seeded_sha256']:return state
        replace_registry(registry,candidate.read_bytes(),state['original_sha256'])
        return state
    if backup.exists() or candidate.exists():raise RuntimeError('unjournaled cold registry files; inspect before retry')
    original=registry.lstat()
    if not stat.S_ISREG(original.st_mode) or original.st_uid!=os.geteuid():
        raise RuntimeError('foreign cold registry')
    before=sha(registry)
    try:
        shutil.copy2(registry,backup);shutil.copy2(registry,candidate)
        inspected=command('gst-inspect-1.0','dtspassthrough',timeout=5,env=registry_environment(bundle,candidate))
        if str(plugin) not in inspected or not re.search(r'Rank\s+.*\(310\)',inspected):
            raise RuntimeError('cold registry plugin identity or rank unavailable')
        current=registry.lstat()
        if ((current.st_dev,current.st_ino,current.st_mtime_ns,current.st_ctime_ns)!=
                (original.st_dev,original.st_ino,original.st_mtime_ns,original.st_ctime_ns) or sha(registry)!=before):
            raise RuntimeError('cold registry changed during inspection')
        state={'plugin':str(plugin),'plugin_sha256':sha(plugin),
            'original_sha256':before,'seeded_sha256':sha(candidate)}
        for path in (backup,candidate):
            with path.open('rb') as stream:os.fsync(stream.fileno())
        save(state,state_path)
        directory=os.open(state_dir,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        replace_registry(registry,candidate.read_bytes(),before)
        return state
    except BaseException:
        # Once journaled, retain all evidence for deterministic retry/rollback.
        if not state_path.exists():
            for path in (backup,candidate):
                if path.exists():path.unlink()
        raise


def restore_cold_registry(registry,state_dir):
    state_path=state_dir/'state.json';backup=state_dir/'original.bin'
    if any(p.is_symlink() for p in (state_dir,state_path,backup,registry)):
        raise RuntimeError('symlink cold registry rollback component')
    state=json.loads(state_path.read_text())
    if sha(backup)!=state['original_sha256']:raise RuntimeError('cold registry backup changed')
    if sha(registry)!=state['original_sha256']:
        replace_registry(registry,backup.read_bytes(),state['seeded_sha256'])
    for name in ('state.json','original.bin','seeded.bin'):(state_dir/name).unlink()
    state_dir.rmdir()


def verify_bundle(bundle):
    if bundle.is_symlink():raise ValueError('symlink package root')
    manifest=json.loads((bundle/'manifest.json').read_text())
    if manifest.get('schema')!=1 or set(manifest.get('files',{}))!=FILES:
        raise ValueError('unexpected package schema/files')
    if manifest.get('kernel')!=KERNEL or not re.fullmatch('[a-f0-9]{64}',manifest.get('module_build','')):
        raise ValueError('unexpected target/build')
    for name,digest in manifest['files'].items():
        path=bundle/name
        cursor=bundle
        for part in Path(name).parts:
            cursor=cursor/part
            if cursor.is_symlink():raise ValueError('symlink package component: '+name)
        if not path.is_file() or path.is_symlink() or sha(path)!=digest:
            raise ValueError('changed/nonregular package artifact: '+name)
    for name,elf_class,machine in [('plugins/libgstdtspassthrough.so',1,40),('c5_es_poc.ko',2,183)]:
        data=(bundle/name).read_bytes()
        if data[:4]!=b'\x7fELF' or data[4]!=elf_class or int.from_bytes(data[18:20],'little')!=machine:
            raise ValueError('wrong target architecture: '+name)
    return manifest


def plan(bundle,prefix):
    manifest=verify_bundle(bundle)
    return {'candidate':True,'hardware_validated':False,'package':str(bundle),
            'activation_blocked':bool(manifest.get('blocking_review_findings')),
            'blocking_review_findings':manifest.get('blocking_review_findings',[]),
            'prefix':str(prefix),'kernel':manifest['kernel'],
            'bindings':['/etc/gst/gstcool.conf','/mnt/flash/data/gst_1_0_registry.arm.bin'],
            'driver':'loaded only on an authenticated DTS-HD request; resident after first renderer until reboot',
            'boot':'disabled until explicitly configured with install-boot',
            'unresolved_acceptance':manifest.get('unresolved_acceptance',[])}


def require_target():
    if os.geteuid()!=0 or platform.system()!='Linux' or platform.release()!=KERNEL:
        raise RuntimeError('this mutation is allowed only as root on the exact target kernel')


def stage(bundle,prefix):
    require_target();manifest=verify_bundle(bundle)
    if prefix.is_symlink():raise RuntimeError('symlink installation prefix')
    prefix=prefix.resolve()
    if not re.fullmatch(r'/home/root/[a-zA-Z0-9_-]+',str(prefix)):
        raise ValueError('unexpected installation prefix')
    prefix.mkdir(mode=0o700,parents=True,exist_ok=True)
    if prefix.is_symlink() or prefix.stat().st_uid!=0 or prefix.stat().st_mode & 0o077:
        raise RuntimeError('unsafe installation directory')
    versions=prefix/'versions';versions.mkdir(mode=0o700,exist_ok=True)
    if versions.is_symlink():raise RuntimeError('unsafe versions directory')
    token=sha(bundle/'manifest.json')[:20]
    destination=versions/token
    if destination.is_symlink():raise RuntimeError('symlink package version')
    if destination.exists():verify_bundle(destination);return destination
    temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=versions))
    try:
        for name in sorted(FILES|{'manifest.json'}):
            target=temporary/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(bundle/name,target);target.chmod(0o600 if name.endswith('.py') else 0o644)
        verify_bundle(temporary)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary);raise
    return destination


def save(state,path):
    temporary=path.with_suffix('.next')
    with temporary.open('w') as output:
        json.dump(state,output,indent=2);output.write('\n');output.flush();os.fsync(output.fileno())
    temporary.chmod(0o600);temporary.replace(path)


class UncertainCommand(RuntimeError):
    pass


def command(*args,timeout=5,env=None):
    # Homebrew boot inherits a reduced PATH; module and mount tools live in
    # sbin. Keep registry overrides while using only trusted system tool dirs.
    environment=dict(os.environ if env is None else env,PATH=COMMAND_PATH)
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=environment)
    try:stdout,stderr=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()
        raise UncertainCommand('target command exceeded deadline; journal retained, no further mutations')
    if process.returncode:raise RuntimeError('target command failed: '+Path(args[0]).name)
    return stdout


def rollback_bindings(state,state_file):
    for item in reversed(state['bindings']):
        current=identity(item['target'])
        if current==item['source_identity']:
            command('umount',item['target'])
        elif current!=item['before_identity']:
            raise RuntimeError('foreign binding: refusing to unmount '+item['target'])
        if sha(item['target'])!=item['before_sha256']:
            raise RuntimeError('original content changed; state retained for inspection')
        state['bindings'].remove(item);save(state,state_file)


def activation_transaction(state,state_file,pairs,start):
    try:
        for source,target in pairs:
            item={'source':str(source),'target':str(target),'source_identity':identity(source),
                  'before_identity':identity(target),'before_sha256':sha(target),'phase':'planned'}
            state['bindings'].append(item);save(state,state_file)
            command('mount','-n','--bind',str(source),str(target))
            if identity(target)!=identity(source):raise RuntimeError('binding identity changed')
            item['phase']='bound';save(state,state_file)
        return start()
    except UncertainCommand:
        state['uncertain_operation']=True;save(state,state_file)
        raise
    except BaseException:
        rollback_bindings(state,state_file)
        raise


def verify_endpoint(endpoint,pid):
    import socket,struct
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(.2);connection.connect(str(endpoint))
        peer,uid,_gid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if peer!=pid or uid!=0:raise RuntimeError('control endpoint belongs to another process')


def spawn_supervisor(bundle,manifest,run,output):
    return subprocess.Popen(['/usr/bin/python3',str(bundle/'supervisor.py'),
        '--module',str(bundle/'c5_es_poc.ko'),'--module-sha256',manifest['files']['c5_es_poc.ko'],
        '--module-build',manifest['module_build'],'--plugin',str(bundle/'plugins/libgstdtspassthrough.so'),
        '--plugin-sha256',manifest['files']['plugins/libgstdtspassthrough.so'],'--control',str(run)],
        stdout=output,stderr=subprocess.STDOUT,start_new_session=True,
        env=dict(os.environ,PATH=COMMAND_PATH))


def _activate_unlocked(bundle,run,allow_trial=False):
    require_target();manifest=verify_bundle(bundle)
    if not allow_trial:
        raise RuntimeError('hardware-unverified candidate: explicit --allow-hardware-trial required')
    if manifest.get('blocking_review_findings'):
        raise RuntimeError('candidate has unresolved review/acceptance; activation disabled')
    if run.exists() and (run.is_symlink() or run.stat().st_uid!=0 or run.stat().st_mode & 0o077):
        raise RuntimeError('unsafe runtime directory')
    run.mkdir(mode=0o700,exist_ok=True)
    state_file=run/'installation-state.json'
    if (run/'control.sock').exists() or (run/'control.sock').is_symlink():raise RuntimeError('preexisting control endpoint; refusing activation')
    if state_file.exists():raise RuntimeError('existing activation state; inspect or deactivate first')
    if not Path('/var/lib/webosbrew/init.d/restore_dts').exists():
        raise RuntimeError('existing dts_restore boot integration is required')
    if ' /usr/lib/gstreamer-1.0/libgstlibav.so ' not in Path('/proc/mounts').read_text():
        raise RuntimeError('existing libav override is not active; no bindings changed')
    registry=Path('/mnt/flash/data/gst_1_0_registry.arm.bin')
    config=Path('/etc/gst/gstcool.conf')
    generated_registry=run/'registry.bin';generated_config=run/'gstcool.conf'
    shutil.copy2(registry,generated_registry)
    generated_config.write_text(native_configuration(config.read_text()))
    environment=registry_environment(bundle,generated_registry)
    inspected=command('gst-inspect-1.0','dtspassthrough',timeout=5,env=environment)
    if str(bundle/'plugins/libgstdtspassthrough.so') not in inspected:
        raise RuntimeError('target dependencies or plugin identity unavailable; no bindings changed')
    if not re.search(r'Rank\s+.*\(310\)',inspected):raise RuntimeError('wrong plugin rank')
    state={'bundle':str(bundle),'boot':False,'bindings':[]}
    save(state,state_file)
    def start():
        process=None
        try:
            with (run/'supervisor.log').open('a') as output:
                os.chmod(run/'supervisor.log',0o600)
                process=spawn_supervisor(bundle,manifest,run,output)
            state['supervisor_pid']=process.pid;save(state,state_file)
            deadline=time.monotonic()+3
            while not (run/'control.sock').is_socket():
                if process.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('supervisor did not become ready')
                time.sleep(.02)
            if process.poll() is not None:raise RuntimeError('supervisor exited before readiness check')
            verify_endpoint(run/'control.sock',process.pid)
            return {'temporary_patch':'ready','boot':False,'driver_loaded':False}
        except BaseException:
            if process is not None:process.terminate() # never wait for an uncertain target task
            raise
    return activation_transaction(state,state_file,[(generated_registry,registry),(generated_config,config)],start)


def activate(bundle,run,allow_trial=False):
    import fcntl
    require_target();manifest=verify_bundle(bundle)
    if not allow_trial:raise RuntimeError('explicit hardware-trial flag required')
    if manifest.get('blocking_review_findings'):raise RuntimeError('unresolved review finding; activation disabled')
    if run.exists() and (run.is_symlink() or run.stat().st_uid!=0 or run.stat().st_mode & 0o077):
        raise RuntimeError('unsafe runtime directory')
    run.mkdir(mode=0o700,exist_ok=True)
    lock_path=run/'installation.lock'
    if lock_path.is_symlink():raise RuntimeError('unsafe installation lock')
    with lock_path.open('a') as lock:
        os.fchmod(lock.fileno(),0o600)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _activate_unlocked(bundle,run,allow_trial)


def deactivate(run):
    require_target();state_file=run/'installation-state.json'
    state=json.loads(state_file.read_text())
    module=Path('/sys/module/c5_es_poc')
    if module.exists():
        from supervisor import parse_state
        if any(lease.opened for lease in parse_state((module/'parameters/session_state').read_text()).leases):
            raise RuntimeError('stop the test playback before deactivation; no active lease will be killed')
    (run/'STOP_NEW_TRIALS').write_text('deactivation requested\n')
    deadline=time.monotonic()+3
    while (run/'control.sock').exists():
        if time.monotonic()>deadline:raise RuntimeError('supervisor still active; no forced cleanup')
        time.sleep(.02)
    rollback_bindings(state,state_file);state_file.unlink()
    return {'userspace_bindings':'restored','driver':'retained if resident; reboot required for complete removal'}


def boot_hook(prefix):
    return ('#!/bin/sh\n# Managed LG C5 DTS passthrough; no service restart or driver unload.\n'
            'exec /usr/bin/python3 '+str(prefix)+'/current/installer.py boot --prefix '+str(prefix)+
            ' >> '+str(prefix)+'/boot.log 2>&1\n').encode()


def secure_prefix(prefix):
    if prefix.is_symlink() or not re.fullmatch(r'/home/root/[a-zA-Z0-9_-]+',str(prefix)):
        raise RuntimeError('unsafe persistent prefix')
    for path in (prefix,prefix/'versions'):
        info=path.stat()
        if path.is_symlink() or info.st_uid!=os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('unsafe persistent directory')


def current_bundle(prefix):
    secure_prefix(prefix)
    state=json.loads((prefix/'persistent-state.json').read_text())
    version=state['version']
    if not re.fullmatch('[a-f0-9]{20}',version):raise RuntimeError('invalid installed version')
    current=prefix/'current';bundle=prefix/'versions'/version
    if not current.is_symlink() or os.readlink(current)!='versions/'+version:
        raise RuntimeError('foreign current pointer')
    manifest=verify_bundle(bundle)
    if sha(bundle/'manifest.json')[:20]!=version:raise RuntimeError('version identity changed')
    for name in FILES|{'manifest.json'}:
        info=(bundle/name).stat()
        if info.st_uid!=os.geteuid() or info.st_mode & 0o022:raise RuntimeError('unsafe installed artifact')
    return bundle,manifest,state


def create_owned_file(path,data,mode=0o600):
    # A hard link publishes fully written bytes without replacing an existing
    # path. Temp and destination share a filesystem; a foreign node is refused.
    fd,name=tempfile.mkstemp(prefix='.c5-install-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:
            os.fchmod(output.fileno(),mode);output.write(data);output.flush();os.fsync(output.fileno())
        os.link(temporary,path)
    finally:temporary.unlink()


def install_boot(bundle,prefix,hook):
    require_target();manifest=verify_bundle(bundle)
    if manifest.get('blocking_review_findings'):raise RuntimeError('review blocker prevents boot installation')
    destination=stage(bundle,prefix);secure_prefix(prefix)
    if hook!=BOOT_HOOK:
        raise RuntimeError('unexpected boot hook path')
    lock_path=prefix/'installation.lock'
    if lock_path.is_symlink():raise RuntimeError('foreign installation lock')
    import fcntl
    with lock_path.open('a') as lock:
        os.fchmod(lock.fileno(),0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state_path=prefix/'persistent-state.json';current=prefix/'current'
        expected={'version':destination.name,'hook':str(hook),'hook_sha256':hashlib.sha256(boot_hook(prefix)).hexdigest()}
        if state_path.exists():
            installed,_,state=current_bundle(prefix)
            if state!=expected or installed!=destination or hook.is_symlink() or sha(hook)!=expected['hook_sha256']:
                raise RuntimeError('different persistent installation; no overwrite')
            return {'boot':'already configured','version':destination.name,'current_session':'unchanged'}
        for path in (state_path,current,hook,prefix/'persistent-transaction.json'):
            if path.exists() or path.is_symlink():raise RuntimeError('foreign or unfinished installation: '+path.name)
        journal=prefix/'persistent-transaction.json';created=[];transaction={'planned':[],'created':[]}
        try:
            for path,data in ((state_path,(json.dumps(expected,indent=2)+'\n').encode()),(hook,boot_hook(prefix)),(current,None)):
                transaction['planned'].append(str(path));save(transaction,journal)
                if data is None:os.symlink('versions/'+destination.name,path)
                else:create_owned_file(path,data,0o700 if path==hook else 0o600)
                marker=[path.lstat().st_dev,path.lstat().st_ino]
                created.append((path,marker));transaction['created'].append([str(path),marker]);save(transaction,journal)
            current_bundle(prefix);journal.unlink()
        except BaseException:
            for path,marker in reversed(created):
                info=path.lstat()
                if [info.st_dev,info.st_ino]!=marker:raise RuntimeError('foreign change during rollback; journal retained')
                path.unlink()
            if journal.exists():journal.unlink()
            raise
    return {'boot':'configured','version':destination.name,'current_session':'unchanged','reboot_validated':False}


def start_boot(prefix,run):
    require_target();bundle,_,_=current_bundle(prefix)
    if (run/'installation-state.json').exists() or (run/'control.sock').exists():
        raise RuntimeError('an activation already exists; no stacked bindings')
    # Homebrew hooks may run concurrently. The original restore_dts binds all
    # three resources; wait for its complete configuration before layering ours.
    deadline=time.monotonic()+10
    while True:
        pairs=(('/home/root/dts_restore-2.0/gst/libgstlibav.so','/usr/lib/gstreamer-1.0/libgstlibav.so'),
               ('/tmp/gst_1_0_registry.arm.bin','/mnt/flash/data/gst_1_0_registry.arm.bin'),
               ('/tmp/gstcool.conf','/etc/gst/gstcool.conf'))
        if all(Path(a).is_file() and identity(a)==identity(b) for a,b in pairs):break
        if time.monotonic()>deadline:raise RuntimeError('original DTS restore hook not complete; no changes')
        time.sleep(.1)
    return activate(bundle,run,True)


def _disable_boot(prefix):
    require_target();_,_,state=current_bundle(prefix)
    hook=Path(state['hook'])
    if hook!=BOOT_HOOK or hook.is_symlink() or sha(hook)!=state['hook_sha256']:
        raise RuntimeError('foreign boot hook; no removal')
    hook.unlink();(prefix/'current').unlink();(prefix/'persistent-state.json').unlink()
    return {'boot':'disabled','current_session':'unchanged; retained driver until reboot'}


def disable_boot(prefix):
    require_target();secure_prefix(prefix)
    import fcntl
    path=prefix/'installation.lock'
    if path.is_symlink():raise RuntimeError('foreign installation lock')
    with path.open('a') as lock:
        os.fchmod(lock.fileno(),0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _disable_boot(prefix)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['dry-run','stage','activate','deactivate','install-boot','boot','disable-boot'])
    parser.add_argument('--bundle',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--prefix',type=Path,default=Path('/home/root/c5-dts-passthrough'))
    parser.add_argument('--run',type=Path,default=Path('/run/c5-dts'))
    parser.add_argument('--allow-hardware-trial',action='store_true')
    options=parser.parse_args()
    if options.action=='dry-run':result=plan(options.bundle,options.prefix)
    elif options.action=='stage':result={'staged':str(stage(options.bundle,options.prefix))}
    elif options.action=='activate':result=activate(options.bundle,options.run,options.allow_hardware_trial)
    elif options.action=='install-boot':result=install_boot(options.bundle,options.prefix,BOOT_HOOK)
    elif options.action=='boot':result=start_boot(options.prefix,options.run)
    elif options.action=='disable-boot':result=disable_boot(options.prefix)
    else:result=deactivate(options.run)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
