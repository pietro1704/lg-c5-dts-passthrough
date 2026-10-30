"""Volatile C5 capability configuration and journaled EDID RAM override."""
from pathlib import Path
import base64,hashlib,json,os,re,subprocess,time
from luna_rpc import luna
from edid import validate,parse_dump,constrain_dts,wire_text,supported_sad

KEY='tv.model.edidType'
NODE=Path('/proc/lg/hdmi20/port1/edid_file_rw')
CACHE=Path('/proc/lg/hdmi20/port1/edid')


def digest(data):return hashlib.sha256(data).hexdigest()


def save(path,value):
    next_path=path.with_suffix('.next')
    fd=os.open(next_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream,indent=2)
    next_path.replace(path)


def setup_model(run):
    journal=Path(run)/'model-policy.json'
    before=luna('luna://com.webos.service.config/getConfigs',{'configNames':[KEY]})['configs'][KEY]
    if before not in ('TrueHD','TrueHD+dts'):raise RuntimeError('unexpected model audio policy')
    database=Path('/var/preferences/configd_db.json');fingerprint=digest(database.read_bytes())
    if not journal.exists():save(journal,{'original':before,'candidate':'TrueHD+dts','factory_sha256':fingerprint,'phase':'prepared'})
    if before!='TrueHD+dts':luna('luna://com.webos.service.config/setConfigs',{'configs':{KEY:'TrueHD+dts'}})
    after=luna('luna://com.webos.service.config/getConfigs',{'configNames':[KEY]})['configs'][KEY]
    if after!='TrueHD+dts' or digest(database.read_bytes())!=fingerprint:
        raise RuntimeError('model policy readback/database preservation failed')
    record=json.loads(journal.read_text());record['phase']='verified_applied';save(journal,record)


def receiver_sad():
    response=luna('luna://com.webos.service.audiooutput/quality/getSettings',{'category':'external_arc'})
    values=[s['value'] for s in response.get('settings',[]) if s.get('category')=='external_arc']
    if len(values)!=1 or values[0].get('arcActivation')!='eARC':return None
    try:return supported_sad(values[0].get('advancedEarcCapability',[]))
    except ValueError:return None


def hardware_edid():
    values=re.findall(r'0x([0-9a-fA-F]{2})(?=\s|$)',NODE.read_text())
    if len(values)!=512:raise RuntimeError('unexpected hardware EDID encoding')
    data=bytes(int(x,16) for x in values);validate(data)
    if data!=parse_dump(CACHE.read_text()):raise RuntimeError('hardware/cache mismatch; no write')
    return data


def ps5_connected():
    values={}
    for line in Path('/proc/lg/hdmi20/port1/pkt').read_text().splitlines():
        m=re.search(r'\[SPD\]\[ B([0-9]{2})\].*Hex\[\s*0x([0-9a-fA-F]{2})',line)
        if m:values[int(m[1])]=int(m[2],16)
    return (bytes(values.get(i,0) for i in range(1,9)).rstrip(b'\0 ')==b'SCEI' and
        bytes(values.get(i,0) for i in range(9,25)).rstrip(b'\0 ')==b'PS5')


def renegotiate(run):
    """Request one native HPD restart; an uncertain operation is never retried."""
    journal=Path(run)/'hpd-policy.json'
    state=json.loads(journal.read_text()) if journal.exists() else {'phase':'idle','requests':[]}
    if state['phase']=='prepared':raise RuntimeError('unconfirmed prior HPD restart; stop mutations')
    now=time.monotonic();recent=[t for t in state['requests'] if now-t<60]
    if len(recent)>=3:raise RuntimeError('HDMI renegotiation rate limit; stop mutations')
    state={'phase':'prepared','requests':recent+[now]};save(journal,state)
    process=subprocess.Popen(['/usr/bin/python3',str(Path(__file__).with_name('hpd.py'))],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:out,err=process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate();raise RuntimeError('uncertain HPD operation; retain journal and stop mutations')
    if process.returncode:raise RuntimeError('HPD request failed; retain journal and stop mutations')
    state['phase']='requested';save(journal,state)


def apply_edid(run,notify_source=False):
    """Repair volatile audio EDID, optionally notifying the source via native HPD."""
    hpd_journal=Path(run)/'hpd-policy.json'
    if notify_source and hpd_journal.exists() and json.loads(hpd_journal.read_text())['phase']=='prepared':
        raise RuntimeError('prior HDMI renegotiation unconfirmed; no further EDID writes')
    if not ps5_connected():return False
    sad=receiver_sad()
    if sad is None:return False
    before=hardware_edid();candidate=constrain_dts(before,sad)
    if candidate==before:return True
    time.sleep(.1)
    if hardware_edid()!=before or not ps5_connected():return False
    path=Path(run)/'edid-policy.json'
    state={'phase':'prepared','backup':base64.b64encode(before).decode(),'candidate':base64.b64encode(candidate).decode(),
           'backup_sha256':digest(before),'candidate_sha256':digest(candidate)}
    save(path,state)
    # The proc setter always returns count, so only exact hardware/cache
    # readback can prove success. An uncertain result stops further mutation.
    data=wire_text(candidate)
    p=subprocess.Popen(['/usr/bin/python3','-c',
        'import os,sys;d=sys.stdin.buffer.read();f=os.open(sys.argv[1],os.O_WRONLY);assert os.write(f,d)==len(d);os.close(f)',str(NODE)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:out,err=p.communicate(data,timeout=2)
    except subprocess.TimeoutExpired:
        p.terminate();raise RuntimeError('uncertain EDID operation; stop mutations and retain journal')
    if p.returncode or hardware_edid()!=candidate:raise RuntimeError('EDID readback failed; retain journal')
    state['phase']='verified_applied';save(path,state)
    if notify_source:
        renegotiate(run)
        state['hpd_restart_requested']=True;save(path,state)
    return True
