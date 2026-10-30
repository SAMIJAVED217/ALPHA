# ALPHA Technical Audit and Migration Plan

Audited in October 2026 on one Windows 11 laptop with 4 CPU cores, integrated graphics and
no dedicated GPU, using Python 3.14 and PyQt6 6.12.

Status words in this document are strict:

- **Verified** means it was run on the test machine and the result was observed.
- **Code present** means the code exists but was not exercised in this audit.
- **Missing** means a search of the source found nothing.

## 1. Existing architecture

| Layer | Where | Notes |
|---|---|---|
| Entry and session | `main.py` (`JarvisLive`) | Asyncio loop on a worker thread, one TaskGroup per session |
| Interface | `ui.py`, about 7,400 lines | One PyQt6 file: HUD, panels, overlays, settings |
| Face | `core/avatar.py`, `avatar_mesh.py`, `viseme.py` | Software QPainter head, spectral plus transcript visemes |
| Voice | `core/echo.py`, `wake_word.py`, `hotkey.py`, `audio_devices.py` | Used by the live session |
| Providers | `providers/` | Gemini Live by default, or a local STT + LLM + TTS pipeline |
| Tools | `actions/*.py`, `plugins/*.py` | Auto-discovered, each with a `TOOL` or `PLUGIN` dict |
| Safety | `core/confirm.py`, `undo.py`, `outcome.py` | Human gate, undo stack, honest result wording |
| Memory | `memory/memory_manager.py`, `long_term.json` | JSON store with search and a UI panel |
| Remote | `dashboard/` | Phone control over local HTTPS |

Python + PyQt6 stays. Nothing found justifies moving to another framework.

## 2. Working functionality (verified)

- App launches and connects to Gemini Live in about 3 seconds after the window appears.
- Typed turn returns real audio about 0.8 to 2.5 seconds after sending.
- Avatar, state changes and the activity log render in the real window.
- Confirmation banner shows, CANCEL runs nothing, and the alert clears.
- Hostile window titles do not execute in real PowerShell.
- Automated tests pass from a fresh clone: `providers/_tests.py` and `tests/test_alpha.py`.

## 3. Partially implemented or not yet verified

Code present, not exercised in this audit because each needs a person, hardware or a
side effect:

- Spoken conversation with a real microphone, barge-in and echo handling.
  Barge-in is deliberately disabled in `main.py` until tried on real hardware.
- Push-to-talk and lip-sync accuracy by eye.
- Wake word on a real microphone in a real room. Synthesised "Hey Jarvis" clips were
  detected 4 of 4 times across two voices, with 0 of 2 false alarms on a control phrase,
  but a human voice, room noise and speaker echo have not been tried.
- Screen capture, webcam, file, volume and brightness tools, and undo of real changes.
- Local pipeline against a live model. No local model server was available for testing.
- The phone dashboard. It was disabled in every run to avoid a UAC firewall prompt.
- Low-resource mode: the frame-rate unit test passes, but real runs were inconsistent
  (41% at 7 fps, then 59% at 11 fps). The second result is unexplained.

## 4. Missing against the specification

| Item | State |
|---|---|
| "Hey ALPHA" wake word | Missing. Only the pretrained `hey_jarvis` model exists; the UI says so honestly |
| OCR | Missing |
| System tray, always-on-top and compact modes | Missing |
| Secure credential storage | Missing; keys sit in `config/api_keys.json` in plain text |
| SQLite memory | Missing; JSON works, so migration is optional |
| Plugin manifests, permissions, three example plugins | Missing; plugins are single files with full user rights |
| Anthropic as a conversation engine | No catalog entry; only the `ask_claude` plugin was found |
| Vosk as a live engine | Only in the unused `core/stt.py` |
| Faster-Whisper, Piper, ElevenLabs | Provider code exists, but these optional packages were not tested |
| Installers and cross-platform testing | Missing. Only Windows was tested |

## 5. Known bugs

Fixed in this pass:

- Idle HUD used a full CPU core.
- `focus_window` reported success on macOS and with `xdotool` without checking the result.
- A second confirmation request could replace the action behind a visible banner.
- WMI connection rebuilt every 10 seconds on machines without the thermal sensor.
- `"JARVI    S"` corrupted default in `main.py`.
- **`providers/_tests.py` test [9] erased settings.** It deleted the `providers` block from
  the real `config/api_keys.json` and never restored it. The suite now runs on a throwaway
  config and fails if the real file changes by a single byte. If you ran an older copy of
  that suite, check your ENGINE & PROVIDERS settings.

Open:

- `core/stt.py`, `core/tts.py` and `core/llm_client.py` have no importers. They are dead
  code or were never wired in.
- `pynvml` prints a deprecation warning at startup.
- The microphone config name is stored as a raw device string, so it breaks if renamed.
- **Unexplained native crashes.** Windows logged heap corruption (`0xc0000374`) in some
  runs, including with the wake word off, so not every crash came from the wake model.
  Moving the detector to `core/wake_worker.py` took the in-process variant from 0 of 4
  surviving startups to 7 of 7, and 7 of 7 with the wake word off, with no crashes logged.
  The device-scan race was then fixed: all PortAudio access now goes through
  `audio_devices.pa_lock`, a second caller shares the running scan, and the session does
  this work off the event loop. A further 14 of 14 startups survived, and a live reconnect
  plus a spoken reply worked. **The race is not proven to be the crash cause.** About 1,000
  unlocked open and close cycles and 5 concurrent real scans could not reproduce a crash on
  the old code, so the cause is still unknown.
