"""Package only owned components; no firmware, target libraries, media or logs."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,shutil,subprocess,tempfile

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('global_install',ROOT/'global_install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def verified_modules(donor,expected=None):
    manifest=json.loads((donor/'hdmi-manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('kernel')!=installer.KERNEL:
        raise RuntimeError('wrong module donor target')
    for name,digest in manifest['files'].items():
        if '/' in name or name in ('.','..') or (donor/name).is_symlink() or installer.sha(donor/name)!=digest:
            raise RuntimeError('module donor changed')
    for name in ('broker-gate.ko','auto-route.ko'):
        if name not in manifest['files'] or (expected and manifest['files'][name]!=expected[name]):
            raise RuntimeError('unverified module donor')
    return {name:donor/name for name in ('broker-gate.ko','auto-route.ko')}


def build(native,output,project,objcopy,hd_donor,hdmi_donor=None):
    if output.exists() or output.is_symlink():raise RuntimeError('output already exists')
    native_manifest=json.loads((native/'manifest.json').read_text())
    for name,digest in native_manifest['files'].items():
        if installer.sha(native/name)!=digest:raise RuntimeError('native donor changed')
    hd_modules=verified_modules(hd_donor,{
        'broker-gate.ko':'add58c20c8d71a6e52a54a669aaa2fed06854e9792031ba6a1b1aa85e4643a92',
        'auto-route.ko':'68e85ab974a5b8ca11fa61ce2eae37153e3d655ce21e103afdfe5a742aa09982'})
    temporary=Path(tempfile.mkdtemp(prefix='.global-package-',dir=output.parent))
    try:
        mapping={'global_install.py':ROOT/'global_install.py',
            'NOTICE':project/'NOTICE',
            'LICENSES/GPL-2.0-only.txt':project/'LICENSES/GPL-2.0-only.txt',
            'LICENSES/LGPL-2.1-or-later.txt':project/'LICENSES/LGPL-2.1-or-later.txt'}
        for name in native_manifest['files']:mapping['native/'+name]=native/name
        mapping['native/manifest.json']=native/'manifest.json'
        for name in ['hdmi_supervisor.py','alsa_controls.py','luna_rpc.py','runtime_settings.py','edid.py','hpd.py']:
            mapping['hdmi/'+name]=ROOT/name
        mapping['hdmi/broker-gate.ko']=ROOT/'module/c5_hdmi_broker_gate.ko'
        mapping['hdmi/auto-route.ko']=ROOT/'module/c5_hdmi_auto_route.ko'
        if hdmi_donor:
            for name,path in verified_modules(hdmi_donor).items():mapping['hdmi/'+name]=path
        hd_source=project/'experiments/hdmi-hd'
        for name in installer.HD_FILES:
            mapping['hdmi_hd/'+name]=(hd_modules[name] if name.endswith('.ko') else
                ROOT/name if name=='hdmi_hd_supervisor.py' else hd_source/name)
        for name,source in mapping.items():
            if source.is_symlink() or not source.is_file():raise RuntimeError('nonregular build input')
            destination=temporary/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        for component in ['hdmi','hdmi_hd']:
            for name in ['broker-gate.ko','auto-route.ko']:
                subprocess.run([str(objcopy),'--strip-debug',str(temporary/component/name)],check=True)
        (temporary/'README.md').write_text('''# LG C5 global DTS passthrough candidate

Target: rooted LG C5, firmware33.31.68, kernel5.4.268-329.ptl4tv.5, compatible
existing dts_restore-2.0 and an eARC receiver advertising DTS support.

Native component: candidate original Nuvio1.2.9 DTS-HD MA path, with route
confirmation before preroll, no synthetic latency and executable identity
checks. This native candidate still requires hardware acceptance.
HDMI components: DTS core5.1/48kHz and
DTS-HD HBR8-channel/192kHz on the
PS5 connected to UI HDMI3 / driver port1. It connects the original compressed
path to eARC; it does not decode, downmix or transcode the DTS payload.
The two transport channels contain six encoded channels.

The receiver and sound were confirmed on the PS5. A decoder stop/start cycle
and recovery from a real failed session passed. The center channel has not
yet been isolated in the game. The native six-channel clip was tested earlier.

The separate HDMI-HD proof was confirmed with DTS-HD MA and sound on a Blu-ray.
This package adds its controller to boot and requires an eARC receiver
advertising eight-channel/192kHz DTS-HD MA. This combined candidate has NOT
passed its first reboot or the complete native/Dolby/source-switching matrix.
Other HDMI ports and other firmware are not claimed.

Install the next-boot hook on the supported TV as root:

    python3 global_install.py dry-run
    python3 global_install.py install-boot

Installation stages immutable owned files and preserves current playback.
It seeds the persistent GStreamer registry before the next cold boot so pooled
media workers discover DTS before the Homebrew hook runs. A guarded backup is
restored by disable-boot; current runtime registry bindings are preserved.
The first full reboot must be validated before declaring persistence working.
The hook starts the shared native bridge, native plugin integration and HDMI
controllers, including the separately scoped HDMI-HD controller. Model
capability changes and EDID changes are volatile RAM changes;
factory provisioning and firmware/EEPROM are not written. EDID preserves the
existing video descriptors and advertises only the supported core5.1/48kHz mode.

To disable future activation while preserving the current session:

    python3 /home/root/c5-dts-global/current/global_install.py disable-boot

Audio controllers stay resident after use until reboot. Never force unloads or
restart broad HDMI/audio services. A broker/HDMI-service crash fails closed and
can require reboot; do not stack another controller over an existing instance.

Runtime status is under /run/c5-dts-global and native control remains at
/run/c5-dts/control.sock. This archive contains owned code and binaries only.
Required target GStreamer/FFmpeg/ALSA libraries are supplied by the TV and
compatible restoration patch, and are not redistributed. See NOTICE/LICENSES.
''')
        hdmi_files=['broker-gate.ko','auto-route.ko','hdmi_supervisor.py','alsa_controls.py','luna_rpc.py','runtime_settings.py','edid.py','hpd.py']
        hdmi={'schema':1,'kernel':installer.KERNEL,'files':{p:installer.sha(temporary/'hdmi'/p) for p in hdmi_files}}
        (temporary/'hdmi/hdmi-manifest.json').write_text(json.dumps(hdmi,indent=2,sort_keys=True)+'\n')
        hd={'schema':1,'kernel':installer.KERNEL,
            'files':{p:installer.sha(temporary/'hdmi_hd'/p) for p in sorted(installer.HD_FILES)}}
        (temporary/'hdmi_hd/hdmi-manifest.json').write_text(json.dumps(hd,indent=2,sort_keys=True)+'\n')
        manifest={'schema':1,'kernel':installer.KERNEL,'reboot_audio_verified':False,
            'files':{p:installer.sha(temporary/p) for p in sorted(installer.PAYLOAD)},
            'acceptance_pending':['combined HDMI-HD boot','Nuvio MA/Dolby/source switches','center isolated','DTS2.0 and lifecycle matrix']}
        (temporary/'global-manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        installer.verify(temporary);temporary.rename(output)
        return manifest
    except BaseException:shutil.rmtree(temporary);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--objcopy',type=Path,required=True)
    parser.add_argument('--hd-donor',type=Path,required=True)
    parser.add_argument('--hdmi-donor',type=Path);a=parser.parse_args()
    result=build(a.native,a.output,a.project,a.objcopy,a.hd_donor,a.hdmi_donor)
    print(json.dumps({'package':'verified','files':len(result['files']),'reboot_audio_verified':False}))
