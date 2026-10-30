# LG C5 DTS / DTS-HD MA passthrough

Experimental TV-wide integration for the original Nuvio and native GStreamer players. The bridge parses and packs the original DTS bitstream; it never decodes, downmixes or transcodes it. Every payload, including the MA extension, is checked byte for byte.

**Confirmed on 30 October 2026:** the final native artifact activated automatically after a full TV reboot. Nuvio 1.2.9 passed first playback, DD → DTS-HD MA, three consecutive pause/resume cycles and closing/reopening with a new authenticated native media worker. Video advanced without a media error; the DSP reported DTS-HD MA at eight-channel/192kHz transport with normal lipsync restored. An earlier native-player clip verified all six channels. Supported target: LG C5, firmware33.31.68, kernel `5.4.268-329.ptl4tv.5`, rooted webOS, existing compatible `dts_restore-2.0` integration and eARC receiver. Other models/firmware are refused or unverified.

This is experimental. The final native artifact also passed target clock tests and real Nuvio seek checks before reboot. The preceding package remains retained for recovery. Extended lifecycle stability, measured A/V sync and the complete Dolby/source matrix remain pending. A DTS lossy label while the receiver shows MA after an HD session is under investigation. Receiver labels alone do not prove the encoded profile.

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
and uses the current guarded native supervisor. On 30 October 2026, the same
booted package passed Nuvio acceptance, PS5 game DTS and Blu-ray DD → DTS-HD MA;
the user confirmed normal sound. The game capture retained DTS at48kHz and a
locked4K signal for30 seconds. The Blu-ray capture retained video lock across
the format change and reached DTS-HD MA with an eight-channel/192kHz carrier.
The complete source/receiver lifecycle matrix and game startup delay remain
unmeasured; these checks do not establish every channel or title combination.

PS5 games use the console's audio-format priority setting. Blu-ray playback has
its own audio-format setting: select Bitstream in that player and the desired
disc language/track. A brief PCM receiver label can appear during the transition;
in the captured DD → MA switch, MA output appeared about one second after the
first observed DTS-HD input sample. Sustained PCM is not successful MA acceptance.

### Instalar ou remover na TV

