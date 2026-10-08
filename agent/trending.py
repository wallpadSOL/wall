"""The week's loudest clips from the streamers on the wall.

Not cut by the agent: these are the clips the platforms' own viewers made and
watched, pulled from Twitch and Kick and ranked by views. They show what a
room is capable of before anyone launches a coin for it. Refreshed on a slow
timer; written to trending.json next to the agent's own files.
"""
from __future__ import annotations

import logging
import time

from . import streams as S

log = logging.getLogger("wall.trending")

PER_STREAMER = 2      # diversity: no one streamer owns the strip
TOP_N = 12
PERIOD = "LAST_WEEK"


def twitch_clips(logins: list[str], per: int = 4) -> list[dict]:
    """Top clips of the last week for a batch of logins, one GQL call."""
    if not logins:
        return []
    q = """query($logins:[String!],$n:Int!){ users(logins:$logins){ login displayName profileImageURL(width:300)
             clips(first:$n, criteria:{period:LAST_WEEK, sort:VIEWS_DESC}){ edges{ node{
               slug title viewCount durationSeconds thumbnailURL createdAt url game{ displayName } } } } } }"""
    doc = S.twitch_gql(q, {"logins": logins[:50], "n": per})
    out = []
    for u in (doc.get("data") or {}).get("users") or []:
        if not u:
            continue
        for e in ((u.get("clips") or {}).get("edges") or []):
            c = e.get("node") or {}
            if not c.get("slug"):
                continue
            out.append({
                "platform": "twitch", "slug": u["login"].lower(), "key": f"twitch:{u['login'].lower()}",
                "name": u.get("displayName") or u["login"], "avatar": u.get("profileImageURL") or "",
                "id": c["slug"], "title": (c.get("title") or "")[:120], "views": int(c.get("viewCount") or 0),
                "duration_s": int(c.get("durationSeconds") or 0), "thumb": c.get("thumbnailURL") or "",
                "url": c.get("url") or f"https://clips.twitch.tv/{c['slug']}",
                "category": ((c.get("game") or {}).get("displayName") or ""),
                "created": c.get("createdAt") or "",
            })
    return out


def kick_clips(slug: str, name: str = "", avatar: str = "", per: int = 4) -> list[dict]:
    r = S.session().get(f"https://kick.com/api/v2/channels/{slug}/clips?cursor=0&sort=view&time=week", timeout=15)
    if r.status_code != 200:
        log.warning("kick clips %s: http %s", slug, r.status_code)
        return []
    out = []
    for c in (r.json() or {}).get("clips") or []:
        if not c.get("id"):
            continue
        out.append({
            "platform": "kick", "slug": slug, "key": f"kick:{slug}",
            "name": name or ((c.get("channel") or {}).get("username")) or slug, "avatar": avatar,
            "id": c["id"], "title": (c.get("title") or "")[:120], "views": int(c.get("view_count") or 0),
            "duration_s": int(c.get("duration") or 0), "thumb": c.get("thumbnail_url") or "",
            "url": f"https://kick.com/{slug}/clips/{c['id']}",
            "category": ((c.get("category") or {}).get("name") or ""),
            "created": c.get("created_at") or "",
        })
        if len(out) >= per:
            break
    return out


def collect(streams: list[S.Stream]) -> list[dict]:
    """Rank the week's clips across the wall: by views, at most PER_STREAMER each, TOP_N total."""
    tw = [s.slug for s in streams if s.platform == "twitch"]
    found: list[dict] = []
    try:
        found += twitch_clips(tw)
    except Exception as e:  # noqa: BLE001
        log.warning("twitch clips: %s", e)
    for s in streams:
        if s.platform != "kick":
            continue
        try:
            found += kick_clips(s.slug, s.name, s.avatar)
        except Exception as e:  # noqa: BLE001
            log.warning("kick clips %s: %s", s.slug, e)
    return rank(found)


MIN_PER_PLATFORM = 2  # Kick rooms are smaller than Twitch rooms; the strip still shows both


def rank(found: list[dict]) -> list[dict]:
    found = sorted((c for c in found if c.get("thumb")), key=lambda c: -c["views"])
    per: dict[str, int] = {}
    out: list[dict] = []
    for c in found:
        if per.get(c["key"], 0) >= PER_STREAMER:
            continue
        per[c["key"]] = per.get(c["key"], 0) + 1
        out.append(c)
        if len(out) >= TOP_N:
            break
    chosen = {c["id"] for c in out}
    for plat in ("twitch", "kick"):
        have = sum(1 for c in out if c["platform"] == plat)
        extra = [c for c in found if c["platform"] == plat and c["id"] not in chosen]
        while have < MIN_PER_PLATFORM and extra:
            c = extra.pop(0)
            if per.get(c["key"], 0) >= PER_STREAMER:
                continue
            per[c["key"]] = per.get(c["key"], 0) + 1
            if len(out) >= TOP_N:
                # drop the weakest clip of the other platform
                victims = [i for i in range(len(out) - 1, -1, -1) if out[i]["platform"] != plat]
                if not victims:
                    break
                out.pop(victims[0])
            out.append(c); chosen.add(c["id"]); have += 1
    out.sort(key=lambda c: -c["views"])
    return out


def doc(streams: list[S.Stream]) -> dict:
    return {"updated": int(time.time()), "period": "7d", "clips": collect(streams)}
