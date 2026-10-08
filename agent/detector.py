"""The line. One rule for every stream, no per-stream tuning.

Everything here is pure: no clock, no network, no files. Callers pass
message timestamps and the moment of evaluation. That is what makes the
tests deterministic and the numbers on the site reproducible by anyone.

Definitions, fixed once and used everywhere:

    tick        10 seconds, aligned to the unix epoch
    rate        messages in the last COMPLETE tick, per second
    baseline    mean rate of the 6 complete ticks before it (one minute)
    multiplier  rate / max(baseline, 0.3)
    floor       2.0 messages per second - below it a chat never fires
    warm-up     those 6 baseline ticks must be fully observed
    cooldown    90 seconds per stream after a SPIKE

The line is 3x. A chat is SPIKE when it runs at least three times its own
last minute and at least two messages a second. WARM at 1.5x. A stream that
has been watched for less than 70 seconds is WARMING and cannot fire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

WARMING = "WARMING"
QUIET = "QUIET"
WARM = "WARM"
SPIKE = "SPIKE"


@dataclass(frozen=True)
class Params:
    tick_s: int = 10
    baseline_ticks: int = 6
    floor_per_s: float = 2.0
    spike_x: float = 3.0
    warm_x: float = 1.5
    cooldown_s: int = 90
    baseline_min_per_s: float = 0.3


DEFAULTS = Params()


@dataclass(frozen=True)
class Verdict:
    state: str
    tick: int              # start of the rated tick
    rate: float            # messages per second in the rated tick
    baseline: float        # messages per second, mean of the minute before
    multiplier: float
    ticks_observed: int
    cooldown_until: int
    messages: int = 0      # raw count in the rated tick

    @property
    def fired(self) -> bool:
        return self.state == SPIKE


def tick_start(ts: int, tick_s: int = 10) -> int:
    return (int(ts) // tick_s) * tick_s


def _ceil_tick(ts: int, tick_s: int) -> int:
    return -(-int(ts) // tick_s) * tick_s


def counts_by_tick(times: Iterable[float], tick_s: int = 10) -> dict[int, int]:
    out: dict[int, int] = {}
    for ts in times:
        t = tick_start(ts, tick_s)
        out[t] = out.get(t, 0) + 1
    return out


def evaluate(
    *,
    counts: Mapping[int, int],
    now: int,
    watch_start: int,
    cooldown_until: int = 0,
    params: Params = DEFAULTS,
) -> Verdict:
    """Rate one stream at one moment.

    counts          tick start -> messages in that tick
    now             evaluation time, unix seconds
    watch_start     when the agent joined this chat
    cooldown_until  from the previous verdict, 0 if never fired
    """
    t = params.tick_s
    rated = tick_start(now, t) - t
    first_full = _ceil_tick(watch_start, t)
    oldest_needed = rated - params.baseline_ticks * t
    observed = 0 if rated < first_full else (rated - first_full) // t + 1

    if rated < first_full or oldest_needed < first_full:
        return Verdict(
            state=WARMING, tick=rated, rate=counts.get(rated, 0) / t,
            baseline=0.0, multiplier=0.0, ticks_observed=observed,
            cooldown_until=cooldown_until, messages=counts.get(rated, 0))

    total = sum(counts.get(oldest_needed + i * t, 0)
                for i in range(params.baseline_ticks))
    baseline = total / (params.baseline_ticks * t)
    messages = counts.get(rated, 0)
    rate = messages / t
    multiplier = rate / max(baseline, params.baseline_min_per_s)

    loud = multiplier >= params.spike_x and rate >= params.floor_per_s
    cooling = now < cooldown_until

    if loud and not cooling:
        state = SPIKE
        cooldown_until = now + params.cooldown_s
    elif multiplier >= params.warm_x:
        state = WARM
    else:
        state = QUIET

    return Verdict(
        state=state, tick=rated, rate=rate, baseline=baseline,
        multiplier=multiplier, ticks_observed=observed,
        cooldown_until=cooldown_until, messages=messages)


def line_for(baseline: float, params: Params = DEFAULTS) -> float:
    """The rate, per second, a chat must reach to fire right now."""
    return max(params.spike_x * max(baseline, params.baseline_min_per_s),
               params.floor_per_s)