Baixe **[lg-c5-dts-passthrough-v0.4.0-tv.tar.gz](https://github.com/pietro1704/lg-c5-dts-passthrough/releases/tag/v0.4.0)**
(não escolha “Source code”), copie para a TV e, no terminal SSH como root:

```sh
tar -xzf lg-c5-dts-passthrough-v0.4.0-tv.tar.gz
cd lg-c5-dts-passthrough-v0.4.0
sh install.sh
```

Após “Instalação concluída”, reinicie a TV manualmente.
Para desfazer, na mesma pasta: **`sh remove.sh`**, depois reinicie manualmente.
A remoção desativa esta integração, restaura o registro anterior e preserva o
`dts_restore` e as versões guardadas. Não faz reset de fábrica.

**Pré-requisitos:** LG C5 no firmware/kernel acima, root, Homebrew, Python 3,
`dts_restore-2.0` compatível já instalado e receiver eARC/DTS. Não precisa
reinstalar se já está funcionando; outro pacote instalado é preservado e recusado.

[Guia curto de instalação](INSTALL.md) ·
[Wiki](https://github.com/pietro1704/lg-c5-dts-passthrough/wiki) ·
[Testes e limites](docs/wiki/Testing.md)

Os scripts novos são testados no CI; esta release reutiliza os binários aceitos
anteriormente e não foi testada novamente em TV real. A v0.3.0 permanece somente
de fontes; o binário da v0.2.0 não contém as correções atuais.

## How it works

The plugin checks complete DTS packets, packs IEC61937 and marks native HD buffers as ES. This C5 consumes BE16 carrier words; transport words are swapped after validating the payload. MA5.1 uses an eight-channel/S16/192kHz transport carrier, preserving six encoded source channels.

The exact-build controller associates renderer0/1 with authenticated process leases, generation and epoch. It connects the owned compressed path to eARC and protects codec7 callback handling. SHA256 fingerprints guard firmware-specific locations. No firmware instructions are distributed.

Startup retains originals after sending caps and waits for route confirmation before the first output buffer completes preroll. Merely waiting deadlocks the vendor lipsync stage: route confirmation itself needs renderer preparation. The plugin identifies the sole owned LG sink by exact library SHA256, ARM32 instance size, negotiated caps and the current lease. It temporarily disables that sink's lipsync during bounded neutral feeds through the already-loaded vendor API, outside GStreamer preroll. READY restores the saved lipsync mode before any original buffer is released. Cancellation restores the saved context without restarting playback; a replaced handle is never written.

Preparation adds no artificial latency and never shifts original audio PTS. The previous two-second latency could underflow the vendor audio/video clock during track changes. Route confirmation has a1.5-second deadline and neutral preparation is capped at16 blocks of32768 bytes; failure sends no original audio to an unconfirmed route. Pause suspends the lease; resume requires fresh proof. Firmware updates require revalidation of the private adapter ABI.

All first-buffer preparation enters the pause-aware READY loop. Previously,
preparing before this loop could treat a normal pause or seek as a decode error.
Cancellation during preparation/restoration retains originals for a fresh epoch;
flush returns its cancellation status, while genuine preparation failures still
fail closed. Host regressions exercise the actual entry and receive-loop code;
restoring the obsolete pre-wait call makes the paused-entry regression fail.

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
# Include the target-matched prebuilt libdl.so.2 in sysroot/usr/lib/.
DTS_NATIVE_SESSION=1 bash build-plugin.sh

# Separately supplied, already prepared C5 kernel trees and toolchain.
C5_KERNEL_SOURCE=/path/to/kernel C5_KERNEL_OBJECTS=/path/to/prepared-output bash native/build-module.sh

python3 native/checks.py
node --test tests/verify-iec.test.mjs
python3 scripts/check-publication.py
```

`native/test-preparation.py` extracts the actual preparation and restoration
functions and runs them under ASan/UBSan with modeled vendor, tree, caps and lease
calls. It covers successful preparation/restoration, repeated sessions, partial
feed and vendor failures, cancellation, epoch/handle changes, ABI/library mismatch
and bounded feeds. `--emit <path>` emits the same fixture for a separately built
ARM32 run. These models do not establish real vendor readiness or A/V sync.

Without `DTS_NATIVE_SESSION=1`, the plugin keeps diagnostic rank NONE and blocks unverified native HD. `native/module/build-id.h` identifies this released controller; changed sources require a new identity and target validation. Build scripts do not supply vendor kernel prerequisites or promise bit-identical output across toolchains.

`native/audit-module.py` audits ABI/vermagic/imports against separately supplied local artifacts. `native/package-candidate.py` packages owned files and strips debug. Without exact target clock evidence it blocks activation. `native/test-clock-target.py` explicitly runs only on the ARM32 target with memory sinks, without opening an audio device. Host CI never runs device tests.

The target regression reproduced original-buffer drops before the fix. The final native artifact preserves all1,867,776 carrier bytes and original PTS with delayed READY and fragmented input, while a second sink on the same clock stays within the0.05-second acceptance bound. Timeout, cancellation, disconnect and stale/future epochs release zero originals. These checks supplement the receiver confirmation and do not replace hardware lifecycle acceptance.

## Publication and licenses

The deny-by-default ignore file and publication check permit only reviewed text sources. Credentials, addresses, device evidence, media and investigation notes remain private. Release binaries contain only this project's code. See `NOTICE`, `LICENSES/` and source SPDX identifiers.

Related projects: [dts_restore](https://github.com/lgstreamer/dts_restore) and [lg-g5-dts](https://github.com/dakenf/lg-g5-dts). Addresses from other models must never be applied to the C5.
