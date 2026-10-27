"""Host-only regressions. Never accesses the TV or loads a kernel module."""
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parent
for name in ('supervisor','installer','configuration','package'):
    subprocess.run([sys.executable,str(root/('test-'+name+'.py'))],check=True)
with tempfile.TemporaryDirectory(prefix='c5-native-tests-') as directory:
    for name in ('lease-policy','lease-state','ready-protocol','release-policy'):
        output=str(Path(directory)/name)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/('test-'+name+'.c')),'-o',output],check=True)
        subprocess.run([output],check=True)
subprocess.run([sys.executable,str(root/'test-clock-target.py'),'--help'],stdout=subprocess.DEVNULL,check=True)
