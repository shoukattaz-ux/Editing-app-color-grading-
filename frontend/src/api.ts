import type { Blueprint, Schema } from "./model";

export interface Bridge {
  backend: { port: number; token: string };
  pickFile?: (opts: { title: string; filters?: { name: string; extensions: string[] }[] }) => Promise<string | null>;
  pickFolder?: (title: string) => Promise<string | null>;
  isElectron?: boolean;
  pathFor?: (f: File) => string;
  app?: { info: () => Promise<{ version: string; packaged: boolean; logDir: string }>; checkUpdates: () => Promise<void>; openLogs: () => Promise<void> };
}
declare global { interface Window { evp?: Bridge } }

export function connection(): { base: string; token: string } | null {
  if (window.evp?.backend) return { base: `http://127.0.0.1:${window.evp.backend.port}`, token: window.evp.backend.token };
  const q = new URLSearchParams(location.search);       // browser/dev mode: ?port=8765&token=...
  if (q.get("port") && q.get("token")) return { base: `http://127.0.0.1:${q.get("port")}`, token: q.get("token")! };
  return null;
}

export class ApiError extends Error { constructor(msg: string, public status: number, public body?: any) { super(msg); } }

export interface Issue { level: string; code: string; message: string; op_id?: string | null; path?: string | null }
export interface Validation { ok: boolean; errors: Issue[]; warnings: Issue[]; plan: { order: number; id: string; type: string; start: number; end: number; alpha_source: string | null }[]; video: VideoInfo | null; repaired?: Blueprint | null }
export interface VideoInfo { duration: number; fps: number; width: number; height: number; has_audio: boolean; codec: string }
export interface ProjectState { path: string; name: string; version: number; source: { path: string | null } | null; video: VideoInfo | null; blueprint: Blueprint; ui: Record<string, any>; missing: { source: string | null; assets: { id: string; path: string }[] }; recovery_available: boolean }
export interface JobState { id: string; kind: string; state: string; progress: number; eta_seconds: number | null; current: string; error: string | null; output: string; log: string[]; hardware_fallback: boolean; operations: string[] }

export interface AiDevice { choice: string; running_on: string; provider: string; wanted: string; actual: string | null; fell_back: boolean; options: { value: string; label: string }[] }
export interface ReportConfig { endpoint: string; bucket: string; access_key_id_masked: string; has_secret: boolean; enabled: boolean; auto_send: boolean; configured: boolean; pending: number; last: { ok?: boolean; error?: string; when?: number } }

export class Api {
  constructor(public base: string, public token: string) {}
  private async req(method: string, path: string, body?: unknown, raw = false): Promise<any> {
    const r = await fetch(this.base + path, { method, headers: { "x-evp-token": this.token, ...(body !== undefined ? { "content-type": "application/json" } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      let b: any; try { b = await r.json(); } catch { /* ignore */ }
      const d = b?.detail; const msg = typeof d === "string" ? d : d?.errors ? d.errors.map((e: any) => e.message ?? e).join("; ") : `HTTP ${r.status}`;
      throw new ApiError(msg, r.status, b);
    }
    return raw ? r.blob() : r.json();
  }
  mediaUrl(path: string) { return `${this.base}/api/media/project?path=${encodeURIComponent(path)}&token=${this.token}`; }
  sourceUrl() { return `${this.base}/api/media/source?token=${this.token}`; }
  schema(): Promise<Schema> { return this.req("GET", "/api/schema"); }
  system(): Promise<any> { return this.req("GET", "/api/system"); }
  defaults(): Promise<{ projects_dir: string }> { return this.req("GET", "/api/defaults"); }
  quickProject(source?: string, name?: string): Promise<ProjectState> { return this.req("POST", "/api/project/quick", { source: source || null, name: name || null }); }
  recent(): Promise<{ path: string; name: string }[]> { return this.req("GET", "/api/project/recent"); }
  newProject(parent: string, name: string, source?: string): Promise<ProjectState> { return this.req("POST", "/api/project/new", { parent, name, source: source || null }); }
  openProject(path: string): Promise<ProjectState> { return this.req("POST", "/api/project/open", { path }); }
  setSource(path: string): Promise<ProjectState> { return this.req("POST", "/api/project/source", { path }); }
  putBlueprint(blueprint: Blueprint, ui?: any) { return this.req("PUT", "/api/project/blueprint", { blueprint, ui }); }
  save(blueprint: Blueprint, ui?: any): Promise<{ version: number }> { return this.req("POST", "/api/project/save", { blueprint, ui }); }
  saveAs(parent: string, name: string): Promise<ProjectState> { return this.req("POST", "/api/project/save_as", { parent, name }); }
  recover(): Promise<ProjectState> { return this.req("POST", "/api/project/recover"); }
  discardAutosave() { return this.req("POST", "/api/project/discard_autosave"); }
  versions(): Promise<{ file: string; version: number; modified: number; operations: number }[]> { return this.req("GET", "/api/project/versions"); }
  restore(file: string): Promise<ProjectState> { return this.req("POST", "/api/project/restore", { file }); }
  relink(kind: "source" | "asset", path: string, asset_id?: string): Promise<ProjectState> { return this.req("POST", "/api/project/relink", { kind, path, asset_id }); }
  importAsset(path: string): Promise<{ path: string }> { return this.req("POST", "/api/project/asset", { path }); }
  validate(blueprint: unknown): Promise<Validation> { return this.req("POST", "/api/blueprint/validate", { blueprint }); }
  /** width null = full output resolution (used by the 1:1 loupe) */
  frame(blueprint: Blueprint, t: number, original: boolean, width: number | null = 960): Promise<Blob> { return this.req("POST", "/api/preview/frame", { blueprint, t, original, width }, true); }
  render(blueprint: Blueprint, preset: "proxy" | "export", extra: Record<string, any> = {}): Promise<{ cached: boolean; output: string; job: JobState | null }> { return this.req("POST", "/api/render", { blueprint, preset, ...extra }); }
  aiDevice(): Promise<AiDevice> { return this.req("GET", "/api/ai-device"); }
  setAiDevice(device: string): Promise<AiDevice> { return this.req("PUT", "/api/ai-device", { device }); }
  reportConfig(): Promise<ReportConfig> { return this.req("GET", "/api/report/config"); }
  saveReportConfig(c: Partial<{ endpoint: string; bucket: string; access_key_id: string; secret_access_key: string; enabled: boolean; auto_send: boolean }>): Promise<ReportConfig> { return this.req("PUT", "/api/report/config", c); }
  reportTest(): Promise<{ ok: boolean; error?: string; key?: string; queued?: boolean }> { return this.req("POST", "/api/report/test"); }
  reportSend(message: string): Promise<{ ok: boolean; error?: string; key?: string }> { return this.req("POST", "/api/report/send", { message }); }
  job(id: string): Promise<JobState> { return this.req("GET", `/api/jobs/${id}`); }
  jobAction(id: string, a: "pause" | "resume" | "cancel" | "retry"): Promise<JobState> { return this.req("POST", `/api/jobs/${id}/${a}`); }
}
