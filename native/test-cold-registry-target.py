# SPDX-License-Identifier: GPL-2.0-only
"""ARM target regression with real GStreamer registries; never opens audio."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROBE='''import ctypes,sys
g=ctypes.CDLL("libgstreamer-1.0.so.0")
g.gst_init(None,None)
g.gst_element_factory_find.restype=ctypes.c_void_p
g.gst_object_unref.argtypes=[ctypes.c_void_p]
for line in sys.stdin:
    factory=g.gst_element_factory_find(b"dtspassthrough")
    print(int(bool(factory)),flush=True)
    if factory:g.gst_object_unref(factory)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.bundle))
    import installer
    installer.require_target();installer.verify_bundle(args.bundle)
    children=[]
    with tempfile.TemporaryDirectory(prefix='c5-cold-registry-regression-') as directory:
        root=Path(directory);registry=root/'registry.bin'
        shutil.copy2(args.baseline,registry)
        def child():
            env=dict(os.environ,GST_PLUGIN_PATH_1_0='/usr/lib/gstreamer-1.0:/mnt/lg/res/lglib/gstreamer-1.0',
                GST_REGISTRY_1_0=str(registry),GST_REGISTRY_UPDATE='no')
            process=subprocess.Popen([sys.executable,'-u','-c',PROBE],env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            children.append(process);return process
        def found(process):
            process.stdin.write('probe\n');process.stdin.flush()
            import select
            if not select.select([process.stdout],[],[],5)[0]:raise RuntimeError('registry probe timed out')
            result=process.stdout.readline().strip()
            if result not in ('0','1'):raise RuntimeError('registry probe failed')
            return result=='1'
        try:
            old=child()
            assert not found(old),'baseline already includes native plugin'
            installer.seed_cold_registry(args.bundle,registry,root/'seed')
            assert not found(old),'existing process must demonstrate cached registry'
            assert found(child()),'new boot process cannot discover seeded DTS factory'
            installer.restore_cold_registry(registry,root/'seed')
            assert not found(child()),'rollback failed to restore original cold registry'
            assert registry.read_bytes()==args.baseline.read_bytes()
            print('PASS real GStreamer: stale worker reproduced; seeded cold start discovers DTS; rollback verified')
        finally:
            for process in children:
                process.stdin.close()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
