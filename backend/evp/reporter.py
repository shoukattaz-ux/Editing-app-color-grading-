"""Problem reports to Cloudflare R2 (S3 API, AWS Signature V4, standard library only).

* Credentials are NEVER part of the source or the installer. They live in `<data dir>/report.json` (mode 600), entered in
  Settings, or come from the environment (EVP_REPORT_ENDPOINT / _BUCKET / _ACCESS_KEY_ID / _SECRET_ACCESS_KEY).
* A report contains: app/OS/FFmpeg versions, the error and the tail of the job log, the blueprint (operations and parameters;
  file paths reduced to file names) and the plan. Never video, audio, frames or project files. Home folders are masked.
* Sending happens in a background thread with a timeout; failures go to an outbox and are retried later. Automatic reports are
  rate-limited and de-duplicated so a failing loop cannot flood the bucket.
"""
from __future__ import annotations
from . import proc
import datetime as _dt, hashlib, hmac, io, json, os, platform, re, threading, time, urllib.error, urllib.request, uuid, zipfile
from pathlib import Path
from urllib.parse import quote, urlparse

R2_SUFFIX = ".r2.cloudflarestorage.com"
MAX_AUTO_PER_HOUR = 6
MAX_LOG_CHARS = 60_000


# ---------------------------------------------------------------- AWS Signature V4
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method: str, host: str, path: str, headers: dict, payload_hash: str, access_key: str, secret_key: str,
                  region: str = "auto", service: str = "s3", query: str = "", now: _dt.datetime | None = None) -> dict:
    """Return `headers` plus x-amz-date and Authorization. `path` must already be URI-encoded."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h.update({"host": host, "x-amz-date": amz})
    names = sorted(h)
    canonical = "\n".join([method, path, query, "".join(f"{k}:{h[k]}\n" for k in names), ";".join(names), payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
    return h


def put_object(endpoint: str, bucket: str, key: str, body: bytes, access_key: str, secret_key: str, content_type: str = "application/zip",
               timeout: float = 15.0) -> None:
    u = urlparse(endpoint)
    path = "/" + quote(bucket, safe="") + "/" + quote(key, safe="/-_.~")
    h = sigv4_headers("PUT", u.netloc, path, {"content-type": content_type, "x-amz-content-sha256": hashlib.sha256(body).hexdigest()},
                      hashlib.sha256(body).hexdigest(), access_key, secret_key)
    req = urllib.request.Request(f"{u.scheme}://{u.netloc}{path}", data=body, method="PUT", headers={k: v for k, v in h.items() if k != "host"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status not in (200, 201, 204): raise RuntimeError(f"storage answered HTTP {r.status}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"storage refused the upload (HTTP {e.code}): {e.read(300).decode(errors='replace')}") from None


# ---------------------------------------------------------------- configuration
def validate_endpoint(endpoint: str, allow_local: bool = False) -> str:
    u = urlparse(endpoint.strip())
    if allow_local and u.hostname in ("127.0.0.1", "localhost") and u.scheme in ("http", "https"):
        return f"{u.scheme}://{u.netloc}"
    if u.scheme != "https" or not (u.hostname or "").endswith(R2_SUFFIX):
        raise ValueError(f"endpoint must be an https://<account>{R2_SUFFIX} address")
    return f"https://{u.netloc}"


class Reporter:
    def __init__(self, data_dir: Path, app_version: str = "", allow_local: bool = False):
        self.dir = Path(data_dir)
        self.cfg_path = self.dir / "report.json"
        self.outbox = self.dir / "outbox"
        self.version = app_version
        self.allow_local = allow_local
        self.last: dict = {}
        self._sent: list[float] = []
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self.install_id = self._install_id()

    # -- config -----------------------------------------------------------------------------------------------
    def _install_id(self) -> str:
        f = self.dir / "install_id"
        try:
            if f.is_file(): return f.read_text(encoding="utf-8").strip()[:36]
            self.dir.mkdir(parents=True, exist_ok=True); i = uuid.uuid4().hex; f.write_text(i, encoding="utf-8"); return i
        except OSError:
            return "unknown"

    def config(self) -> dict:
        c: dict = {}
        try: c = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): pass
        env = {"endpoint": os.environ.get("EVP_REPORT_ENDPOINT"), "bucket": os.environ.get("EVP_REPORT_BUCKET"),
               "access_key_id": os.environ.get("EVP_REPORT_ACCESS_KEY_ID"), "secret_access_key": os.environ.get("EVP_REPORT_SECRET_ACCESS_KEY")}
        c.update({k: v for k, v in env.items() if v})
        return c

    def configured(self) -> bool:
        c = self.config()
        return bool(c.get("enabled", True) and all(c.get(k) for k in ("endpoint", "bucket", "access_key_id", "secret_access_key")))

    def public_config(self) -> dict:
        c = self.config()
        kid = c.get("access_key_id") or ""
        return {"endpoint": c.get("endpoint", ""), "bucket": c.get("bucket", ""), "access_key_id_masked": ("…" + kid[-4:]) if kid else "",
                "has_secret": bool(c.get("secret_access_key")), "enabled": bool(c.get("enabled", True)), "auto_send": bool(c.get("auto_send", True)),
                "configured": self.configured(), "pending": len(list(self.outbox.glob("*.zip"))) if self.outbox.is_dir() else 0, "last": self.last}

    def save_config(self, endpoint: str | None, bucket: str | None, access_key_id: str | None, secret_access_key: str | None,
                    enabled: bool | None, auto_send: bool | None) -> None:
        c = self.config() if self.cfg_path.is_file() else {}
        if endpoint is not None: c["endpoint"] = validate_endpoint(endpoint, self.allow_local)
        if bucket is not None:
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket): raise ValueError("bucket name: 3-63 lowercase letters, digits, dots or hyphens")
            c["bucket"] = bucket
        if access_key_id: c["access_key_id"] = access_key_id.strip()
        if secret_access_key: c["secret_access_key"] = secret_access_key.strip()        # empty = keep the stored one
        if enabled is not None: c["enabled"] = enabled
        if auto_send is not None: c["auto_send"] = auto_send
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f: json.dump(c, f)
        os.replace(tmp, self.cfg_path)
        try: os.chmod(self.cfg_path, 0o600)
        except OSError: pass

    # -- building a report ----------------------------------------------------------------------------------------
    def _mask(self, text: str) -> str:
        home = str(Path.home())
        text = text.replace(home, "~")
        text = re.sub(r"(?i)([A-Za-z]:\\Users\\)[^\\\s\"']+", r"\1<user>", text)
        text = re.sub(r"(/(?:home|Users)/)[^/\s\"']+", r"\1<user>", text)
        return text

    def _clean_blueprint(self, bp) -> dict:
        def walk(x, key=""):
            if isinstance(x, dict): return {k: walk(v, k) for k, v in x.items()}
            if isinstance(x, list): return [walk(v, key) for v in x]
            if isinstance(x, str) and key in ("path", "source_video", "lut_path"): return os.path.basename(x.replace("\\", "/"))
            return x
        return walk(bp) if isinstance(bp, dict) else {}

    def build(self, kind: str, message: str, details: dict | None = None, blueprint=None, log: str = "") -> bytes:
        info = {"kind": kind, "message": self._mask(message)[:2000], "app_version": self.version, "install_id": self.install_id,
                "time_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "system": {"os": platform.platform(), "python": platform.python_version(), "machine": platform.machine()},
                "details": json.loads(self._mask(json.dumps(details or {}, default=str)))}
        try:
            from .media import ffmpeg_bin; import subprocess
            info["system"]["ffmpeg"] = proc.run([ffmpeg_bin(), "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5).stdout.splitlines()[0]
        except Exception: pass
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.json", json.dumps(info, indent=1))
            if blueprint is not None: z.writestr("blueprint.json", json.dumps(self._clean_blueprint(blueprint), indent=1))
            if log: z.writestr("log.txt", self._mask(log[-MAX_LOG_CHARS:]))
        return buf.getvalue()

    def _key(self, kind: str) -> str:
        t = _dt.datetime.now(_dt.timezone.utc)
        return f"reports/{t:%Y/%m/%d}/{t:%H%M%S}-{kind}-{self.install_id[:8]}-{uuid.uuid4().hex[:6]}.zip"

    # -- sending ----------------------------------------------------------------------------------------------
    def _upload(self, key: str, body: bytes) -> None:
        c = self.config()
        endpoint = validate_endpoint(c["endpoint"], self.allow_local)
        put_object(endpoint, c["bucket"], key, body, c["access_key_id"], c["secret_access_key"])

    def send(self, kind: str, message: str, details=None, blueprint=None, log: str = "", wait: bool = True) -> dict:
        """Upload now (wait=True: returns {ok, key|error}) or in the background."""
        if not self.configured(): return {"ok": False, "error": "problem reporting is not configured (Settings > Problem reports)"}
        body = self.build(kind, message, details, blueprint, log)
        key = self._key(kind)
        def work() -> dict:
            try:
                self._upload(key, body)
                res = {"ok": True, "key": key, "when": time.time()}
                self.flush_outbox()
            except Exception as e:                                                   # noqa - any network problem: keep it for later
                self._queue(key, body)
                res = {"ok": False, "error": self._mask(str(e)), "when": time.time(), "queued": True}
            self.last = res
            return res
        if wait: return work()
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "queued": True}

    def auto(self, kind: str, message: str, details=None, blueprint=None, log: str = "") -> bool:
        """Report a failure without being asked - only if enabled, de-duplicated and rate-limited."""
        c = self.config()
        if not (self.configured() and c.get("auto_send", True)): return False
        sig = hashlib.sha1((kind + re.sub(r"\d+", "#", message)[:300]).encode()).hexdigest()
        with self._lock:
            now = time.time()
            self._sent = [t for t in self._sent if now - t < 3600]
            if sig in self._seen or len(self._sent) >= MAX_AUTO_PER_HOUR: return False
            self._seen.add(sig); self._sent.append(now)
        self.send(kind, message, details, blueprint, log, wait=False)
        return True

    def _queue(self, key: str, body: bytes) -> None:
        try:
            self.outbox.mkdir(parents=True, exist_ok=True)
            files = sorted(self.outbox.glob("*.zip"))
            for old in files[:max(0, len(files) - 19)]: old.unlink(missing_ok=True)   # keep at most 20
            (self.outbox / (key.replace("/", "__"))).write_bytes(body)
        except OSError: pass

    def flush_outbox(self) -> int:
        n = 0
        if not self.outbox.is_dir() or not self.configured(): return 0
        for f in sorted(self.outbox.glob("*.zip"))[:10]:
            try: self._upload(f.name.replace("__", "/"), f.read_bytes()); f.unlink(); n += 1
            except Exception: break
        return n
