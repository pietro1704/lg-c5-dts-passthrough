import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('alsa',Path(__file__).with_name('alsa_controls.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Function:
    def __init__(self,call):self.call=call
    def __call__(self,*args):return self.call(*args)

class Library:
    def __init__(self):
        self.next=100;self.allocated={};self.freed=[];self.writes=[];self.closes=0
        self.count=4;self.type=2;self.read_error=0;self.write_error=0
        functions={
            'snd_ctl_open':self.allocate,'snd_ctl_close':self.close,
            'snd_ctl_elem_id_malloc':self.allocate,'snd_ctl_elem_id_free':self.free,
            'snd_ctl_elem_info_malloc':self.allocate,'snd_ctl_elem_info_free':self.free,
            'snd_ctl_elem_value_malloc':self.allocate,'snd_ctl_elem_value_free':self.free,
            'snd_ctl_elem_id_set_numid':lambda p,v:self.allocated[p.value].update(numid=v),
            'snd_ctl_elem_info_set_id':self.copy_identity,'snd_ctl_elem_value_set_id':self.copy_identity,
            'snd_ctl_elem_info':lambda *_:0,'snd_ctl_elem_info_get_type':lambda _:self.type,
            'snd_ctl_elem_info_get_count':lambda _:self.count,
            'snd_ctl_elem_info_get_min':lambda _:0,'snd_ctl_elem_info_get_max':lambda _:2147483647,
            'snd_ctl_elem_read':lambda *_:self.read_error,
            'snd_ctl_elem_write':self.write,
            'snd_ctl_elem_value_get_integer':lambda p,i:[0,0,1,48][i],
            'snd_ctl_elem_value_set_integer':lambda p,i,v:self.allocated[p.value].setdefault('values',{}).update({i:v})}
        for name,fn in functions.items():setattr(self,name,Function(fn))
    def allocate(self,p,*args):
        self.next+=1;p._obj.value=self.next;self.allocated[self.next]={};return 0
    def free(self,p):self.freed.append(p.value);del self.allocated[p.value]
    def close(self,p):self.closes+=1;del self.allocated[p.value];return 0
    def copy_identity(self,a,b):self.allocated[a.value].update(self.allocated[b.value])
    def write(self,handle,value):
        self.writes.append(self.allocated[value.value].copy());return self.write_error

class Tests(unittest.TestCase):
    def setUp(self):
        self.library=Library();self.patch=patch.object(m.C,'CDLL',return_value=self.library);self.patch.start()
        self.addCleanup(self.patch.stop)
    def test_read_and_write_preserve_numid_and_integer_positions(self):
        with m.AlsaControls() as controls:
            self.assertEqual(controls.values(20,4),[0,0,1,48])
            controls.values(20,4,[1,48,0,0])
        self.assertEqual(self.library.writes,[{'numid':20,'values':{0:1,1:48,2:0,3:0}}])
        self.assertEqual(self.library.allocated,{})
    def test_foreign_control_is_rejected_without_an_ioctl(self):
        with m.AlsaControls() as controls:
            with self.assertRaises(ValueError):controls.values(83,4,[1,48,0,0])
        self.assertEqual(self.library.writes,[])
    def test_abi_change_never_writes(self):
        for kind,count in [(1,4),(2,5)]:
            self.library.type=kind;self.library.count=count
            with m.AlsaControls() as controls:
                with self.assertRaises(RuntimeError):controls.values(20,4,[1,48,0,0])
        self.assertEqual(self.library.writes,[]);self.assertEqual(self.library.allocated,{})
    def test_bad_values_never_write_and_release_allocations(self):
        for values in [[1,48],[1,-1,0,0],[1,2**31,0,0],[1,True,0,0]]:
            with m.AlsaControls() as controls:
                with self.assertRaises(ValueError):controls.values(20,4,values)
        self.assertEqual(self.library.writes,[]);self.assertEqual(self.library.allocated,{})
    def test_driver_failure_and_cancellation_release_owned_resources(self):
        self.library.read_error=-5
        with self.assertRaises(OSError):
            with m.AlsaControls() as controls:controls.values(20,4)
        self.assertEqual(self.library.allocated,{})
        with self.assertRaises(KeyboardInterrupt):
            with m.AlsaControls() as controls:raise KeyboardInterrupt()
        self.assertEqual(self.library.allocated,{})
    def test_write_error_is_reported_and_closed(self):
        self.library.write_error=-22
        with self.assertRaises(OSError):
            with m.AlsaControls() as controls:controls.values(20,4,[1,48,0,0])
        self.assertEqual(self.library.allocated,{})

if __name__=='__main__':unittest.main()
