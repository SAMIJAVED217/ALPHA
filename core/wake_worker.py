"""
core/wake_worker.py — the wake model, in a process of its own.

Run by core/wake_word.py as:  python -m core.wake_worker <model> <threshold>

Loading the model inside the app corrupted the Windows heap on most startups
(exception 0xc0000374), which killed the whole assistant with no Python error.
Here a native crash costs the wake word and nothing else.

stdin:  raw 16 kHz mono int16 PCM. End of input means the app has gone, so this
        process exits on its own and cannot be left running.
stdout: one word per line: READY, DETECT, or ERROR <reason>.

Nothing here touches the network: audio stays on this machine.
"""
import sys

CHUNK = 1280       # 80 ms, the frame size openwakeword is built around


def main() -> int:
    model_name, threshold = sys.argv[1], float(sys.argv[2])
    try:
        import numpy as np
        from openwakeword.model import Model
        model = Model(wakeword_models=[model_name], inference_framework="onnx")
    except Exception as e:
        print(f"ERROR {str(e)[:160]}", flush=True)
        return 1

    print("READY", flush=True)
    stdin = sys.stdin.buffer
    while True:
        data = stdin.read(CHUNK * 2)
        if len(data) < CHUNK * 2:
            return 0
        scores = model.predict(np.frombuffer(data, dtype=np.int16))
        if max(scores.values(), default=0.0) >= threshold:
            print("DETECT", flush=True)
            model.reset()      # one utterance must not fire twice


if __name__ == "__main__":
    sys.exit(main())
