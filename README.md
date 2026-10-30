# LG C5 DTS / DTS-HD MA passthrough

Experimental TV-wide integration for the original Nuvio and native GStreamer players. The bridge parses and packs the original DTS bitstream; it never decodes, downmixes or transcodes it. Every payload, including the MA extension, is checked byte for byte.

**Confirmed on 27 October 2026:** Nuvio 1.2.9 playing a selected DTS-HD MA track; the user confirmed DTS-HD MA on the receiver and correct sound. An earlier native-player clip verified all six channels. Supported target: LG C5, firmware33.31.68, kernel `5.4.268-329.ptl4tv.5`, rooted webOS, existing compatible `dts_restore-2.0` integration and eARC receiver. Other models/firmware are refused or unverified.

This is an experimental release. First reboot activation, extended pause/seek/track stability, measured A/V sync and Dolby regression remain pending. A DTS lossy label while the receiver shows MA after an HD session is under investigation. Receiver labels alone do not prove the encoded profile.

## Install on the supported TV

The source tree also contains an experimental global installer in
`native/hdmi/`. It stages the native passthrough controller and a separate HDMI
controller in one immutable Homebrew boot package. DTS core5.1/48kHz from the
PS5 on UI HDMI3 (driver port1) has been confirmed with sound after a full TV
reboot. EDID repairs request a native HDMI renegotiation, with bounded retries;
opening TV settings suspends controller actions until the selected source returns.

`experiments/hdmi-hd/` contains the separately scoped DTS-HD HDMI controller.
DSP codec6 and mode4/HBR recovered non-PCM eight-channel/192kHz transport on a
Blu-ray trial; the user confirmed DTS-HD MA with sound. The global candidate
now packages this controller for boot, checks the receiver's MA capabilities,
and uses the current guarded native supervisor. The combined boot package,
Dolby preservation and complete source/receiver lifecycle matrix still need
hardware acceptance. Game DTS startup delay remains under investigation.

The published v0.2.0 archive is the native-only release described below.

The global installer also seeds the persistent GStreamer registry for the next
cold boot, retaining a verified backup. Media workers preloaded before the
Homebrew hook otherwise cache a catalog without the DTS bridge and can omit
the DTS track after reopening Nuvio. Seeding preserves current playback;
`disable-boot` restores the backup only if the seeded disk registry still matches.
An interrupted preparation can be retried with `prepare-cold-boot`. A complete
reboot and Nuvio reopen/AC3/DTS acceptance are still required for this correction.

Extract the release archive on the TV as root. It contains only this project's plugin, controller, scripts, manifest and license/documentation files. Required GStreamer/FFmpeg runtime libraries come from the compatible restoration patch; they are not redistributed.

```sh
python3 installer.py dry-run
python3 installer.py install-boot
```

`install-boot` verifies hashes/architectures, stages an immutable version under `/home/root/c5-dts-passthrough` and creates `zz-c5-dts-passthrough` in the Homebrew init directory. The hook waits for the original restoration overrides. Installation preserves current playback and does not restart the TV. **First reboot still needs validation.**

To disable future activation while preserving the current session:

```sh
python3 /home/root/c5-dts-passthrough/current/installer.py disable-boot
```

For an explicitly requested temporary trial, use `activate --allow-hardware-trial`. `deactivate` restores only bindings owned by that activation and refuses active leases. A controller used for audio stays resident until reboot; never force an unload or restart broad audio services.

## How it works

The plugin checks complete DTS packets, packs IEC61937 and marks native HD buffers as ES. This C5 consumes BE16 carrier words; transport words are swapped after validating the payload. MA5.1 uses an eight-channel/S16/192kHz transport carrier, preserving six encoded source channels.

The exact-build controller associates renderer0/1 with authenticated process leases, generation and epoch. It connects the owned compressed path to eARC and protects codec7 callback handling. SHA256 fingerprints guard firmware-specific locations. No firmware instructions are distributed.

