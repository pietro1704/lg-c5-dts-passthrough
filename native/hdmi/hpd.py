"""One native C5 HDMI hot-plug restart after a verified PS5 EDID repair."""
import ctypes,fcntl,json,os,platform
from pathlib import Path

KERNEL='5.4.268-329.ptl4tv.5'
CONTROL_ID=0x985909
QUERYCAP=0x80685600

class Control(ctypes.Structure):
    _pack_=1
    _fields_=[('id',ctypes.c_uint32),('size',ctypes.c_uint32),('reserved',ctypes.c_uint32),('ptr',ctypes.c_uint64)]

class Controls(ctypes.Structure):
    _fields_=[('ctrl_class',ctypes.c_uint32),('count',ctypes.c_uint32),('error_idx',ctypes.c_uint32),
        ('request_fd',ctypes.c_int32),('reserved',ctypes.c_uint32),('controls',ctypes.POINTER(Control))]

def restart_port1():
    """Use the same port/state/control as HdmiPortCtrl::restart_hpd()."""
    if os.geteuid()!=0 or platform.release()!=KERNEL:raise RuntimeError('wrong C5 target')
    fd=os.open('/dev/video12',os.O_RDWR|os.O_CLOEXEC)
    try:
        caps=bytearray(104);fcntl.ioctl(fd,QUERYCAP,caps,True)
        identity=tuple(bytes(caps[a:b]).split(bytes([0]))[0] for a,b in [(0,16),(16,48)])
        if identity!=(b'o22',b'hdmi'):raise RuntimeError('wrong HDMI driver identity')
        # This V4L2 enum is one based. v4l2_hdmi20_process_ctrl subtracts
        # one before HDMI20_Module_Set_RestartHPD: enum2 is proc port1.
        data=(ctypes.c_uint32*2)(2,2)
        control=Control(CONTROL_ID,ctypes.sizeof(data),0,ctypes.addressof(data))
        controls=Controls(0x980000,1,0,0,0,ctypes.pointer(control))
        request=(3<<30)|(ctypes.sizeof(controls)<<16)|(ord('V')<<8)|72
        buffer=bytearray(ctypes.string_at(ctypes.addressof(controls),ctypes.sizeof(controls)))
        # A timeout must be enforced by the owning parent; never retry an
        # uncertain vendor ioctl. This function issues exactly one restart.
        fcntl.ioctl(fd,request,buffer,True)
    finally:os.close(fd)

def main():
    from runtime_settings import hardware_edid,ps5_connected,receiver_sad
    from edid import advertised_formats
    if not ps5_connected() or receiver_sad() is None or 7 not in advertised_formats(hardware_edid()):
        raise RuntimeError('PS5, DTS receiver and verified hardware/cache EDID required')
    restart_port1()
    print(json.dumps({'hpd_restart_requested':True,'driver_port':1,'audio_verified':False}))

if __name__=='__main__':main()
