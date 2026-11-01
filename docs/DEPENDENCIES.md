# Dependencies and licences

Checked on 2026-10-30 from the metadata of the packages actually installed, not from memory.
Python is **3.12** (ALPHA accepts 3.11 to 3.13). PySide6 and the Google SDK both support it,
and `sounddevice` ships a ready-built Windows audio library for it.

## Direct dependencies

| Package | Version | Licence | Used for |
| --- | --- | --- | --- |
| PySide6-Essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | The interface. ALPHA takes it under **LGPL-3.0**. |
| google-genai | 2.34.0 | Apache-2.0 | The official Gemini SDK. |
| sounddevice | 0.5.6 | MIT | Microphone and speakers. Includes PortAudio, which is MIT-style. |
| numpy | 2.5.4 | BSD-3-Clause, plus 0BSD, MIT, Zlib, CC0-1.0 for bundled parts | Sound calculations. |
| keyring | 25.7.0 | MIT | Reaches the Windows Credential Manager. |
| xAI API | service | Owner's own account | Optional. `ask_grok` sends a question through httpx only when the owner asks for Grok. |
| Windows voices | built in | Windows | Offline replies use whichever voice Windows has for the language. Nothing is downloaded. |
| Open-Meteo | service | Free, no key | Weather. Only the city name and its position are sent. |
| Google News RSS | service | Free, no key | Headlines. Only the topic is sent. |
| pypdf | 6.21.4 | BSD-3-Clause | Reads text from PDFs. |
| pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0 | Draws PDF pages as pictures when Gemini will not read a PDF directly. |
| vosk | 0.3.45 | Apache-2.0 | Offline "hey" and "hi" listening, in its own process. |
| faster-whisper | 1.2.1 | MIT | Offline speech recognition, in its own process. |
| faster-whisper-base | Systran | MIT | The Whisper speech model. Downloaded by the installer, not stored in this repository. |
| vosk-model-small-en-us-0.15 | 0.15 | Apache-2.0 | The speech model. Downloaded by the installer, not stored in this repository. |
| pycaw | 20260927 | MIT | Reading and setting the speaker volume. |
| comtypes | 1.4.17 | MIT | Needed by pycaw. |
| psutil | 7.2.2 | BSD-3-Clause | The live readings on the HUD. |
| mss | 10.2.0 | MIT | Screen capture when you turn it on. |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | Webcam capture when you turn it on. Bundles FFmpeg under LGPL as a separate library. |
| pytest | 9.2.0 | MIT | Tests only. Not part of the app. |

## Packages these bring in

| Licence | Packages |
| --- | --- |
| Apache-2.0 | distro, flatbuffers, google-auth, hf-xet, huggingface_hub, requests, tenacity, tokenizers |
| Apache-2.0 OR BSD | cryptography, packaging |
| Apache-2.0 / MIT | sniffio |
| BSD | av, click, colorama, fsspec, httpcore, httpcore2, httpx, httpx2, protobuf, idna, pyasn1, pyasn1_modules, pycparser, Pygments, pywin32-ctypes, websockets |
| MIT | annotated-types, anyio, charset-normalizer, ctranslate2, filelock, h11, onnxruntime, PyYAML, srt, truststore, iniconfig, jaraco.classes, jaraco.context, jaraco.functools, more-itertools, pluggy, pydantic, pydantic_core, typing-inspection, urllib3 |
| MIT-0 | cffi |
| MIT and MPL-2.0 | tqdm |
| MPL-2.0 | certifi |
| PSF-2.0 | typing_extensions |
| LGPL-3.0-only OR GPL | shiboken6, the runtime half of PySide6 |

## Notes for the owner

- **Nothing here is GPL-only, AGPL or non-commercial.** PySide6 and shiboken6 list GPL only as
  an alternative, and ALPHA uses the LGPL option. PyQt is not used.
- **LGPL means:** ALPHA may stay closed-source. If ALPHA is ever given to other people, they
  must be told it uses Qt and PySide6 under the LGPL, be given the licence text, and be able to
  swap in their own build of those libraries. Keeping them as separate files, as now, makes
  that easy. The Windows installer milestone must keep them separate too.
- **MPL-2.0 (certifi, tqdm)** covers only those packages' own files, which ALPHA does not change.
- **Wake word choice:** openWakeWord's code is Apache-2.0, but its pre-trained models are
  CC BY-NC-SA, which is non-commercial, so it was not used.
- **av** bundles FFmpeg libraries under the LGPL as separate files. ALPHA passes raw sound and does
  not use them directly.
- **Offline voice** uses the voices built into Windows, so none are distributed with ALPHA. Piper was
  not used, because its current version is GPL.
- **Gemini itself** is an online service with its own terms, separate from the SDK's licence.
- ALPHA's own icon is drawn by `alpha/icon.py`, and no third-party artwork or fonts are included.
