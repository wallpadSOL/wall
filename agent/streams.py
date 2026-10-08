"""What a stream is to the agent, and the two platforms it reads.

Twitch and Kick are read anonymously: no account, no key, no cost. The chat
is public, the viewer count is public, and the agent only ever counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Awaitable, Callable

log = logging.getLogger("wall.streams")

TWITCH_GQL = "https://gql.twitch.tv/gql"
# The client id Twitch's own web player ships with. Public, read-only.
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_IRC = "wss://irc-ws.chat.twitch.tv:443"

KICK_CHANNEL = "https://kick.com/api/v2/channels/{slug}"
KICK_TOP = "https://kick.com/stream/livestreams/en?page=1&limit=40&sort=desc"
KICK_PUSHER = ("wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
               "?protocol=7&client=js&version=8.4.0-rc2&flash=false")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


@dataclass
class Stream:
    platform: str          # "twitch" | "kick"
    slug: str
    name: str = ""
    title: str = ""
    category: str = ""
    viewers: int = 0
    live: bool = False
    chatroom_id: int | None = None   # kick only
    pinned: bool = False
    url: str = ""
    thumb: str = ""
    thumb_alt: str = ""    # what the site falls back to if thumb fails to load
    avatar: str = ""

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.slug.lower()}"

    def public(self) -> dict:
        d = asdict(self)
        d.pop("chatroom_id", None)
        return d


# ---------------------------------------------------------------- http

def _http():
    """A requests-like session that Kick's edge will talk to.

    curl_cffi impersonates a browser's TLS fingerprint, which is what gets
    past Cloudflare on kick.com. Without it the Twitch side still works.
    """
    try:
        from curl_cffi import requests as cr  # type: ignore
        s = cr.Session(impersonate="chrome")
        s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        return s
    except Exception:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        return s


_SESSION = None


def session():
    global _SESSION
    if _SESSION is None:
        _SESSION = _http()
    return _SESSION


# ---------------------------------------------------------------- twitch

def twitch_gql(query: str, variables: dict | None = None) -> dict:
    r = session().post(
        TWITCH_GQL,
        json={"query": query, "variables": variables or {}},
        headers={"Client-Id": TWITCH_CLIENT_ID, "Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def twitch_info(slugs: list[str]) -> dict[str, Stream]:
    """Viewer count, title and category for a list of logins."""
    out: dict[str, Stream] = {}
    if not slugs:
        return out
    q = """query($logins:[String!]){ users(logins:$logins){ login displayName profileImageURL(width:300) offlineImageURL
             stream{ viewersCount title game{ displayName } }
             videos(first:1, sort:TIME){ edges{ node{ previewThumbnailURL(width:640,height:360) } } } } }"""
    data = twitch_gql(q, {"logins": slugs[:100]}).get("data", {}).get("users") or []
    for u in data:
        if not u:
            continue
        s = u.get("stream") or {}
        login = u["login"].lower()
        vod = ""
        for e in ((u.get("videos") or {}).get("edges") or []):
            v = (e.get("node") or {}).get("previewThumbnailURL") or ""
            if v and "404" not in v:
                vod = v
                break
        offline = u.get("offlineImageURL") or ""
        avatar = u.get("profileImageURL") or ""
        art = [x for x in (offline, vod, avatar) if x]  # what a card shows when there is no live frame
        st = Stream(
            platform="twitch", slug=login, name=u.get("displayName") or u["login"],
            title=(s.get("title") or "")[:140],
            category=((s.get("game") or {}).get("displayName") or ""),
            viewers=int(s.get("viewersCount") or 0), live=bool(s),
            url=f"https://twitch.tv/{u['login']}",
            avatar=avatar,
        )
        pv = twitch_preview(login) if s else ""
        chain = ([pv] if pv else []) + art
        st.thumb = chain[0] if chain else ""
        st.thumb_alt = chain[1] if len(chain) > 1 else ""
        out[st.key] = st
    return out


_preview_ok: dict[str, tuple[float, bool]] = {}


def twitch_preview(login: str) -> str:
    """The live preview frame, unless Twitch answers with its camera placeholder.

    Twitch redirects previews-ttv/… to ttv-static/404_preview-… when it has no frame
    (channel just went live, previews off, or offline). That image is a grey camera
    icon, which is what a card must never show. Checked at most once a minute per login.
    """
    url = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-440x248.jpg"
    now = time.time()
    ts, ok = _preview_ok.get(login, (0.0, True))
    if now - ts > 60:
        try:
            r = session().head(url, allow_redirects=False, timeout=8)
            ok = r.status_code == 200 and "404_preview" not in (r.headers.get("location") or "")
        except Exception:  # noqa: BLE001
            ok = True  # a network hiccup is not a reason to drop the frame
        _preview_ok[login] = (now, ok)
    return url if ok else ""


def twitch_top(n: int = 30, languages: list[str] | None = None) -> list[Stream]:
    """Top live streams by viewers. GQL caps a page at 30, so walk the cursor for more."""
    q = """query($n:Int!,$langs:[Language!],$after:Cursor){ streams(first:$n, after:$after, options:{sort:VIEWER_COUNT, languages:$langs}){
             edges{ cursor node{ viewersCount title broadcaster{ login displayName profileImageURL(width:300) } game{ displayName } } } pageInfo{ hasNextPage } } }"""
    langs = [l.upper() for l in languages] if languages else None
    edges, after = [], None
    while len(edges) < n:
        doc = twitch_gql(q, {"n": min(30, n - len(edges)), "langs": langs, "after": after})
        page = ((doc.get("data") or {}).get("streams") or {})
        got = page.get("edges") or []
        if not got:
            if not edges:
                log.warning("twitch top: empty (%s)", str(doc.get("errors") or doc)[:160])
            break
        edges += got
        if not (page.get("pageInfo") or {}).get("hasNextPage"):
            break
        after = got[-1].get("cursor")
        if not after:
            break
    out = []
    for e in edges:
        nd = e.get("node") or {}
        b = nd.get("broadcaster") or {}
        if not b.get("login"):
            continue
        out.append(Stream(
            platform="twitch", slug=b["login"].lower(), name=b.get("displayName") or b["login"],
            title=(nd.get("title") or "")[:140],
            category=((nd.get("game") or {}).get("displayName") or ""),
            viewers=int(nd.get("viewersCount") or 0), live=True,
            url=f"https://twitch.tv/{b['login']}",
            thumb=twitch_preview(b["login"].lower()),
            thumb_alt=b.get("profileImageURL") or "",
            avatar=b.get("profileImageURL") or "",
        ))
    return out


async def twitch_chat(slug: str, on_message: Callable[[float], None], stop: asyncio.Event):
    """Join one Twitch chat anonymously and call on_message(ts) per message.

    Reconnects forever until stop is set. Never sends a message.
    """
    import websockets
    nick = f"justinfan{random.randint(10000, 99999)}"
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(TWITCH_IRC, ping_interval=None, open_timeout=15) as ws:
                await ws.send(f"NICK {nick}")
                await ws.send(f"JOIN #{slug.lower()}")
                backoff = 2
                log.info("twitch joined #%s", slug)
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=300)
                    except asyncio.TimeoutError:
                        await ws.send("PING :keepalive")
                        continue
                    for line in str(raw).split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                        elif " PRIVMSG #" in line:
                            on_message(time.time())
        except Exception as e:  # noqa: BLE001
            log.warning("twitch #%s: %s; reconnect in %ss", slug, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------- kick

def kick_info(slug: str) -> Stream | None:
    r = session().get(KICK_CHANNEL.format(slug=slug), timeout=15)
    if r.status_code != 200:
        log.warning("kick %s: http %s", slug, r.status_code)
        return None
    j = r.json()
    ls = j.get("livestream") or {}
    cats = ls.get("categories") or []
    thumb = ((ls.get("thumbnail") or {}).get("url") or (ls.get("thumbnail") or {}).get("src") or "")
    if ls and not thumb:
        try:  # the v2 livestream endpoint carries the frame when the channel doc does not
            r2 = session().get(f"https://kick.com/api/v2/channels/{slug}/livestream", timeout=15)
            if r2.status_code == 200:
                d = (r2.json() or {}).get("data") or {}
                thumb = ((d.get("thumbnail") or {}).get("src") or (d.get("thumbnail") or {}).get("url") or "")
        except Exception:  # noqa: BLE001
            pass
    avatar = (j.get("user") or {}).get("profile_pic") or ""
    banner = (j.get("offline_banner_image") or {}).get("src") or ""
    if not ls:  # offline: the channel's banner, then the avatar
        thumb = banner or avatar
    alt = next((x for x in (banner, avatar) if x and x != thumb), "")
    return Stream(
        platform="kick", slug=(j.get("slug") or slug).lower(),
        name=((j.get("user") or {}).get("username")) or j.get("slug") or slug,
        title=(ls.get("session_title") or "")[:140],
        category=(cats[0].get("name") if cats else "") or "",
        viewers=int(ls.get("viewer_count") or 0), live=bool(ls.get("is_live")),
        chatroom_id=((j.get("chatroom") or {}).get("id")),
        url=f"https://kick.com/{j.get('slug') or slug}",
        thumb=thumb, thumb_alt=alt, avatar=avatar,
    )


def kick_top(n: int = 30, languages: list[str] | None = None) -> list[Stream]:
    """Top live Kick channels by viewers. Endpoint shape varies; be forgiving."""
    r = session().get(KICK_TOP, timeout=15)
    if r.status_code != 200:
        log.warning("kick top: http %s", r.status_code)
        return []
    j = r.json()
    rows = j.get("data") if isinstance(j, dict) else j
    out = []
    for row in rows or []:
        ch = row.get("channel") or row
        slug = (ch.get("slug") or (ch.get("user") or {}).get("username") or "").lower()
        if not slug:
            continue
        cats = row.get("categories") or row.get("category") or []
        if isinstance(cats, dict):
            cats = [cats]
        lang = str(row.get("language") or ch.get("language") or "").lower()[:2]
        if languages and lang and lang not in [l.lower() for l in languages]:
            continue  # the wall is English-speaking rooms; a Japanese or Arabic chat spikes on its own terms
        out.append(Stream(
            platform="kick", slug=slug,
            name=((ch.get("user") or {}).get("username")) or slug,
            title=(row.get("session_title") or "")[:140],
            category=(cats[0].get("name") if cats else "") or "",
            viewers=int(row.get("viewer_count") or 0), live=True,
            chatroom_id=((ch.get("chatroom") or {}).get("id")),
            url=f"https://kick.com/{slug}",
            thumb=(((row.get("thumbnail") or {}).get("src")) or ((row.get("thumbnail") or {}).get("url")) or ""),
            thumb_alt=((ch.get("user") or {}).get("profile_pic") or ""),
            avatar=((ch.get("user") or {}).get("profile_pic") or ""),
        ))
    out.sort(key=lambda s: -s.viewers)
    return out[:n]


async def kick_chat(chatroom_id: int, slug: str, on_message: Callable[[float], None], stop: asyncio.Event):
    """Subscribe to one Kick chatroom through Pusher and count messages."""
    import websockets
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(KICK_PUSHER, ping_interval=None, open_timeout=15) as ws:
                await ws.send(json.dumps({"event": "pusher:subscribe",
                                          "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}}))
                backoff = 2
                log.info("kick joined %s (chatroom %s)", slug, chatroom_id)
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"event": "pusher:ping", "data": {}}))
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    ev = msg.get("event", "")
                    if ev == "pusher:ping":
                        await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                    elif ev.endswith("ChatMessageEvent"):
                        on_message(time.time())
        except Exception as e:  # noqa: BLE001
            log.warning("kick %s: %s; reconnect in %ss", slug, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
