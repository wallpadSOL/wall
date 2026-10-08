"""The desk: keeps the last two minutes of every watched stream on disk and
cuts a clip the moment the line is crossed.

One streamlink -> ffmpeg pipeline per stream writes 5-second MPEG-TS
segments into a ring of 24 (two minutes). A cut takes the newest N segments,
joins them, re-encodes once to a phone-friendly mp4 and writes a thumbnail.
Nothing is uploaded or posted from here: the clip lands in clips.json and
the queue, and a person decides what to do with it.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import sys
import subprocess
import time

from .streams import Stream

log = logging.getLogger("wall.clipper")

SEG_S = 5
RING = 24


class Clipper:
    def __init__(self, data_dir: str, quality: str = "480p,360p,worst", clip_s: int = 30,
                 keep_clips: int = 200, enabled: bool = True):
        self.buf_dir = os.path.join(data_dir, "buf")
        self.clip_dir = os.path.join(data_dir, "clips")
        self.quality = quality
        self.clip_s = clip_s
        self.keep_clips = keep_clips
        # streamlink lives in the venv next to the interpreter; systemd does not put that on PATH
        venv_bin = os.path.dirname(sys.executable)
        if venv_bin and venv_bin not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = venv_bin + os.pathsep + os.environ.get("PATH", "")
        self.enabled = enabled and shutil.which("streamlink") is not None and shutil.which("ffmpeg") is not None
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        os.makedirs(self.buf_dir, exist_ok=True)
        os.makedirs(self.clip_dir, exist_ok=True)
        if not self.enabled:
            log.warning("clipper disabled (need streamlink and ffmpeg on PATH)")

    # ------------------------------------------------------------ buffers

    def _buf(self, key: str) -> str:
        p = os.path.join(self.buf_dir, key.replace(":", "_"))
        os.makedirs(p, exist_ok=True)
        return p

    async def watch(self, stream: Stream, stop: asyncio.Event) -> None:
        """Keep the ring buffer of one stream alive until stop is set."""
        if not self.enabled:
            return
        buf = self._buf(stream.key)
        backoff = 5
        while not stop.is_set():
            cmd = (
                f"streamlink --stdout --twitch-disable-ads --retry-streams 5 --retry-open 3 "
                f"{stream.url} {self.quality} | "
                f"ffmpeg -loglevel error -i pipe:0 -c copy -f segment -segment_time {SEG_S} "
                f"-segment_wrap {RING} -reset_timestamps 1 -y {buf}/seg%03d.ts"
            )
            try:
                proc = await asyncio.create_subprocess_shell(cmd, stdout=subprocess.DEVNULL,
                                                            stderr=subprocess.PIPE)
                self.procs[stream.key] = proc
                log.info("buffer up: %s", stream.key)
                err = await proc.stderr.read()
                rc = await proc.wait()
                if rc not in (0, None):
                    log.warning("buffer %s exited %s: %s", stream.key, rc, err[-300:].decode(errors="ignore"))
            except Exception as e:  # noqa: BLE001
                log.warning("buffer %s: %s", stream.key, e)
            finally:
                self.procs.pop(stream.key, None)
            if stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def unwatch(self, key: str) -> None:
        p = self.procs.get(key)
        if p and p.returncode is None:
            try:
                p.terminate()
            except ProcessLookupError:
                pass

    # ------------------------------------------------------------ cutting

    def newest_segments(self, key: str, seconds: int) -> list[str]:
        buf = self._buf(key)
        segs = sorted(glob.glob(os.path.join(buf, "seg*.ts")), key=os.path.getmtime)
        now = time.time()
        # the newest file is still being written; leave it out unless it is old
        if segs and now - os.path.getmtime(segs[-1]) < 1.5:
            segs = segs[:-1]
        n = max(1, -(-seconds // SEG_S))
        return segs[-n:]

    def cut(self, stream: Stream, meta: dict) -> dict | None:
        """Join the newest segments into a clip. Synchronous; run in a thread."""
        if not self.enabled:
            return None
        segs = self.newest_segments(stream.key, self.clip_s)
        if not segs:
            log.warning("cut %s: no buffer yet", stream.key)
            return None
        ts = int(meta.get("ts") or time.time())
        base = f"{stream.key.replace(':', '_')}-{ts}"
        out = os.path.join(self.clip_dir, base + ".mp4")
        thumb = os.path.join(self.clip_dir, base + ".jpg")
        lst = os.path.join(self.clip_dir, base + ".txt")
        with open(lst, "w") as f:
            for s in segs:
                f.write(f"file '{s}'\n")
        try:
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out,
            ], check=True, timeout=180)
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y", "-ss", "1", "-i", out, "-frames:v", "1",
                "-vf", "scale=640:-2", thumb,
            ], check=True, timeout=60)
        except Exception as e:  # noqa: BLE001
            log.warning("cut %s failed: %s", stream.key, e)
            return None
        finally:
            try:
                os.remove(lst)
            except OSError:
                pass
        dur = len(segs) * SEG_S
        rec = {
            "key": stream.key, "platform": stream.platform, "slug": stream.slug,
            "name": stream.name, "title": stream.title, "ts": ts,
            "file": "clips/" + os.path.basename(out), "thumb": "clips/" + os.path.basename(thumb),
            "duration_s": dur, "rate": meta.get("rate"), "baseline": meta.get("baseline"),
            "multiplier": meta.get("multiplier"), "viewers": stream.viewers,
            "posted": False,
        }
        self.trim()
        log.info("clip: %s (%ss, %sx)", rec["file"], dur, rec["multiplier"])
        return rec

    def trim(self) -> None:
        clips = sorted(glob.glob(os.path.join(self.clip_dir, "*.mp4")), key=os.path.getmtime)
        for old in clips[:-self.keep_clips]:
            for p in (old, old[:-4] + ".jpg"):
                try:
                    os.remove(p)
                except OSError:
                    pass
