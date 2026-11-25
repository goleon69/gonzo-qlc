# Third-party software

Gonzo is released under the GNU General Public License v3.0 or later (see `LICENSE`).
The packaged apps include the following third-party components, each under its own license.

| Component | Use in Gonzo | License |
|---|---|---|
| [Vosk](https://github.com/alphacep/vosk-api) (Alpha Cephei) | offline speech recognition | Apache-2.0 |
| [vosk-model-small-it-0.22](https://alphacephei.com/vosk/models) (Alpha Cephei) | Italian speech model | Apache-2.0 |
| [python-osc](https://github.com/attwad/python-osc) | sends OSC messages to QLC+ | Unlicense (public domain) |
| [PyYAML](https://github.com/yaml/pyyaml) | reads `config.yaml` | MIT |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) | microphone input | MIT |
| [PortAudio](https://www.portaudio.com/) | audio library used by sounddevice | MIT-style (PortAudio license) |
| [pywebview](https://github.com/r0x0r/pywebview) | native window (macOS, Windows) | BSD-3-Clause |
| [CFFI](https://github.com/python-cffi/cffi) | used by Vosk and sounddevice | MIT |
| [PyObjC](https://github.com/ronaldoussoren/pyobjc) (macOS) | used by pywebview on macOS | MIT |
| [pythonnet](https://github.com/pythonnet/pythonnet) (Windows) | used by pywebview on Windows | MIT |
| [Python](https://www.python.org/) | runtime bundled in the app | PSF License |
| [PyInstaller](https://pyinstaller.org/) bootloader | starts the packaged app | GPL-2.0 with bootloader exception |

The full license texts are available at the linked project pages.

QLC+ is a separate program (Apache-2.0) and is not included in Gonzo.
