"""The agent loop: pick streams, read their chats, rate them every second,
cut on the line, write the files the site reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field

from . import detector as d
from . import streams as S
from .clipper import Clipper
from .server import Store, serve
from .state import Watch, build_state, evaluate_watch, read_json, write_json

log = logging.getLogger("wall.agent")


@dataclass
class Config:
    data_dir: str = "./data"
    pinned: list[str] = field(default_factory=list)      # "kick:slug" / "twitch:login"
    auto_top_twitch: int = 3
    auto_top_kick: int = 3
    twitch_languages: list[str] = field(default_factory=lambda: ["en"])
    # auto picks: only these categories (empty = any), never these. pinned streams ignore both.
    categories: list[str] = field(default_factory=lambda: ["Just Chatting", "IRL"])
    exclude_categories: list[str] = field(default_factory=lambda: ["Slots & Casino", "Slots", "Casino", "Gambling"])
    max_streams: int = 8
    refresh_s: int = 60
    host: str = "127.0.0.1"
    port: int = 8791
    cors: str = "*"
    clipper: bool = True
    quality: str = "480p,360p,worst"
    clip_s: int = 30
    params: d.Params = d.DEFAULTS


class Agent:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.watches: dict[str, Watch] = {}
        self.tasks: dict[str, list[asyncio.Task]] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.started = time.time()
        self.clipper = Clipper(cfg.data_dir, cfg.quality, cfg.clip_s, enabled=cfg.clipper)
        self.clips: dict[str, dict] = {}
        self.last_fired: dict[str, int] = {}
        os.makedirs(cfg.data_dir, exist_ok=True)
        for c in read_json(os.path.join(cfg.data_dir, "clips.json"), []):
            self.clips.setdefault(c["key"], c)
        # the public event log: what the agent did, newest last. the site shows it as "the agent, right now"
        self.events: deque = deque(read_json(os.path.join(cfg.data_dir, "events.json"), {}).get("events", []), maxlen=150)
        self.events_dirty = False
        self.seen_state: dict[str, str] = {}
        self.warm_said: dict[str, float] = {}
        self.n_coins = len(read_json(os.path.join(cfg.data_dir, "coins.json"), []))
        self.n_rounds = len(read_json(os.path.join(cfg.data_dir, "fees.json"), []))
        self.event("boot", text="agent started")

    def event(self, kind: str, **data) -> None:
        rec = {"ts": int(time.time()), "kind": kind}
        rec.update(data)
        self.events.append(rec)
        self.events_dirty = True

    def _stream_bits(self, s: S.Stream) -> dict:
        return {"key": s.key, "name": s.name or s.slug, "platform": s.platform, "viewers": s.viewers}

    # ------------------------------------------------------------ roster

    def _parse_pin(self, s: str) -> tuple[str, str] | None:
        if ":" not in s:
            return None
        plat, slug = s.split(":", 1)
        plat, slug = plat.strip().lower(), slug.strip().lower()
        return (plat, slug) if plat in ("twitch", "kick") and slug else None

    async def roster(self) -> list[S.Stream]:
        loop = asyncio.get_running_loop()
        pins = [p for p in (self._parse_pin(x) for x in self.cfg.pinned) if p]
        want: dict[str, S.Stream] = {}

        tw_pins = [slug for plat, slug in pins if plat == "twitch"]
        if tw_pins:
            try:
                for k, st in (await loop.run_in_executor(None, S.twitch_info, tw_pins)).items():
                    st.pinned = True
                    want[k] = st
            except Exception as e:  # noqa: BLE001
                log.warning("twitch info: %s", e)
        for plat, slug in pins:
            if plat == "kick":
                try:
                    st = await loop.run_in_executor(None, S.kick_info, slug)
                    if st:
                        st.pinned = True
                        want[st.key] = st
                except Exception as e:  # noqa: BLE001
                    log.warning("kick info %s: %s", slug, e)

        def ok(st: S.Stream) -> bool:
            c = (st.category or "").strip().lower()
            if any(c == x.lower() or x.lower() in c for x in self.cfg.exclude_categories):
                return False
            if any(x.lower() in (st.title or "").lower() for x in ("esl ", "blast ", "iem ", "major ", "rebroadcast")):
                return False  # tournaments and rebroadcasts are not a person
            return not self.cfg.categories or any(c == x.lower() for x in self.cfg.categories)

        if self.cfg.auto_top_twitch > 0:
            try:
                top = await loop.run_in_executor(None, S.twitch_top, 60, self.cfg.twitch_languages)
                for st in top:
                    if len([w for w in want.values() if w.platform == "twitch" and not w.pinned]) >= self.cfg.auto_top_twitch:
                        break
                    if ok(st):
                        want.setdefault(st.key, st)
            except Exception as e:  # noqa: BLE001
                log.warning("twitch top: %s", e)
        if self.cfg.auto_top_kick > 0:
            try:
                top = await loop.run_in_executor(None, S.kick_top, 60, self.cfg.twitch_languages)
                n = 0
                for st in top:
                    if n >= self.cfg.auto_top_kick:
                        break
                    if st.key not in want and ok(st):
                        w = self.watches.get(st.key)
                        if w and w.stream.avatar:
                            # already on the wall: keep the art we fetched when it joined
                            st.avatar, st.thumb_alt = w.stream.avatar, st.thumb_alt or w.stream.avatar
                            st.thumb = st.thumb or w.stream.thumb
                            if st.chatroom_id is None:
                                st.chatroom_id = w.stream.chatroom_id
                        elif st.chatroom_id is None or not st.avatar:
                            # the top list has no avatar or offline art; one channel call fills the chain
                            full = await loop.run_in_executor(None, S.kick_info, st.slug)
                            if full:
                                st.chatroom_id = st.chatroom_id or full.chatroom_id
                                st.avatar = st.avatar or full.avatar
                                st.thumb_alt = st.thumb_alt or full.thumb_alt or full.avatar
                                st.thumb = st.thumb or full.thumb
                        want[st.key] = st
                        n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("kick top: %s", e)

        # live first (pinned before auto picks, then by viewers); offline pins only fill what is left
        out = sorted(want.values(), key=lambda s: (not s.live, not s.pinned, -s.viewers))
        return out[: self.cfg.max_streams]

    async def apply_roster(self, streams: list[S.Stream]) -> None:
        keep = {s.key for s in streams}
        for key in list(self.watches):
            if key not in keep:
                await self._drop(key)
        for s in streams:
            if s.key in self.watches:
                w = self.watches[s.key]
                w.stream.viewers, w.stream.title, w.stream.category, w.stream.live = s.viewers, s.title, s.category, s.live
                w.stream.thumb, w.stream.thumb_alt = s.thumb, s.thumb_alt
                if s.avatar:
                    w.stream.avatar = s.avatar
                if s.chatroom_id and not w.stream.chatroom_id:
                    w.stream.chatroom_id = s.chatroom_id
                continue
            if s.platform == "kick" and not s.chatroom_id:
                log.warning("kick %s has no chatroom id, skipping", s.slug)
                continue
            await self._add(s)

    async def _add(self, s: S.Stream) -> None:
        w = Watch(stream=s, joined=time.time())
        self.watches[s.key] = w
        stop = asyncio.Event()
        self.stops[s.key] = stop
        if s.platform == "twitch":
            t = asyncio.create_task(S.twitch_chat(s.slug, w.add, stop))
        else:
            t = asyncio.create_task(S.kick_chat(s.chatroom_id, s.slug, w.add, stop))
        tasks = [t]
        if self.clipper.enabled and s.live:
            tasks.append(asyncio.create_task(self.clipper.watch(s, stop)))
        self.tasks[s.key] = tasks
        log.info("watching %s (%s viewers)", s.key, s.viewers)
        self.event("join", **self._stream_bits(s), live=bool(s.live))

    async def _drop(self, key: str) -> None:
        w = self.watches.get(key)
        if w:
            self.event("drop", **self._stream_bits(w.stream), live=bool(w.stream.live))
        self.stops[key].set()
        await self.clipper.unwatch(key)
        for t in self.tasks.get(key, []):
            t.cancel()
        self.watches.pop(key, None)
        self.tasks.pop(key, None)
        self.stops.pop(key, None)
        log.info("dropped %s", key)

    # ------------------------------------------------------------ ticking

    def on_spike(self, w: Watch, v: d.Verdict) -> None:
        rec = {"key": w.stream.key, "platform": w.stream.platform, "slug": w.stream.slug,
               "name": w.stream.name, "title": w.stream.title, "ts": int(time.time()),
               "tick": v.tick, "rate": round(v.rate, 2), "baseline": round(v.baseline, 2),
               "multiplier": round(v.multiplier, 2), "viewers": w.stream.viewers}
        w.last_spike = rec
        spikes = read_json(self.store.path("spikes.json"), [])
        spikes.append(rec)
        write_json(self.store.path("spikes.json"), spikes[-500:])
        log.info("SPIKE %s %.1f/s vs %.1f/s (%.1fx)", w.stream.key, v.rate, v.baseline, v.multiplier)
        self.event("spike", **self._stream_bits(w.stream), rate=round(v.rate, 1), baseline=round(v.baseline, 1),
                   multiplier=round(v.multiplier, 2))
        asyncio.get_running_loop().run_in_executor(None, self._cut, w.stream, rec)

    def _cut(self, stream: S.Stream, rec: dict) -> None:
        clip = self.clipper.cut(stream, rec)
        if not clip:
            return
        self.clips[stream.key] = clip
        clips = read_json(self.store.path("clips.json"), [])
        clips.append(clip)
        write_json(self.store.path("clips.json"), clips[-500:])
        self.event("cut", **self._stream_bits(stream), multiplier=rec.get("multiplier"), file=clip.get("file"),
                   thumb=clip.get("thumb"), duration_s=clip.get("duration_s"))

    def tick(self) -> None:
        now = time.time()
        coins = {}
        for c in self.store.read("coins.json", []):
            coins.setdefault(c["stream"], c)   # first coin on a stream is the wall's
        for w in list(self.watches.values()):
            w.prune(now)
            v = evaluate_watch(w, int(now), self.cfg.params)
            if v.fired and self.last_fired.get(w.stream.key) != v.tick:
                self.last_fired[w.stream.key] = v.tick
                self.on_spike(w, v)
            self._verdict_event(w, v, now)
        doc = build_state(list(self.watches.values()), now, coins, self.clips, self.started, self.cfg.params)
        write_json(self.store.path("state.json"), doc)
        self._outside_events()
        if self.events_dirty:
            self.events_dirty = False
            write_json(self.store.path("events.json"), {"updated": int(now), "events": list(self.events)})

    def _verdict_event(self, w, v, now: float) -> None:
        """warm / calm transitions (spikes have their own event). warm is said at most once per two minutes per stream."""
        k = w.stream.key
        prev = self.seen_state.get(k)
        self.seen_state[k] = v.state
        if prev is None or prev == v.state:
            return
        if v.state == "WARM" and now - self.warm_said.get(k, 0) > 120:
            self.warm_said[k] = now
            self.event("warm", **self._stream_bits(w.stream), multiplier=round(v.multiplier, 2), rate=round(v.rate, 1))
        elif v.state == "QUIET" and prev in ("SPIKE", "WARM"):
            self.event("calm", **self._stream_bits(w.stream), rate=round(v.rate, 1))

    def _outside_events(self) -> None:
        """things other processes write: a coin launched through the server, a fee round from the timer."""
        coins = self.store.read("coins.json", [])
        if len(coins) > self.n_coins:
            for c in coins[self.n_coins:]:
                w = self.watches.get(c.get("stream") or "")
                self.event("launch", ticker=c.get("ticker"), name=c.get("name"), mint=c.get("mint"),
                           key=c.get("stream"), launcher=c.get("launcher"),
                           stream_name=(w.stream.name if w else (c.get("stream") or "").split(":")[-1]))
            self.n_coins = len(coins)
        rounds = self.store.read("fees.json", [])
        if len(rounds) > self.n_rounds:
            for r in rounds[self.n_rounds:]:
                sp = r.get("split") or {}
                self.event("fees", collected=r.get("collected", 0), burn=sp.get("burn", 0),
                           launchers=sp.get("launchers", 0), platform=sp.get("platform", 0),
                           swept=((r.get("sweep") or {}).get("lamports") or 0), note=(r.get("note") or "")[:120])
            self.n_rounds = len(rounds)

    async def refresh_trending(self) -> None:
        """The week's top clips across the wall, on a slow timer, written to trending.json."""
        from . import trending
        loop = asyncio.get_running_loop()
        streams = [w.stream for w in self.watches.values()]
        try:
            d = await loop.run_in_executor(None, trending.doc, streams)
            if d.get("clips") or not self.store.read("trending.json", None):
                self.store.write("trending.json", d)
                self.event("trending", n=len(d.get("clips") or []))
            log.info("trending: %d clips", len(d.get("clips") or []))
        except Exception as e:  # noqa: BLE001
            log.warning("trending: %s", e)

    async def run(self) -> None:
        last_roster = 0.0
        last_trending = 0.0
        while True:
            if time.time() - last_roster >= self.cfg.refresh_s:
                try:
                    await self.apply_roster(await self.roster())
                except Exception as e:  # noqa: BLE001
                    log.warning("roster: %s", e)
                last_roster = time.time()
                if time.time() - last_trending >= 600:
                    last_trending = time.time()
                    asyncio.create_task(self.refresh_trending())
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                log.exception("tick: %s", e)
            await asyncio.sleep(1.0)


def run(cfg: Config, launcher=None) -> None:
    from .state import install_warning_tap
    install_warning_tap()
    store = Store(cfg.data_dir, launcher)
    os.makedirs(cfg.data_dir, exist_ok=True)
    if launcher:
        store.write("launch.json", {"enabled": True, "hot_wallet": launcher.pubkey,
                                    "launch_fee_sol": launcher.launch_fee_lamports / 1e9,
                                    "dev_buy_sol": launcher.dev_buy_sol, "wall_mint": launcher.wall_mint,
                                    "min_wall_hold": launcher.min_wall_hold})
    else:
        store.write("launch.json", {"enabled": False})
    serve(store, cfg.host, cfg.port, cfg.cors)
    agent = Agent(cfg, store)
    asyncio.run(agent.run())
