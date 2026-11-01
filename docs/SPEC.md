# ALPHA Specification

ALPHA is a personal AI desktop companion for Windows first. It hears, speaks, sees,
remembers and controls the computer, with an animated presence as its centrepiece.

This document says what ALPHA must do. It deliberately does not say how.

## Original work

1. Every source file is written fresh in this repository.
2. Do not copy code, prompts, interface text or file layouts from any existing assistant.
3. Check a dependency's licence before adding it, and record it in `docs/DEPENDENCIES.md`.
4. Do not add anything GPL, AGPL or non-commercial. PySide6 is used for the interface, under the LGPL.

## Non-negotiable rules

- Never claim something happened unless its result was observed.
- The model cannot approve its own actions. Irreversible actions need a person to press a
  control in the interface, and the model has no route to that control.
- Never run model-written code or shell commands without that approval. Never put
  model-supplied text inside a command string.
- API keys go in the Windows Credential Manager, never in a plain file.
- Personal data stays on this machine unless the user sends it to a provider.
- Microphone, camera and screen use must always be visible on screen, and off by default.
- Tests never touch real user data, real settings or real credentials. They run against
  temporary locations, and a test fails if the real settings file changes.
- `.gitignore` comments go on their own lines, because Git treats inline text as part of
  the pattern.
- Before any push, check what will be uploaded for secrets and personal data.

## Lessons to design in from the start

These are general engineering requirements. Solve them in your own way.

- Native machine-learning libraries can crash the whole process. Run them in a separate
  process, so a crash there costs only that feature.
- The audio library is not safe to use from several threads at once. All device access must
  be serialised, and enumeration must never run twice at the same time.
- Never block the event loop on audio devices or model loading.
- Importing heavy libraries from two threads at once can fail. Check readiness from files
  on disk, without importing the library.
- Software-painted animation is expensive. Idle must stay well below 25% of one CPU core,
  and rendering must stop while the window is hidden or minimised.
- Target machine: 4-core laptop, integrated graphics, about 10 GB of RAM, no NVIDIA GPU.

## Milestone 1: a small app that really works

Finish this before anything else. It is complete only when each item has been observed
working in the running app.

1. **Window.** A PySide6 window with an original futuristic design: dark background and
   one animated presence in the centre, not a face. It reacts to state and audio level.
2. **States and colours.** Idle blue, listening cyan, thinking violet, speaking electric
   blue, warning amber, error red. Colours ease between states and are configurable.
3. **Conversation.** Text input and a live spoken reply through the official Google GenAI
   SDK and the Gemini Live API. Check the current documentation for model names.
4. **Push-to-talk.** Hold a key or an on-screen button to talk. The microphone stays closed
   otherwise, and a clear indicator shows when it is open.
5. **Settings.** First-run setup asks for the Gemini key and stores it in the Credential
   Manager. Choose microphone and speakers. Settings persist.
6. **Safety gate.** One real example of a confirmed action: creating a file only after the
   user presses CONFIRM, with cancel and expiry both tested.
7. **Tests.** Automated tests for all of the above, runnable with `python -m pytest`
   or `unittest`, and passing from a fresh clone with no settings or keys present.
8. **Packaging basics.** A desktop shortcut with an original icon.

## Later milestones

Order may change, but each must end usable and tested.

- Local wake word, with its own detector and a licence that permits this use. Changed by the owner on
  2026-11-01: "hey", "hi", or simply speaking while hands-free is on.
- Natural voice: interruption, echo handling, silence and microphone errors.
- Speech recognition and text-to-speech options that work offline.
- Provider choice: Gemini Live, Ollama, LM Studio, OpenAI, Anthropic, OpenRouter, each
  declaring what it really supports. Unsupported features are never shown as available.
- Screen vision with OCR and webcam, both user-activated with persistent indicators.
- Computer control: apps, volume, brightness, windows, keyboard and mouse, all reporting
  real results and platform limits.
- Undo for reversible actions, by button and by voice.
- Long-term memory the user can view, search, edit, export and delete. Sensitive details
  are not saved silently, and only relevant memories are sent to a provider.
- Plugins with manifests and declared permissions. No plugin gets unrestricted access.
- Interface modes: fullscreen, standard, compact floating, always-on-top, and system tray.
- Languages: English, Korean, Urdu and Hindi, with availability shown per engine.
- An animated expressive face with accurate lip sync, designed from scratch.
- Windows installer, then macOS and Linux testing.

## Owner's changes on 2026-11-01

The owner replaced three rules above for their own computer. The microphone starts on with
hands-free, and turning it off is remembered. Everyday actions asked for directly run without
CONFIRM. An on-screen talk button is no longer provided. CONFIRM stays for sending to other people,
blind mouse clicks, shutting down, sensitive words, and anything that follows reading outside text.
The model still has no route to the CONFIRM control. The lip-synced face is dropped: bending a
picture looked bad.

## Personality

Calm, intelligent, confident, friendly and concise, with occasional humour. ALPHA understands
context and speaks naturally. It never pretends to have seen or done something.

## Licence

Proprietary for now: see `LICENSE`. The owner will choose the final licence before the
project is published, so keep every dependency compatible with that choice.
