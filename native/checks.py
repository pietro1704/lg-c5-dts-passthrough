"""Host-only regressions. Never accesses the TV or loads a kernel module."""
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parent
subprocess.run([sys.executable,str(root/'test-ready-receive.py')],check=True)
subprocess.run([sys.executable,str(root/'test-preparation.py')],check=True)
for name in ('supervisor','installer','configuration','package'):
    subprocess.run([sys.executable,str(root/('test-'+name+'.py'))],check=True)
for name in ('alsa-controls','luna-rpc','supervisor','hd-supervisor','global-install','runtime-settings','hpd'):
    subprocess.run([sys.executable,str(root/'hdmi'/('test-'+name+'.py'))],check=True)
experiment=root.parent/'experiments/hdmi-hd'
subprocess.run([sys.executable,str(experiment/'test-hd-controller.py')],check=True)
with tempfile.TemporaryDirectory(prefix='c5-native-tests-') as directory:
    for name in ('lease-policy','lease-state','ready-protocol','release-policy'):
        output=str(Path(directory)/name)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/('test-'+name+'.c')),'-o',output],check=True)
        subprocess.run([output],check=True)
    for name,source in [('hdmi-input-policy',root/'hdmi/test-input-policy.c'),
                        ('hdmi-hd-policy',experiment/'test-policy.c')]:
        output=str(Path(directory)/name)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(source),'-o',output],check=True)
        subprocess.run([output],check=True)
subprocess.run([sys.executable,str(root/'test-clock-target.py'),'--help'],stdout=subprocess.DEVNULL,check=True)
