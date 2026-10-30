"""Bounded Luna client with a controlling PTY, including detached boot use."""
import errno,json,os,select,signal,time


def terminal_command(argv,timeout=2):
    child,master=os.forkpty()
    if child==0:
        try:
            os.closerange(3,65536)
            os.execv(argv[0],argv)
        except BaseException:os._exit(127)
    output=bytearray();status=None;deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            available=select.select([master],[],[],min(.05,max(0,deadline-time.monotonic())))[0]
            if available:
                try:data=os.read(master,4096)
                except OSError as error:
                    if error.errno!=errno.EIO:raise
                    data=b''
                if not data:break
                output.extend(data)
                if len(output)>65536:raise RuntimeError('Luna response too large')
            if status is None:
                exited,code=os.waitpid(child,os.WNOHANG)
                if exited:status=code
            elif not available:break
        else:raise TimeoutError('Luna deadline; action result is unconfirmed')
        if status is None:
            for _ in range(10):
                exited,code=os.waitpid(child,os.WNOHANG)
                if exited:status=code;break
                time.sleep(.005)
        if status is None or not os.WIFEXITED(status) or os.WEXITSTATUS(status)!=0:
            raise RuntimeError('Luna client did not exit successfully')
        return output.decode('utf-8').replace('\r\n','\n')
    finally:
        if status is None:
            try:os.kill(child,signal.SIGTERM)
            except ProcessLookupError:pass
            try:os.waitpid(child,os.WNOHANG)
            except ChildProcessError:pass
        os.close(master)


def luna(uri,payload):
    allowed={
        'luna://com.webos.applicationManager/getForegroundAppInfo',
        'luna://com.webos.service.config/getConfigs',
        'luna://com.webos.service.config/setConfigs',
        'luna://com.webos.service.audiooutput/quality/getSettings'}
    if uri not in allowed:raise ValueError('unapproved Luna endpoint')
    text=terminal_command(['/usr/bin/luna-send','-n','1','-w','1500',uri,json.dumps(payload,separators=(',',':'))])
    records=[json.loads(line) for line in text.splitlines() if line.startswith('{')]
    if len(records)!=1 or records[0].get('returnValue') is not True:
        raise RuntimeError('Luna operation was not confirmed')
    return records[0]
