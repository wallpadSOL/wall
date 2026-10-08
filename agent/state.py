"""The state file: everything the site shows, written once a second.

Pure functions build the document from in-memory counts; the agent writes it
atomically. Anyone can read /state.json and recompute every number from the
rule in detector.py.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field

from . import detector as d
from .streams import Stream

VERSION = "0.1"


@dataclass
class Watch:
    stream: Stream
    joined: float
    times: deque = field(default_factory=lambda: deque(maxlen=20000))
    cooldown_until: int = 0
    last_verdict: d.Verdict | None = None
    last_spike: dict | None = None
    peak_rate: float = 0.0

    def add(self, ts: float) -> None:
        self.times.append(ts)

    def prune(self, now: float, keep_s: int = 180) -> None:
        cut = now - keep_s
        while self.times and self.times[0] < cut:
            self.times.popleft()


def rate_now(w: Watch, now: float, span: float = 10.0) -> float:
    """Live rate over the last `span` seconds (not the rated tick)."""
    cut = now - span
    n = sum(1 for t in reversed(w.times) if t >= cut) if w.times else 0
    return n / span


def evaluate_watch(w: Watch, now: int, params: d.Params = d.DEFAULTS) -> d.Verdict:
    counts = d.counts_by_tick(w.times, params.tick_s)
    v = d.evaluate(counts=counts, now=now, watch_start=int(w.joined),
                   cooldown_until=w.cooldown_until, params=params)
    w.cooldown_until = v.cooldown_until
    w.last_verdict = v
    w.peak_rate = max(w.peak_rate, v.rate)
    return v


def history(w: Watch, now: float, params: d.Params = d.DEFAULTS, ticks: int = 12) -> list[float]:
    """Rate per tick for the last `ticks` complete ticks, oldest first. Zeros where nothing came in."""
    counts = d.counts_by_tick(w.times, params.tick_s)
    last = d.tick_start(now, params.tick_s) - params.tick_s
    return [round(counts.get(last - i * params.tick_s, 0) / params.tick_s, 2) for i in range(ticks - 1, -1, -1)]


def stream_doc(w: Watch, v: d.Verdict, now: float, coin: dict | None, clip: dict | None,
               params: d.Params = d.DEFAULTS) -> dict:
    s = w.stream.public()
    s.update({
        "key": w.stream.key,
        "watched_s": int(now - w.joined),
        "rate": round(v.rate, 2),
        "rate_live": round(rate_now(w, now), 2),
        "baseline": round(v.baseline, 2),
        "multiplier": round(v.multiplier, 2),
        "line": round(d.line_for(v.baseline, params), 2),
        "state": v.state,
        "ticks_observed": v.ticks_observed,
        "cooldown_until": v.cooldown_until,
        "messages_tick": v.messages,
        "peak_rate": round(w.peak_rate, 2),
        "history": history(w, now, params),
        "last_spike": w.last_spike,
        "coin": coin,
        "clip": clip,
    })
    return s


# the last few warnings, so the public state says why a roster call failed without anyone reading the journal
import collections, logging as _logging
RECENT_WARNINGS: collections.deque = collections.deque(maxlen=8)


class _Recent(_logging.Handler):
    def emit(self, record):
        try:
            RECENT_WARNINGS.append({"ts": int(record.created), "where": record.name, "msg": record.getMessage()[:200]})
        except Exception:  # noqa: BLE001
            pass


def install_warning_tap() -> None:
    root = _logging.getLogger()
    if not any(isinstance(h, _Recent) for h in root.handlers):
        h = _Recent(); h.setLevel(_logging.WARNING); root.addHandler(h)


def build_state(watches: list[Watch], now: float, coins: dict, clips: dict,
                started: float, params: d.Params = d.DEFAULTS) -> dict:
    streams = []
    for w in watches:
        v = w.last_verdict or evaluate_watch(w, int(now), params)
        streams.append(stream_doc(w, v, now, coins.get(w.stream.key), clips.get(w.stream.key), params))
    streams.sort(key=lambda x: (-(x["state"] == d.SPIKE), -x["multiplier"], -x["viewers"]))
    return {
        "updated": int(now),
        "agent": {"version": VERSION, "uptime_s": int(now - started), "streams": len(streams),
                  "warnings": list(RECENT_WARNINGS)},
        "rule": {"tick_s": params.tick_s, "baseline_ticks": params.baseline_ticks,
                 "spike_x": params.spike_x, "warm_x": params.warm_x,
                 "floor_per_s": params.floor_per_s, "cooldown_s": params.cooldown_s,
                 "baseline_min_per_s": params.baseline_min_per_s},
        "streams": streams,
    }


def write_json(path: str, doc) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)


def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default
