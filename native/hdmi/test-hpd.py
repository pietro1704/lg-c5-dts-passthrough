import ctypes,unittest
from unittest.mock import patch
import hpd

class Tests(unittest.TestCase):
    def run_restart(self,wrong=False,fail=False):
        writes=[]
        def ioctl(fd,request,buffer,mutate):
            if request==hpd.QUERYCAP:
                buffer[:3]=b'bad' if wrong else b'o22';buffer[16:20]=b'hdmi';return
            controls=hpd.Controls.from_buffer_copy(buffer)
            control=controls.controls.contents
            data=ctypes.cast(control.ptr,ctypes.POINTER(ctypes.c_uint32*2)).contents
            writes.append((fd,request,controls.ctrl_class,controls.count,control.id,control.size,list(data)))
            if fail:raise OSError('uncertain operation')
        with patch.object(hpd.os,'geteuid',return_value=0),patch.object(hpd.platform,'release',return_value=hpd.KERNEL),\
            patch.object(hpd.os,'open',return_value=7),patch.object(hpd.os,'close') as close,patch.object(hpd.fcntl,'ioctl',side_effect=ioctl):
            if wrong or fail:
                with self.assertRaises((RuntimeError,OSError)):hpd.restart_port1()
            else:hpd.restart_port1()
            close.assert_called_once_with(7)
        return writes
    def test_exact_native_control_port_state_and_sizes(self):
        writes=self.run_restart();self.assertEqual(len(writes),1)
        self.assertEqual(writes[0][2:],(0x980000,1,0x985909,8,[2,2]))
        self.assertEqual(writes[0][1],0xc0185648 if ctypes.sizeof(ctypes.c_void_p)==4 else 0xc0205648)
        self.assertEqual(ctypes.sizeof(hpd.Control),20)
    def test_wrong_driver_closes_without_restart(self):self.assertEqual(self.run_restart(wrong=True),[])
    def test_failed_restart_never_retries(self):self.assertEqual(len(self.run_restart(fail=True)),1)
    def test_wrong_kernel_never_opens_device(self):
        with patch.object(hpd.os,'geteuid',return_value=0),patch.object(hpd.platform,'release',return_value='wrong'),patch.object(hpd.os,'open') as opened:
            with self.assertRaises(RuntimeError):hpd.restart_port1()
            opened.assert_not_called()

if __name__=='__main__':unittest.main()
