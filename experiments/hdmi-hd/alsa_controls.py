"""Use target libasound in the broker process, preserving its kernel identity."""
import ctypes as C


class AlsaControls:
    def __init__(self,library='libasound.so.2'):
        self.lib=C.CDLL(library);self.handle=C.c_void_p()
        signatures={
            'snd_ctl_open':([C.POINTER(C.c_void_p),C.c_char_p,C.c_int],C.c_int),
            'snd_ctl_close':([C.c_void_p],C.c_int),
            'snd_ctl_elem_id_malloc':([C.POINTER(C.c_void_p)],C.c_int),
            'snd_ctl_elem_id_free':([C.c_void_p],None),
            'snd_ctl_elem_id_set_numid':([C.c_void_p,C.c_uint],None),
            'snd_ctl_elem_info_malloc':([C.POINTER(C.c_void_p)],C.c_int),
            'snd_ctl_elem_info_free':([C.c_void_p],None),
            'snd_ctl_elem_info_set_id':([C.c_void_p,C.c_void_p],None),
            'snd_ctl_elem_info':([C.c_void_p,C.c_void_p],C.c_int),
            'snd_ctl_elem_info_get_type':([C.c_void_p],C.c_int),
            'snd_ctl_elem_info_get_count':([C.c_void_p],C.c_uint),
            'snd_ctl_elem_info_get_min':([C.c_void_p],C.c_long),
            'snd_ctl_elem_info_get_max':([C.c_void_p],C.c_long),
            'snd_ctl_elem_value_malloc':([C.POINTER(C.c_void_p)],C.c_int),
            'snd_ctl_elem_value_free':([C.c_void_p],None),
            'snd_ctl_elem_value_set_id':([C.c_void_p,C.c_void_p],None),
            'snd_ctl_elem_read':([C.c_void_p,C.c_void_p],C.c_int),
            'snd_ctl_elem_write':([C.c_void_p,C.c_void_p],C.c_int),
            'snd_ctl_elem_value_get_integer':([C.c_void_p,C.c_uint],C.c_long),
            'snd_ctl_elem_value_set_integer':([C.c_void_p,C.c_uint,C.c_long],None)}
        for name,(arguments,result) in signatures.items():
            function=getattr(self.lib,name);function.argtypes=arguments;function.restype=result
        self.check(self.lib.snd_ctl_open(C.byref(self.handle),b'hw:0',0))

    @staticmethod
    def check(result):
        if result<0:raise OSError(-result,'ALSA control operation failed')

    def values(self,numid,expected_count,write=None):
        if numid not in (20,21,22,92):raise ValueError('unapproved control')
        identity=C.c_void_p();info=C.c_void_p();value=C.c_void_p()
        try:
            for name,pointer in [('id',identity),('info',info),('value',value)]:
                self.check(getattr(self.lib,'snd_ctl_elem_'+name+'_malloc')(C.byref(pointer)))
            self.lib.snd_ctl_elem_id_set_numid(identity,numid)
            self.lib.snd_ctl_elem_info_set_id(info,identity)
            self.check(self.lib.snd_ctl_elem_info(self.handle,info))
            if self.lib.snd_ctl_elem_info_get_type(info)!=2 or self.lib.snd_ctl_elem_info_get_count(info)!=expected_count:
                raise RuntimeError('control ABI changed')
            self.lib.snd_ctl_elem_value_set_id(value,identity)
            if write is None:
                self.check(self.lib.snd_ctl_elem_read(self.handle,value))
                return [self.lib.snd_ctl_elem_value_get_integer(value,i) for i in range(expected_count)]
            if len(write)!=expected_count:raise ValueError('wrong control arity')
            minimum=self.lib.snd_ctl_elem_info_get_min(info);maximum=self.lib.snd_ctl_elem_info_get_max(info)
            if any(type(v) is not int or not minimum<=v<=maximum for v in write):raise ValueError('invalid integer control value')
            for i,v in enumerate(write):self.lib.snd_ctl_elem_value_set_integer(value,i,v)
            self.check(self.lib.snd_ctl_elem_write(self.handle,value))
        finally:
            for name,pointer in [('value',value),('info',info),('id',identity)]:
                if pointer.value:getattr(self.lib,'snd_ctl_elem_'+name+'_free')(pointer)

    def close(self):
        if self.handle.value:
            self.check(self.lib.snd_ctl_close(self.handle));self.handle=C.c_void_p()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
