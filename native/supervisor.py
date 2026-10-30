# SPDX-License-Identifier: GPL-2.0-only
"""Native DTS-HD session supervisor. Never unloads a driver used for audio.

Only process identity, state and format cross the control socket. Playback URLs,
media bytes and application credentials are not read or transmitted here.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import select
import errno
import stat
import socket
import struct
import subprocess
import threading
import time
COMMAND_PATH='/usr/sbin:/usr/bin:/sbin:/bin'
PROC_ROOT=Path('/proc')
MEDIA_WORKER=Path('/usr/sbin/starfish-media-pipeline')

@dataclass(frozen=True)
class Lease:
    slot: int
    pid: int
    active: int
    generation: int
    index: int
    renderer: int
    es: int
    captures: int
    routes: int
    hd: int
    invalidations: int
    opened: int
    epoch: int
    proof_epoch: int

@dataclass(frozen=True)
class State:
    source: int
    port: int
    resident: int
    leases: tuple

    def owner(self, pid, generation=None):
        matches=[item for item in self.leases if item.pid==pid and
                 (generation is None or item.generation==generation)]
        if len(matches)!=1:
            raise RuntimeError('missing or ambiguous kernel process identity')
        return matches[0]


def parse_state(text):
    lines=text.splitlines()
    if len(lines)!=5:
        raise RuntimeError('unexpected kernel state ABI')
    header=lines[0].split()
    if len(header)!=4 or header[0]!='main':
        raise RuntimeError('missing kernel source selection')
    source,port,resident=map(int,header[1:])
    leases=[]
    for position,line in enumerate(lines[1:]):
        fields=tuple(map(int,line.split()))
        if len(fields)!=14 or fields[0]!=position or min(fields)<0:
            raise RuntimeError('invalid lease record')
        item=Lease(*fields)
        if item.active not in (0,1) or item.es not in (0,1) or item.opened not in (0,1) or item.active>item.opened:
            raise RuntimeError('invalid kernel booleans')
        leases.append(item)
    return State(source,port,resident,tuple(leases))


def edge(graph, source, target):
    return bool(re.search(r'\b'+re.escape(source)+r'\s*->\s*'+re.escape(target)+r'\b',graph))


def dsp_hd_format(text):
    # The output record must contain exactly one value for each field. Reject
    # duplicate/conflicting values and codec-prefix matches.
    blocks=[block for block in text.split('subgraph ') if 'label="output_arc:' in block]
    if len(blocks)!=1:return False
    block=blocks[0]
    patterns={
        'channels':r'(?<![\w-])channels=\(int\)([0-9]+)(?=\\l|[\s,;"}])',
        'bits':r'(?<![\w-])bits-per-sample=\(int\)([0-9]+)(?=\\l|[\s,;"}])',
        'rate':r'(?<![\w-])rate=\(int\)([0-9]+)(?=\\l|[\s,;"}])',
        'es':r'(?<![\w-])es=\(bool\)(true|false)(?=\\l|[\s,;"}])',
        'codec':r'(?<![\w-])codec=\(str\)([a-zA-Z0-9_]+)(?=\\l|[\s,;"}])',
    }
    values={key:re.findall(pattern,block) for key,pattern in patterns.items()}
    return (values['channels']==['8'] and values['bits']==['16'] and
            values['rate']==['192000'] and values['es']==['true'] and
            values['codec'] in (['dts_hd_ma'],['dts_hd']))


def route_confirmed(state, lease, graph, dsp, epoch=1):
    owners=[item for item in state.leases if item.active and item.index==state.port]
    return (len(owners)==1 and owners[0]==lease and
            lease.epoch==epoch and lease.proof_epoch==epoch and
            lease.active==1 and lease.index in (0,1) and state.source==1 and
            state.port==lease.index and lease.renderer==1 and lease.es==1 and
            lease.captures>0 and lease.routes>0 and lease.hd>0 and
            edge(graph,f'renderer_{lease.index}_out100','mixer_0_in100') and
            edge(graph,'mixer_0_out100','output_3_in0') and dsp_hd_format(dsp))


def command(*arguments, timeout=2):
    # communicate(timeout) alone can wait forever while trying to reap a task
    # blocked in a vendor ioctl. Never wait again after terminating that child.
    process=subprocess.Popen(arguments,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                             env=dict(os.environ,PATH=COMMAND_PATH))
    try:
        stdout,stderr=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()
        raise RuntimeError('vendor command exceeded deadline; further routing commands prohibited')
    if process.returncode:
        raise RuntimeError('device command failed: '+Path(arguments[0]).name)
    return stdout


def process_start(pid):
    text=(PROC_ROOT/str(pid)/'stat').read_text()
    return text[text.rindex(')')+2:].split()[19]


def verify_worker(pid,start=None):
    # comm is mutable (prctl); it cannot authenticate a native media worker.
    # Compare the running executable inode with the firmware's canonical binary,
    # and bracket the observation with start time to reject PID reuse.
    observed=process_start(pid)
    if start is not None and observed!=start:
        raise RuntimeError('native process identity changed')
    peer=PROC_ROOT/str(pid)
    expected=MEDIA_WORKER.stat();actual=(peer/'exe').stat()
    if (not stat.S_ISREG(actual.st_mode) or
        (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino) or
        (peer/'comm').read_text().strip()!='starfish-media-'):
        raise RuntimeError('requesting executable is not the native media worker')
    if process_start(pid)!=observed:
        raise RuntimeError('native process identity changed during verification')
    return observed


def verify_plugin(pid,path,digest):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise RuntimeError('plugin artifact changed')
    info=path.stat()
    identity=(os.major(info.st_dev),os.minor(info.st_dev),info.st_ino)
    for line in (PROC_ROOT/str(pid)/'maps').read_text().splitlines():
        fields=line.split()
        major,minor=(int(part,16) for part in fields[3].split(':'))
        if (major,minor,int(fields[4]))==identity:
            return
    raise RuntimeError('requesting process has not mapped the installed plugin inode')


class Kernel:
    def __init__(self,module,digest,build,sys_module=Path('/sys/module/c5_es_poc')):
        self.module,self.digest,self.build,self.root=module,digest,build,sys_module
        self.lock=threading.Lock()
        self.io_failed=threading.Event()

    def state(self):
        return parse_state((self.root/'parameters/session_state').read_text())

    def grant(self,pid):
        with self.lock:
            if self.io_failed.is_set():raise RuntimeError('new driver grants prohibited after vendor failure')
            if hashlib.sha256(self.module.read_bytes()).hexdigest()!=self.digest:
                raise RuntimeError('kernel module artifact changed')
            if not self.root.exists():
                try:command('insmod',str(self.module),'selftest_only=0',timeout=5)
                except (RuntimeError,OSError):
                    self.io_failed.set()
                    raise
            if (self.root/'initstate').read_text().strip()!='live':
                raise RuntimeError('kernel controller is not live; no reservation made')
            if (self.root/'parameters/lease_api_only').read_text().strip() not in ('N','0'):
                raise RuntimeError('isolated API-only test module cannot authorize native audio')
            if (self.root/'parameters/selftest_only').read_text().strip() not in ('N','0'):
                raise RuntimeError('loaded module has no native lease device')
            if (self.root/'parameters/session_protocol').read_text().strip()!='3':
                raise RuntimeError('incompatible loaded kernel controller')
            if (self.root/'parameters/session_build').read_text().strip()!=self.build:
                raise RuntimeError('different resident module revision; restart required')
            (self.root/'parameters/grant_pid').write_text(str(pid)+'\n')
            return self.state().owner(pid).generation

    def vendor_read(self,path):
        if self.io_failed.is_set():raise RuntimeError('vendor commands prohibited after previous failure')
        try:return command('cat',path)
        except (RuntimeError,OSError):
            self.io_failed.set()
            raise

    def refresh(self,guard=lambda:None):
        if self.io_failed.is_set():raise RuntimeError('routing commands prohibited after vendor command failure')
        try:
            text=command('amixer','-c0','cget','numid=92')
            values=re.findall(r'^  : values=([0-9]+(?:,[0-9]+){4})$',text,re.M)
            if len(values)!=1:raise RuntimeError('unrecognized eARC control ABI')
            if self.io_failed.is_set():raise RuntimeError('routing commands prohibited')
            guard()
            command('amixer','-c0','cset','numid=92',values[0])
        except RouteChanged:
            raise
        except (RuntimeError,OSError):
            self.io_failed.set()
            raise


class RouteChanged(RuntimeError):
    """Normal cancellation/ownership change; never a global I/O failure."""


def receive(connection,expected):
    value=bytearray()
    while len(value)<len(expected):
        byte=connection.recv(1)
        if not byte:
            raise EOFError('native client closed')
        value+=byte
    if bytes(value)!=expected:
        raise RuntimeError('unexpected session protocol')


def receive_line(connection):
    value=bytearray()
    while len(value)<48:
        try:byte=connection.recv(1)
        except socket.timeout:
            if value:raise RuntimeError('incomplete control message')
            raise
        if not byte:raise EOFError('native client closed')
        value+=byte
        if byte==b'\n':return bytes(value)
    raise RuntimeError('oversized control message')


def parse_request(line):
    match=re.fullmatch(rb'(PREROLL|CANCEL) ([1-9][0-9]{0,19})\n',line)
    if not match:raise RuntimeError('invalid stream epoch message')
    epoch=int(match[2])
    if epoch>2**32-1:raise RuntimeError('stream epoch overflow')
    return match[1],epoch


def advance_cursor(cursor,kind,epoch):
    if epoch<cursor[0] or (epoch==cursor[0] and not (kind==b'PREROLL' and cursor[1]==b'CANCEL')):
        raise RuntimeError('replayed stream epoch')
    cursor[:]=[epoch,kind]


class Supervisor:
    def __init__(self,kernel,plugin,digest,stop_file):
        self.kernel,self.plugin,self.digest=kernel,plugin,digest
        self.stop_file=stop_file
        self.stop=threading.Event()
        self.clients=set()
        self.client_lock=threading.Lock()
        self.pids=set()
        self.refresh_lock=threading.Lock()
        self.limit=threading.BoundedSemaphore(4)

    def route(self,pid,start,generation,connection=None,cursor=None):
        deadline=time.monotonic()+8
        cursor=cursor if cursor is not None else [1,b'PREROLL']
        if len(cursor)==1:cursor.append(b'PREROLL')
        refreshed=0
        while time.monotonic()<deadline:
            if self.stop.is_set() or self.stop_file.exists():
                raise RuntimeError('trial stopped by health monitor')
            if connection is not None and select.select([connection],[],[],0)[0]:
                kind,epoch=parse_request(receive_line(connection))
                advance_cursor(cursor,kind,epoch)
                if kind==b'CANCEL':return None
                deadline=time.monotonic()+8
                refreshed=0
            verify_worker(pid,start)
            state=self.kernel.state()
            lease=state.owner(pid,generation)
            candidate=(lease.epoch==cursor[0] and lease.proof_epoch==cursor[0] and lease.active and lease.index in (0,1) and state.source==1 and
                       state.port==lease.index and lease.es and lease.routes>0 and lease.hd>0)
            graph=self.kernel.vendor_read('/proc/aaudio/dot_block_connection') if candidate else ''
            dsp=self.kernel.vendor_read('/proc/aaudio/dsp1/dot_registry') if candidate else ''
            if route_confirmed(state,lease,graph,dsp,cursor[0]):
                verify_worker(pid,start)
                verify_plugin(pid,self.plugin,self.digest)
                return cursor[0]
            if (lease.epoch==cursor[0] and lease.active and lease.es and lease.index in (0,1) and
                state.source==1 and state.port==lease.index and refreshed<2 and
                time.monotonic()+1 < deadline):
                with self.refresh_lock:
                    def guard():
                        if self.stop.is_set() or self.stop_file.exists():raise RouteChanged('trial stopped')
                        if connection is not None and select.select([connection],[],[],0)[0]:
                            raise RouteChanged('pending cancellation or client close')
                        try:
                            verify_worker(pid,start)
                            verify_plugin(pid,self.plugin,self.digest)
                            current=self.kernel.state();owned=current.owner(pid,generation)
                        except (OSError,RuntimeError) as error:raise RouteChanged('process/lease identity unavailable') from error
                        owners=[item for item in current.leases if item.active and item.index==current.port]
                        if not (len(owners)==1 and owners[0]==owned and owned.active and owned.opened and owned.epoch==cursor[0] and owned.es and owned.index in (0,1) and current.source==1 and current.port==owned.index):
                            raise RouteChanged('route ownership changed')
                    try:
                        guard();self.kernel.refresh(guard)
                    except RouteChanged:return None
                refreshed+=1
            time.sleep(.1)
        raise TimeoutError('owned native ES/HD/eARC route was not confirmed')

    def serve(self,connection):
        pid=None
        registered=False
        try:
            pid,uid,_gid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid!=0:
                raise RuntimeError('only an identified native media worker may request DTS-HD')
            start=verify_worker(pid)
            verify_plugin(pid,self.plugin,self.digest)
            connection.settimeout(10)
            receive(connection,b'C5HD2\n')
            with self.client_lock:
                if pid in self.pids:
                    connection.sendall(b'BUSY\n')
                    raise RuntimeError('another control connection already represents this process')
                self.pids.add(pid)
                registered=True
            verify_worker(pid,start)
            verify_plugin(pid,self.plugin,self.digest)
            generation=self.kernel.grant(pid)
            verify_worker(pid,start)
            connection.sendall(b'ARMED\n')
            print(json.dumps({'event':'armed','pid':pid,'generation':generation}),flush=True)
            cursor=[0,b'CANCEL']
            while not self.stop.is_set():
                connection.settimeout(1)
                try:line=receive_line(connection)
                except socket.timeout:continue
                kind,epoch=parse_request(line)
                advance_cursor(cursor,kind,epoch)
                if kind==b'CANCEL':continue
                result=self.route(pid,start,generation,connection,cursor)
                if result is not None:
                    connection.sendall(f'READY {result}\n'.encode('ascii'))
                    print(json.dumps({'event':'ready','pid':pid,'generation':generation,'epoch':result}),flush=True)
        except (OSError,ValueError,RuntimeError,EOFError,TimeoutError) as error:
            # A blocked ioctl or failed health signal prohibits new writes. The
            # client closes its kernel lease; the resident name guard stays.
            if self.kernel.io_failed.is_set() or 'deadline' in str(error):self.stop.set()
            print(json.dumps({'event':'refused','pid':pid,'reason':str(error)}),flush=True)
        finally:
            connection.close()
            with self.client_lock:
                self.clients.discard(connection)
                if registered:self.pids.discard(pid)
            self.limit.release()
            print(json.dumps({'event':'closed','pid':pid,'module_retained':True}),flush=True)

    def run(self,directory):
        import fcntl
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=directory.lstat()
        if directory.is_symlink() or info.st_uid!=0 or info.st_mode & 0o077:
            raise RuntimeError('unsafe control directory')
        with (directory/'lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            endpoint=directory/'control.sock'
            if endpoint.exists():
                node=endpoint.lstat()
                if not stat.S_ISSOCK(node.st_mode) or node.st_uid!=0 or node.st_mode & 0o077:
                    raise RuntimeError('unsafe existing control endpoint')
                with socket.socket(socket.AF_UNIX) as previous:
                    previous.settimeout(.2)
                    try:previous.connect(str(endpoint))
                    except OSError as error:
                        if error.errno!=errno.ECONNREFUSED:raise
                    else:raise RuntimeError('existing live control endpoint')
                endpoint.unlink()
            listener=socket.socket(socket.AF_UNIX)
            listener.bind(str(endpoint))
            endpoint.chmod(0o600)
            listener.listen(4)
            listener.settimeout(1)
            try:
                while not self.stop.is_set() and not self.stop_file.exists():
                    try:connection,_=listener.accept()
                    except socket.timeout:continue
                    if not self.limit.acquire(blocking=False):
                        connection.close();continue
                    with self.client_lock:self.clients.add(connection)
                    threading.Thread(target=self.serve,args=(connection,),daemon=True).start()
            finally:
                listener.close()
                self.stop.set()
                with self.client_lock:
                    for connection in self.clients:
                        try:connection.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                endpoint.unlink()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--module',required=True,type=Path)
    parser.add_argument('--module-sha256',required=True)
    parser.add_argument('--module-build',required=True)
    parser.add_argument('--plugin',required=True,type=Path)
    parser.add_argument('--plugin-sha256',required=True)
    parser.add_argument('--control',type=Path,default=Path('/run/c5-dts'))
    options=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('root required')
    kernel=Kernel(options.module,options.module_sha256,options.module_build)
    service=Supervisor(kernel,options.plugin,options.plugin_sha256,options.control/'STOP_NEW_TRIALS')
    signal.signal(signal.SIGTERM,lambda *_:service.stop.set())
    signal.signal(signal.SIGINT,lambda *_:service.stop.set())
    service.run(options.control)

if __name__=='__main__':main()