Startup retains originals after sending caps and waits for route confirmation before the first output buffer completes preroll. It adds no artificial latency and never shifts original audio PTS. The previous two-second latency could underflow the vendor audio/video clock during track changes. Route confirmation has a1.5-second deadline; failure sends no original audio to an unconfirmed route. Pause suspends the lease; resume requires fresh proof. Confirmation before preroll still requires acceptance on the native hardware path.

The supervisor verifies the requesting executable against the firmware's canonical media-worker inode, its process start time and the mapped plugin. A renamed diagnostic process cannot obtain a native lease. Identity is checked again after the handshake, before route refresh and before READY. The packager rejects stale supervisor copies and the retired frozen-supervisor option.

The supervisor rejects ambiguous owners, stale epochs, changed plugin inodes and unexpected formats. Vendor-command failure stops routing writes. Refresh revalidates ownership before writing; the vendor ALSA interface offers no atomic compare-and-set across unrelated applications. Direct vendor-sink diagnostics bypass the platform resource manager and must not run alongside normal playback or HDMI; use the isolated memory-sink harness below.

## Build and test

For clock regressions while a real controller is resident, build the test-only
adapter with `C5_CLOCK_OUTPUT=<existing-directory> bash native/build-clock-isolation.sh`.
Run `native/test-clock-target.py --isolated --require-preroll` on the target with
`LD_PRELOAD=<adapter-directory>/clock-isolation.so` and
`C5_CLOCK_SOCKET=/tmp/c5-clock-<unique-test>/control.sock`, plus the existing
module/plugin/fixture arguments shown by `--help`. This runs the unchanged
production plugin with real GStreamer clocks and appsinks; only the lease and
control socket are simulated. It never opens an audio device or loads/unloads
a kernel module. It is not proof of the vendor renderer's readiness. Delayed
READY, fragmented input, timeout, cancellation, disconnect and stale/future
epochs are separate cases. Never preload this adapter in a real player.

Obtain verified, target-matched prerequisites separately. No firmware, kernel image, libraries, sysroot, toolchain or media is included. Build only this project's targets:

```sh
# Prebuilt Zig in toolchain/zig, headers/libs in sysroot/, FFmpeg5 headers.
DTS_NATIVE_SESSION=1 bash build-plugin.sh

# Separately supplied, already prepared C5 kernel trees and toolchain.
C5_KERNEL_SOURCE=/path/to/kernel C5_KERNEL_OBJECTS=/path/to/prepared-output bash native/build-module.sh

python3 native/checks.py
node --test tests/verify-iec.test.mjs
python3 scripts/check-publication.py
```

Without `DTS_NATIVE_SESSION=1`, the plugin keeps diagnostic rank NONE and blocks unverified native HD. `native/module/build-id.h` identifies this released controller; changed sources require a new identity and target validation. Build scripts do not supply vendor kernel prerequisites or promise bit-identical output across toolchains.

`native/audit-module.py` audits ABI/vermagic/imports against separately supplied local artifacts. `native/package-candidate.py` packages owned files and strips debug. Without exact target clock evidence it blocks activation. `native/test-clock-target.py` explicitly runs only on the ARM32 target with memory sinks, without opening an audio device. Host CI never runs device tests.

The target regression reproduced original-buffer drops before the fix. Afterward, all1,867,776 carrier bytes and original PTS survived; a second sink on the same clock agreed within0.05ms. Fragmentation passed; READY beyond the deadline released zero original bytes. These checks supplement the receiver confirmation and do not replace hardware lifecycle acceptance.

## Publication and licenses

The deny-by-default ignore file and publication check permit only reviewed text sources. Credentials, addresses, device evidence, media and investigation notes remain private. Release binaries contain only this project's code. See `NOTICE`, `LICENSES/` and source SPDX identifiers.

Related projects: [dts_restore](https://github.com/lgstreamer/dts_restore) and [lg-g5-dts](https://github.com/dakenf/lg-g5-dts). Addresses from other models must never be applied to the C5.
