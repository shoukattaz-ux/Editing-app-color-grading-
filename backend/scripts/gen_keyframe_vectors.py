"""Regenerates shared/keyframe_vectors.json: expected interpolation results used by BOTH the Python and TypeScript tests."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evp.keyframes import EASINGS, Keyframe, value_at

OUT = Path(__file__).resolve().parents[2] / "shared" / "keyframe_vectors.json"


def build() -> dict:
    cases = []

    def case(name, kfs, ts):
        cases.append({"name": name, "keyframes": [{"time": k.time, "value": k.value, "easing": k.easing} for k in kfs],
                      "samples": [{"t": t, "v": round(value_at(kfs, t), 12)} for t in ts]})
    ts = [-1, 0, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.4, 3, 5]
    case("single", [Keyframe(1, 0.7)], ts)
    for e in EASINGS:
        case("two_" + e, [Keyframe(0.5, 0.0, e), Keyframe(2.5, 1.0)], ts)
    case("three_mixed", [Keyframe(0, 1, "ease_in"), Keyframe(1, 3, "hold"), Keyframe(2, -2, "ease_out"), Keyframe(4, 0)], [i / 4 for i in range(-2, 20)])
    case("descending", [Keyframe(0, 5, "linear"), Keyframe(1, -5)], ts)
    case("dense", [Keyframe(i * 0.1, (i * 37 % 11) / 10, ["linear", "ease_in_out", "ease_in", "ease_out", "hold"][i % 5]) for i in range(30)], [i / 17 for i in range(60)])
    return {"cases": cases}


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", OUT)
