"""Exercise the real ARM GStreamer plugin with partial packets and lifecycle events.
No audio device is opened: all output goes to appsink and test artifacts.
"""
import ctypes as C
import pathlib
import time
import os

os.environ["DTS_PASSTHROUGH_FILE_TEST"] = "1"

gst = C.CDLL("libgstreamer-1.0.so.0")
app = C.CDLL("libgstapp-1.0.so.0")
obj = C.CDLL("libgobject-2.0.so.0")
ptr, uint64 = C.c_void_p, C.c_uint64

def api(lib, name, restype, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = restype, list(args)
    return fn

init = api(gst, "gst_init", None, ptr, ptr)
parse = api(gst, "gst_parse_launch", ptr, C.c_char_p, ptr)
by_name = api(gst, "gst_bin_get_by_name", ptr, ptr, C.c_char_p)
state = api(gst, "gst_element_set_state", C.c_int, ptr, C.c_int)
get_bus = api(gst, "gst_element_get_bus", ptr, ptr)
pop = api(gst, "gst_bus_timed_pop_filtered", ptr, ptr, uint64, C.c_uint)
unref = api(gst, "gst_object_unref", None, ptr)
mini_unref = api(gst, "gst_mini_object_unref", None, ptr)
new_buffer = api(gst, "gst_buffer_new_allocate", ptr, ptr, C.c_size_t, ptr)
fill = api(gst, "gst_buffer_fill", C.c_size_t, ptr, C.c_size_t, ptr, C.c_size_t)
size = api(gst, "gst_buffer_get_size", C.c_size_t, ptr)
extract = api(gst, "gst_buffer_extract", C.c_size_t, ptr, C.c_size_t, ptr, C.c_size_t)
push = api(app, "gst_app_src_push_buffer", C.c_int, ptr, ptr)
eos = api(app, "gst_app_src_end_of_stream", C.c_int, ptr)
pull = api(app, "gst_app_sink_try_pull_sample", ptr, ptr, uint64)
sample_buffer = api(gst, "gst_sample_get_buffer", ptr, ptr)
pad = api(gst, "gst_element_get_static_pad", ptr, ptr, C.c_char_p)
push_event = api(gst, "gst_pad_push_event", C.c_int, ptr, ptr)
flush_start = api(gst, "gst_event_new_flush_start", ptr)
flush_stop = api(gst, "gst_event_new_flush_stop", ptr, C.c_int)
stream_start = api(gst, "gst_event_new_stream_start", ptr, C.c_char_p)
segment_new = api(gst, "gst_segment_new", ptr)
segment_init = api(gst, "gst_segment_init", None, ptr, C.c_int)
segment_event = api(gst, "gst_event_new_segment", ptr, ptr)
segment_free = api(gst, "gst_segment_free", None, ptr)
get_property = obj.g_object_get
get_property.argtypes = [ptr, C.c_char_p]
get_property.restype = None
init(None, None)
root = pathlib.Path("/tmp/codex-dts-passthrough")
stereo = (root / "fixtures/stereo.dts").read_bytes()
hd = (root / "fixtures/hd.dts").read_bytes()

class Pipeline:
    def __init__(self):
        self.pipeline = parse(
            b"appsrc name=input format=time caps=audio/x-dts ! "
            b"dtspassthrough name=bridge ! "
            b"appsink name=output sync=false wait-on-eos=false", None)
        assert self.pipeline, "parse pipeline"
        self.input = by_name(self.pipeline, b"input")
        self.output = by_name(self.pipeline, b"output")
        self.bus = get_bus(self.pipeline)
        self.srcpad = pad(self.input, b"src")
        assert state(self.pipeline, 4) != 0

    def feed(self, data):
        for start in range(0, len(data), 337):
            chunk = data[start:start + 337]
            buf = new_buffer(None, len(chunk), None)
            storage = C.create_string_buffer(chunk)
            assert fill(buf, 0, storage, len(chunk)) == len(chunk)
            assert push(self.input, buf) == 0, "push buffer"

    def wait_input(self):
        for _ in range(200):
            level = uint64()
            get_property(self.input, b"current-level-bytes", C.byref(level), ptr())
            if not level.value:
                time.sleep(0.02)
                return
            time.sleep(0.01)
        raise AssertionError("input queue failed to drain")

    def new_segment(self):
        seg = segment_new()
        segment_init(seg, 3)
        e = segment_event(seg)
        segment_free(seg)
        assert push_event(self.srcpad, e)

    def finish(self, expect_error=False):
        assert eos(self.input) == 0
        msg = pop(self.bus, 5_000_000_000, 2 if expect_error else 1)
        assert msg, "expected error" if expect_error else "EOS timed out"
        mini_unref(msg)
        result = bytearray()
        while True:
            sample = pull(self.output, 0)
            if not sample:
                break
            buf = sample_buffer(sample)
            n = size(buf)
            data = C.create_string_buffer(n)
            assert extract(buf, 0, data, n) == n
            result.extend(data.raw)
            mini_unref(sample)
        return bytes(result)

    def close(self):
        state(self.pipeline, 1)
        for value in [self.srcpad, self.bus, self.input, self.output, self.pipeline]:
            unref(value)

def run(name, action, expected_source):
    p = Pipeline()
    try:
        action(p)
        packed = p.finish()
        assert packed
        (root / (name + ".spdif")).write_bytes(packed)
        print(name + "=EOS;verify-against=" + expected_source, flush=True)
    finally:
        p.close()

def seek(p):
    p.feed(stereo[:13])
    p.wait_input()
    assert push_event(p.srcpad, flush_start())
    assert push_event(p.srcpad, flush_stop(True))
    p.new_segment()
    p.feed(hd)

def switch_identity(p):
    p.feed(hd[:17])
    p.wait_input()
    assert push_event(p.srcpad, stream_start(b"second-track"))
    p.new_segment()
    p.feed(stereo)

def cancel_restart(p):
    p.feed(hd[:23])
    p.wait_input()
    assert state(p.pipeline, 1) != 0
    assert state(p.pipeline, 4) != 0
    p.feed(stereo)

run("flush-seek", seek, "hd.dts")
run("track-identity", switch_identity, "stereo.dts")
run("cancel-restart", cancel_restart, "stereo.dts")
p = Pipeline()
try:
    p.feed(b"invalid DTS frame")
    assert not p.finish(expect_error=True), "invalid input produced output"
    print("invalid-input=EXPECTED_ERROR;output=0", flush=True)
finally:
    p.close()
os.environ.pop("DTS_PASSTHROUGH_FILE_TEST")
p = Pipeline()
try:
    p.feed(hd)
    assert not p.finish(expect_error=True), "unvalidated HD reached output"
    print("unvalidated-native-hd=EXPECTED_ERROR;output=0", flush=True)
finally:
    p.close()
