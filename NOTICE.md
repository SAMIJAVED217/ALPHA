# Notice

ALPHA is a modified version of **Mark LVI** by **FatihMakes**.

- Original project: https://github.com/FatihMakes/Mark-LVI
- Licence: Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)
- Licence text: [LICENSE](LICENSE) and https://creativecommons.org/licenses/by-nc/4.0/legalcode

**Commercial use is not permitted.** This licence covers the whole project, including the
changes listed below. FatihMakes has not endorsed this modified version.

## What was changed

These changes were made in October 2026. [docs/AUDIT.md](docs/AUDIT.md) has the details.

- Renamed the product to ALPHA, with the name held in `core/identity.py`.
- Put generated code execution and package installs behind a human confirmation.
- Closed a PowerShell injection path in window focusing.
- Added state colours, sticky alerts, lower idle CPU use and a low-resource mode.
- Moved the wake word detector into its own process, `core/wake_worker.py`.
- Put all PortAudio access behind one lock and removed the duplicate device scan.
- Added the tests in `tests/` and a corrected `.gitignore`.

## Third-party code

`core/astra_web` is a bundled third-party interface under the MIT licence. Its licence and
credits are kept beside it in `LICENSE-ASTRA.txt` and `CREDITS-ASTRA.md`.
