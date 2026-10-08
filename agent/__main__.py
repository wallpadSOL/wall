"""wall: the command line.

    python -m agent run                 read chats, cut clips, serve files
    python -m agent fees                one treasury round (cron / timer)
    python -m agent keygen              make the hot wallet keypair file
    python -m agent status              print the wall from state.json
    python -m agent check               what works on this machine
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from . import detector as d
from .main import Config, run as run_agent


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def cfg_from(doc: dict) -> Config:
    a, s, c, det = doc.get("agent", {}), doc.get("server", {}), doc.get("clipper", {}), doc.get("detector", {})
    params = d.Params(
        tick_s=int(det.get("tick_s", 10)), baseline_ticks=int(det.get("baseline_ticks", 6)),
        floor_per_s=float(det.get("floor_per_s", 2.0)), spike_x=float(det.get("spike_x", 3.0)),
        warm_x=float(det.get("warm_x", 1.5)), cooldown_s=int(det.get("cooldown_s", 90)),
        baseline_min_per_s=float(det.get("baseline_min_per_s", 0.3)),
    )
    return Config(
        data_dir=a.get("data_dir", "./data"), pinned=list(a.get("pinned", [])),
        auto_top_twitch=int(a.get("auto_top_twitch", 3)), auto_top_kick=int(a.get("auto_top_kick", 3)),
        twitch_languages=list(a.get("twitch_languages", ["en"])), max_streams=int(a.get("max_streams", 8)),
        categories=list(a.get("categories", ["Just Chatting", "IRL"])),
        exclude_categories=list(a.get("exclude_categories", ["Slots & Casino"])),
        refresh_s=int(a.get("refresh_s", 60)), host=s.get("host", "127.0.0.1"), port=int(s.get("port", 8791)),
        cors=s.get("cors", "*"), clipper=bool(c.get("enabled", True)), quality=c.get("quality", "480p,360p,worst"),
        clip_s=int(c.get("clip_s", 30)), params=params,
    )


def make_launcher(doc: dict):
    so = doc.get("solana", {})
    if not so.get("enabled", False):
        return None
    from . import solana as sol
    from .launch import Launcher
    rpc = sol.Rpc(so.get("rpc", "https://api.mainnet-beta.solana.com"))
    hot = sol.load_keypair(so.get("hot_key_file"))
    return Launcher(rpc, hot, float(so.get("launch_fee_sol", 0.05)), float(so.get("dev_buy_sol", 0.01)),
                    site=so.get("site", ""), twitter=so.get("twitter", ""),
                    wall_mint=so.get("wall_mint", ""), min_wall_hold=float(so.get("min_wall_hold", 0)))


def cmd_run(args):
    doc = load_config(args.config)
    cfg = cfg_from(doc)
    launcher = None
    try:
        launcher = make_launcher(doc)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("wall").warning("launching disabled: %s", e)
    run_agent(cfg, launcher)


def cmd_fees(args):
    doc = load_config(args.config)
    so, fe = doc.get("solana", {}), doc.get("fees", {})
    from . import solana as sol
    from .fees import Treasury
    from .launch import Launcher
    rpc = sol.Rpc(so.get("rpc", "https://api.mainnet-beta.solana.com"))
    hot = sol.load_keypair(so.get("hot_key_file"))
    launcher = Launcher(rpc, hot, 0, 0)
    shares = {"burn": float(fe.get("burn", 0.4)), "launchers": float(fe.get("launchers", 0.4)),
              "platform": float(fe.get("platform", 0.2))}
    data = cfg_from(doc).data_dir
    log = _read(os.path.join(data, "fees.json"), [])
    backlog = next((r.get("burn_backlog", 0) for r in reversed(log) if "burn_backlog" in r), 0)
    t = Treasury(rpc, hot, so["cold_wallet"], so.get("wall_mint", ""), shares,
                 min_round_sol=float(fe.get("min_round_sol", 0.01)), dry_run=args.dry_run,
                 hot_float_sol=float(so.get("hot_float_sol", 0.15)), burn_backlog=backlog)
    coins = _read(os.path.join(data, "coins.json"), [])
    rec = t.run_round(coins, launcher.collect_creator_fees)
    if not args.dry_run:
        log.append(rec)
        _write(os.path.join(data, "fees.json"), log[-2000:])
    print(json.dumps(rec, indent=2))


def cmd_keygen(args):
    from solders.keypair import Keypair
    kp = Keypair()
    path = args.out
    if os.path.exists(path):
        print(f"{path} exists, not overwriting"); sys.exit(1)
    with open(path, "w") as f:
        json.dump(list(bytes(kp)), f)
    os.chmod(path, 0o600)
    print(f"hot wallet: {kp.pubkey()}\nkeypair file: {path}\n"
          f"fund it with ~0.2 SOL for launches; it holds pocket money only")


def cmd_status(args):
    doc = load_config(args.config)
    data = cfg_from(doc).data_dir
    st = _read(os.path.join(data, "state.json"), None)
    if not st:
        print("no state yet"); return
    age = int(time.time()) - int(st.get("updated", 0))
    print(f"updated {age}s ago · {len(st['streams'])} streams · agent up {st['agent']['uptime_s']}s")
    for s in st["streams"]:
        print(f"  {s['state']:8} {s['platform']:6} {s['slug']:20} {s['viewers']:>7}  "
              f"rate {s['rate']:>5.1f}/s  base {s['baseline']:>5.1f}/s  x{s['multiplier']:<5.2f} line {s['line']}/s")


def cmd_check(args):
    import shutil
    print("ffmpeg    ", shutil.which("ffmpeg") or "MISSING")
    print("streamlink", shutil.which("streamlink") or "MISSING (pip install streamlink)")
    for mod in ("websockets", "requests", "solders", "curl_cffi"):
        try:
            __import__(mod); print(f"{mod:10} ok")
        except Exception:
            print(f"{mod:10} MISSING" + ("  (optional, helps with kick.com)" if mod == "curl_cffi" else ""))
    from . import streams as S
    try:
        top = S.twitch_top(3, ["en"])
        print("twitch    ", "ok:", ", ".join(f"{s.slug} {s.viewers}" for s in top))
    except Exception as e:  # noqa: BLE001
        print("twitch     FAIL", e)
    try:
        k = S.kick_top(3)
        print("kick      ", "ok:", ", ".join(f"{s.slug} {s.viewers}" for s in k) if k else "empty list")
    except Exception as e:  # noqa: BLE001
        print("kick       FAIL", e)
    # the chain: which rpc answers, what the wallets hold, whether launching is on
    try:
        doc = load_config(args.config)
        so = doc.get("solana", {})
        from . import solana as sol
        rpc = sol.Rpc(so.get("rpc", ""))
        hot = sol.load_keypair(so.get("hot_key_file"))
        hb = rpc.balance(str(hot.pubkey())) / sol.LAMPORTS
        print("rpc       ", "ok:", rpc.url)
        print("hot wallet", f"{hot.pubkey()}  {hb:.4f} SOL")
        if so.get("cold_wallet"):
            print("cold      ", f"{so['cold_wallet']}  {rpc.balance(so['cold_wallet']) / sol.LAMPORTS:.4f} SOL")
        print("launching ", "ON" if so.get("enabled") else "off (set [solana] enabled = true)",
              "" if hb >= 0.05 else " <- fund the hot wallet first")
    except Exception as e:  # noqa: BLE001
        print("solana     FAIL", e)


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)


def main(argv=None):
    logging.basicConfig(level=os.environ.get("WALL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="wall")
    p.add_argument("-c", "--config", default=os.environ.get("WALL_CONFIG", "config.toml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    f = sub.add_parser("fees"); f.add_argument("--dry-run", action="store_true"); f.set_defaults(fn=cmd_fees)
    k = sub.add_parser("keygen"); k.add_argument("--out", default="hot-wallet.json"); k.set_defaults(fn=cmd_keygen)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
