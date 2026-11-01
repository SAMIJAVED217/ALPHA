"""Speaker volume in its own process, so a crash in the Windows audio layer costs only this.

Reads one JSON request per line on standard input, writes one JSON reply per line.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    from pycaw.pycaw import AudioUtilities

    control = AudioUtilities.GetSpeakers().EndpointVolume
    for line in sys.stdin:
        request = json.loads(line)
        if "percent" in request:
            control.SetMasterVolumeLevelScalar(int(request["percent"]) / 100, None)
        if "muted" in request:
            control.SetMute(int(bool(request["muted"])), None)
        reply = {"percent": round(control.GetMasterVolumeLevelScalar() * 100), "muted": bool(control.GetMute())}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
