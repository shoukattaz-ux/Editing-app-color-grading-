"""Does the AI run on the graphics card, and does it give the same answer as on the processor?

    python scripts/gpu_check.py        (needs internet the first time: downloads the 14 MB matting model and the 5 MB upscaler weights)

Prints the graphics adapters, the provider chain, then runs the real matting network and the real upscaling network on the selected device
and on the CPU and compares the results. Exits non-zero if they disagree or the device fails.
"""
import os, sys, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from evp.ml import device, models  # noqa: E402


def frame(w=320, h=240, seed=1):
    rng = np.random.default_rng(seed)
    f = np.full((h, w, 3), (60, 170, 70), np.uint8)
    f[60:200, 100:220] = (200, 150, 125)
    return np.clip(f.astype(int) + rng.integers(-6, 7, f.shape), 0, 255).astype(np.uint8)


def main() -> int:
    print("adapters:", device.adapters())
    print("onnxruntime providers:", device._available())
    print("chain:", device.provider_chain())
    print("describe:", device.describe()["running_on"])
    from evp.ml import matting, upscale
    img = frame()
    results = {}
    for choice in ("auto", "cpu"):
        device.set_choice(choice)
        t = time.time()
        m = matting.RVM()
        a, fg = m(img, 0.5)
        a2, _ = m(img, 0.5)                                     # second frame uses the recurrent state
        results[choice] = (a2, m.provider, time.time() - t)
        print(f"matting on {choice:5s}: provider={m.provider} {results[choice][2]:.1f}s alpha mean={a2.mean():.3f}")
    d = float(np.abs(results["auto"][0] - results["cpu"][0]).mean())
    print(f"matting difference GPU vs CPU: mean |alpha| diff = {d:.4f}")
    if d > 0.05: print("FAIL: matting differs too much"); return 1
    small = img[:96, :128]
    outs = {}
    for choice in ("auto", "cpu"):
        device.set_choice(choice)
        sess = upscale.session("general", 0.0)
        t = time.time(); sr = upscale.run_net(sess, small, 4); dt = time.time() - t
        outs[choice] = sr; print(f"upscale on {choice:5s}: provider={sess.get_providers()[0]} {dt:.1f}s out={sr.shape}")
    du = float(np.abs(outs["auto"] - outs["cpu"]).mean())
    print(f"upscale difference GPU vs CPU: mean abs diff = {du:.4f} (0..1 scale)")
    if du > 0.02: print("FAIL: upscaling differs too much"); return 1
    print("GPU CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
