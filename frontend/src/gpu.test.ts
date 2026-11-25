import { describe, expect, it } from "vitest";
import { gpuView } from "./Gpu";

const base = { label: "", detail: "", percent: null, engines: {}, vram_mb: null, system_percent: null, adapter: null, provider: null, measurable: true, encoder: null, encoder_on_gpu: null, fell_back: false, job: null };

describe("gpuView", () => {
  it("shows the engine's words and a colour class per state", () => {
    expect(gpuView(null)).toEqual({ cls: "unknown", text: "GPU …" });
    expect(gpuView({ ...base, state: "active", label: "GPU active · 41%" })).toEqual({ cls: "active", text: "GPU active · 41%" });
    expect(gpuView({ ...base, state: "ready", label: "GPU ready · idle" }).cls).toBe("ready");
    expect(gpuView({ ...base, state: "cpu", label: "GPU not used" }).cls).toBe("cpu");
  });
});
