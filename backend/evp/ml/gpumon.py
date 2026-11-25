"""Is the graphics card really working? Honest, measured evidence instead of a label.

Windows exposes per-process GPU counters (the same numbers Task Manager > Performance > GPU shows). Once every ~2 s - and only while the
window is asking - one PowerShell call adds up the utilisation and dedicated video memory of THIS engine and its child processes
(ffmpeg's hardware encoder). That is proof the work is on the card: not "a GPU is selected", but "this app is using 41 % of the Radeon now".
Other systems cannot measure it this way; they only report which device is configured and what the AI runtime really got.
"""
from __future__ import annotations
import json, os, sys, threading, time
from typing import Callable

GPU_ENCODER_SUFFIXES = ("_nvenc", "_qsv", "_amf", "_videotoolbox")
IDLE_BELOW = 1.0            # percent of an engine: below this the card is not meaningfully working for us

_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$root=%d
$ids=New-Object System.Collections.Generic.HashSet[int]
[void]$ids.Add($root)
$procs=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
do { $grew=$false
  foreach($p in $procs){ if($ids.Contains([int]$p.ParentProcessId) -and -not $ids.Contains([int]$p.ProcessId)){ [void]$ids.Add([int]$p.ProcessId); $grew=$true } }
} while($grew)
$mine=@{}; $total=@{}
foreach($c in (Get-Counter '\GPU Engine(*)\Utilization Percentage').CounterSamples){
  if($c.InstanceName -match 'pid_(\d+)_.*engtype_(.+)$'){
    $procId=[int]$Matches[1]; $et=$Matches[2]
    $total[$et]=[double]$total[$et]+$c.CookedValue
    if($ids.Contains($procId)){ $mine[$et]=[double]$mine[$et]+$c.CookedValue }
  }
}
$mem=0.0
foreach($c in (Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples){
  if($c.InstanceName -match 'pid_(\d+)_'){ if($ids.Contains([int]$Matches[1])){ $mem+=$c.CookedValue } }
}
@{mine=$mine; total=$total; vram_bytes=$mem} | ConvertTo-Json -Compress
"""


def parse_sample(text: str) -> dict | None:
    """PowerShell JSON -> {percent, engines, system_percent, vram_mb}. The busiest engine counts (3D / Compute / VideoEncode ... as in Task Manager)."""
    try:
        d = json.loads(text.strip().splitlines()[-1])
        mine = {k: round(float(v), 1) for k, v in (d.get("mine") or {}).items()}
        total = {k: float(v) for k, v in (d.get("total") or {}).items()}
        return {"percent": round(max(mine.values(), default=0.0), 1), "engines": mine,
                "system_percent": round(max(total.values(), default=0.0), 1), "vram_mb": round(float(d.get("vram_bytes") or 0) / 1048576, 1)}
    except Exception:
        return None


def _run_powershell(script: str) -> str:
    from .. import proc
    r = proc.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
    return r.stdout or ""


class GpuMonitor:
    def __init__(self, runner: Callable[[str], str] | None = None, windows: bool | None = None, pid: int | None = None):
        self._runner = runner or _run_powershell
        self.measurable = (sys.platform == "win32") if windows is None else windows
        self._pid = pid or os.getpid()
        self._lock = threading.Lock()
        self._sample: dict | None = None
        self._when = 0.0
        self._asked = 0.0
        self._error = ""
        self._thread: threading.Thread | None = None

    # ---- sampling (background, only while somebody is looking)
    def poke(self) -> None:
        self._asked = time.time()
        if self.measurable and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name="gpu-monitor", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while time.time() - self._asked < 15:
            self.sample_once()
            time.sleep(1.0)

    def sample_once(self) -> None:
        try:
            s = parse_sample(self._runner(_PS % self._pid))
            with self._lock:
                if s is None: self._error = "the Windows GPU counters returned nothing (old graphics driver?)"
                else: self._sample, self._when, self._error = s, time.time(), ""
        except Exception as e:
            with self._lock: self._error = f"{type(e).__name__}: {e}"[:160]

    # ---- the answer the window shows
    def snapshot(self, device: dict, jobs: list[dict] | None = None) -> dict:
        self.poke()
        with self._lock:
            s, when, err = (dict(self._sample) if self._sample else None), self._when, self._error
        fresh = s is not None and time.time() - when < 8
        running = [j for j in (jobs or []) if j.get("state") == "running"]
        job = running[0] if running else None
        enc = (job or {}).get("encoder")
        enc_gpu = bool(enc) and enc.endswith(GPU_ENCODER_SUFFIXES)
        provider = device.get("provider") or ""
        fell_back = bool(device.get("fell_back"))
        gpu_selected = provider not in ("", "CPU") and not fell_back
        name = device.get("wanted") if gpu_selected else None
        pct = s["percent"] if (fresh and s) else None
        if not gpu_selected and not (pct and pct >= IDLE_BELOW):
            state = "cpu"
            reason = ("the graphics card could not be used and the processor took over" if fell_back else
                      "processor-only mode is selected (Settings > AI device)" if device.get("choice") == "cpu" else
                      "no usable graphics card was found for AI (Windows needs the DirectML runtime)" if sys.platform == "win32" else
                      "this computer has no GPU runtime for the AI models")
        elif pct is not None and pct >= IDLE_BELOW:
            state, reason = "active", ""
        elif not self.measurable and enc_gpu and running:
            state, reason = "active", ""                       # cannot measure here; the hardware encoder is named by the running job itself
        else:
            state, reason = "ready", ""
        if state == "active" and pct is not None: label = f"GPU active · {pct:.0f}%"
        elif state == "active": label = "GPU in use"
        elif state == "ready": label = "GPU ready · idle" if self.measurable else "GPU selected"
        else: label = "GPU not used"
        parts = []
        if name: parts.append(f"Graphics card: {name} (AI runs on it via {provider})." if provider == "Dml" else f"AI runs on: {name or provider}.")
        if pct is not None: parts.append(f"Measured now: this app uses {pct:.0f}% of the card" + (f" ({', '.join(f'{k} {v:.0f}%' for k, v in s['engines'].items() if v >= 0.5)})" if s["engines"] else "") + f" and {s['vram_mb']:.0f} MB of video memory. Whole card: {s['system_percent']:.0f}%.")
        elif self.measurable: parts.append("Live usage is being measured…" if not err else f"Live usage could not be read: {err}.")
        else: parts.append("Live usage percentages are only available on Windows; this shows the configured device.")
        if enc: parts.append(f"Export encoder: {enc}" + (" (on the GPU)." if enc_gpu else " (on the processor)."))
        if reason: parts.append("Why: " + reason + ".")
        if fell_back: parts.append("Settings > AI device lets you pick a card or run the check again.")
        return {"state": state, "label": label, "detail": " ".join(parts), "percent": pct, "engines": (s or {}).get("engines", {}) if fresh else {},
                "vram_mb": (s or {}).get("vram_mb") if fresh else None, "system_percent": (s or {}).get("system_percent") if fresh else None,
                "adapter": name, "provider": provider or None, "measurable": self.measurable, "encoder": enc, "encoder_on_gpu": enc_gpu if enc else None,
                "fell_back": fell_back, "job": (job or {}).get("current")}


_MON: GpuMonitor | None = None


def monitor() -> GpuMonitor:
    global _MON
    if _MON is None: _MON = GpuMonitor()
    return _MON
