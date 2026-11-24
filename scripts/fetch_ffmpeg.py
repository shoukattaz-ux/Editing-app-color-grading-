"""Download a static FFmpeg + ffprobe into build/ffmpeg (bundled into the installer).

Windows: gyan.dev "essentials" build; Linux: johnvansickle static build. Both are GPL builds (libx264/libx265): fine for personal use;
if you distribute the installer, GPL source-offer obligations apply to the FFmpeg binaries.
"""
import io, os, shutil, stat, sys, tarfile, urllib.request, zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "build" / "ffmpeg"
URLS = {"win32": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "linux": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"}


def main() -> int:
    if sys.platform not in URLS:
        print("no automatic download for this OS: put ffmpeg and ffprobe into build/ffmpeg yourself"); return 1
    shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir(parents=True)
    print("downloading", URLS[sys.platform])
    data = urllib.request.urlopen(URLS[sys.platform], timeout=300).read()
    want = {"ffmpeg.exe", "ffprobe.exe"} if sys.platform == "win32" else {"ffmpeg", "ffprobe"}
    if sys.platform == "win32":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if os.path.basename(n) in want and "/bin/" in n: (OUT / os.path.basename(n)).write_bytes(z.read(n))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as t:
            for m in t.getmembers():
                if os.path.basename(m.name) in want and m.isfile():
                    f = OUT / os.path.basename(m.name); f.write_bytes(t.extractfile(m).read()); f.chmod(f.stat().st_mode | stat.S_IEXEC)
    got = sorted(p.name for p in OUT.iterdir())
    print("bundled:", got)
    return 0 if set(got) == want else 2


if __name__ == "__main__":
    sys.exit(main())