- The detector was ready about 11 seconds after startup in the last 7 runs. One earlier
  run took 38 seconds. Speech before it is ready is not heard.

## 6. Security concerns

Fixed in this pass:

- **Generated code ran with no human gate.** `code_helper` and `dev_agent` executed model
  output, and `dev_agent` also pip-installed packages the model named. All of that now
  waits for CONFIRM on the HUD, which the model cannot press. The banner shows the command
  and packages.
- **PowerShell injection in `focus_window`.** A model-supplied title sat inside a
  double-quoted string, so `$(...)` executed. It now travels in an environment variable.

Open, highest first:

1. API keys, OAuth tokens and personal memory are stored unencrypted on disk. `.gitignore`
   keeps them out of the repository, but anything that can read your disk can read them.
2. After one CONFIRM, the fix loop can rewrite and re-run code without asking again. This
   is disclosed on the banner, but it is broader than one approval of one file.
3. Plugins run in-process with full user rights and no permission model.
4. Web and document content reach the model, so prompt injection can still request tools.
   The gate limits the worst outcomes but does not cover every tool, for example
   `send_message`.
5. Launching can raise a UAC prompt to change firewall rules for the dashboard. It should
   be opt-in rather than automatic.

## 7. Dependency problems

- Development used Python 3.14 while `setup.py` says it is tested up to 3.13. Everything
  used here worked, but optional packages may lack wheels.
- `PyQt6-WebEngine` is absent, so the `astra` style uses its drawn fallback. The comment
  in `ui.py` suggests pinning PyQt6 to 6.11, which would downgrade the whole app. Not
  recommended: it adds about 200 MB for one centrepiece.
- `core/astra_web` is a third-party bundle of about 1.2 MB, which conflicts with the goal
  of an original identity. Removing it is recommended.

## 8. Licence constraint

The base project is **CC BY-NC 4.0** by FatihMakes. Attribution must stay, which is why the
footer credits it, and **commercial use is not permitted**. Any commercial release needs a
separate licence from the author.

## 9. Cross-platform limitations

`core/platform_support.py` correctly separates Wayland from X11, and reports macOS
permissions. Windows is assumed fully capable with no real probing. Only Windows has been
run. macOS and Linux behaviour is untested, and the Wayland and no-display logic is covered
by unit tests only.

## 10. Performance baseline

Measured on the real app, idle in LISTENING, repeated runs, same conditions:

| | Before | After |
|---|---|---|
| Idle frame rate | 21.5 fps | 12 fps |
| Idle CPU, share of one core | 97% | 58% |
| Share of all 8 threads | 12% | 7% |
| Talking frame rate | 30 fps | 30 fps (unchanged) |
| Memory | 340 MB | 340 MB |

One face frame still costs about 28 ms of Python and QPainter. Going much lower needs a
different approach, such as caching the idle head into a pixmap.

## 11. What changed in this pass

- `core/identity.py`: one place for name, tagline, version and wake phrase.
- User-facing rename across 30 files. Deliberately not renamed: `_AES_SALT`, firewall rule
  names, `JARVIS Uploads`, `JarvisProjects`, tool name `shutdown_jarvis`, class names,
  the `JARVIS_AI` autostart key, and `Hey Jarvis`, which is what the detector really hears.
- `core/state_theme.py`: the eight specified state colours, eased and configurable through
  `state_colors` in the config. Applies to the face, core and particle styles; the `astra`
  style keeps its own gold.
- Sticky alerts: amber while a confirmation waits, red when the connection fails.
- Lower idle pacing and a LOW RESOURCE switch in the Controls drawer.
- `tests/test_alpha.py`: run with `python -m unittest discover -s tests`.

## 12. Plan from here

Each step ends usable and tested. Order is by risk first, then by what users will notice most.

1. **Credentials and consent.** Encrypt keys with Windows DPAPI, make the firewall step
   opt-in, and gate `send_message`.
2. **Voice on real hardware.** Needs a person at the microphone: push-to-talk, echo, barge-in.
3. **Real "Hey ALPHA".** Recommended route: a local Vosk grammar spotter for "hey alpha",
   testable with synthesised speech. Training an openWakeWord model is the alternative.
4. **HUD layout and modes.** Split `ui.py`, add tray, compact and always-on-top modes, and
   collapsible panels. Decide on removing `astra_web`.
5. **Vision.** OCR, active-window and monitor selection, each with visible indicators.
6. **Local AI.** Test Ollama and Faster-Whisper with models that fit a 10 GB laptop, and
   test offline end to end. Add Anthropic as a conversation engine.
7. **Plugins.** Manifests, permissions, enable and disable, three examples, a guide.
8. **Packaging.** Windows installer first, then macOS and Linux testing on real machines.
