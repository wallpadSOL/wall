import json
import os
import threading
import time
import urllib.request

import pytest

from agent import detector as d
from agent import fees
from agent import solana as sol
from agent.server import Store, serve
from agent.state import Watch, build_state, evaluate_watch, rate_now
from agent.streams import Stream


# ------------------------------------------------------------- state

def test_watch_rates_and_state_doc():
    s = Stream(platform="twitch", slug="xqc", name="xQc", viewers=50000, live=True, url="https://twitch.tv/xqc")
    w = Watch(stream=s, joined=1000)
    # 6/s for a minute, then 20/s
    for t in range(1000, 1060):
        for _ in range(6):
            w.add(t + 0.1)
    for t in range(1060, 1070):
        for _ in range(20):
            w.add(t + 0.1)
    v = evaluate_watch(w, 1075)
    assert v.state == d.SPIKE and v.rate == 20.0 and v.baseline == 6.0
    doc = build_state([w], 1075.0, {}, {}, started=900.0)
    st = doc["streams"][0]
    assert st["state"] == "SPIKE" and st["line"] == 18.0 and st["slug"] == "xqc"
    assert "chatroom_id" not in st
    assert doc["rule"]["spike_x"] == 3.0
    assert rate_now(w, 1070.0) == 20.0


def test_prune_keeps_three_minutes():
    w = Watch(stream=Stream("kick", "a"), joined=0)
    for t in range(0, 400):
        w.add(t)
    w.prune(400)
    assert w.times[0] >= 220


# ------------------------------------------------------------- fees

def test_split_is_exact_and_dust_goes_to_platform():
    p = fees.split(1_000_000_001, 0.4, 0.4, 0.2)
    assert p["burn"] == 400_000_000 and p["launchers"] == 400_000_000
    assert p["burn"] + p["launchers"] + p["platform"] == 1_000_000_001
    assert fees.split(0, 0.4, 0.4, 0.2) == {"burn": 0, "launchers": 0, "platform": 0}


def test_pro_rata_by_volume_and_equal_when_no_volume():
    out = fees.pro_rata(1000, {"a": 300.0, "b": 100.0})
    assert out == {"a": 750, "b": 250}
    out = fees.pro_rata(1001, {"a": 0.0, "b": 0.0})
    assert sum(out.values()) == 1001
    assert fees.pro_rata(0, {"a": 1}) == {}


def test_sol_received_reads_balance_delta():
    tx = {"meta": {"err": None, "preBalances": [10, 5], "postBalances": [4, 11]},
          "transaction": {"message": {"accountKeys": [{"pubkey": "payer"}, {"pubkey": "hot"}]}}}
    assert sol.sol_received(tx, "hot") == 6
    assert sol.sol_received(tx, "payer") == 0
    assert sol.sol_received(None, "hot") == 0
    tx["meta"]["err"] = {"x": 1}
    assert sol.sol_received(tx, "hot") == 0


def test_burn_instruction_layout():
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    owner = Keypair().pubkey()
    mint = Pubkey.new_unique()
    ix = sol.burn_instruction(owner, mint, 123)
    assert ix.program_id == sol.TOKEN_PROGRAM
    assert ix.data[0] == 8 and int.from_bytes(ix.data[1:9], "little") == 123
    assert ix.accounts[1].pubkey == mint and ix.accounts[2].is_signer


# ------------------------------------------------------------- server

class FakeLauncher:
    def create(self, **kw):
        if kw["ticker"].upper() == "BAD":
            raise RuntimeError("ticker rejected")
        return {"mint": "M1", "ticker": kw["ticker"].upper(), "stream": kw["stream_key"],
                "launcher": kw["payer"], "created": 1}


@pytest.fixture
def http(tmp_path):
    store = Store(str(tmp_path), FakeLauncher())
    store.write("state.json", {"updated": 1, "streams": []})
    os.makedirs(tmp_path / "clips")
    (tmp_path / "clips" / "a.mp4").write_bytes(b"\x00" * 10)
    srv = serve(store, "127.0.0.1", 0)
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}", store
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read(), dict(r.headers)


def _post(url, doc):
    req = urllib.request.Request(url, data=json.dumps(doc).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_serves_files_and_clips(http):
    base, _ = http
    code, body, hdr = _get(base + "/state.json")
    assert code == 200 and json.loads(body)["updated"] == 1
    assert hdr["Access-Control-Allow-Origin"] == "*"
    code, body, hdr = _get(base + "/clips/a.mp4")
    assert code == 200 and hdr["Content-Type"] == "video/mp4" and len(body) == 10
    code, body, _ = _get(base + "/coins.json")
    assert code == 200 and json.loads(body) == []
    with pytest.raises(urllib.error.HTTPError):
        _get(base + "/clips/../config.toml")


def test_server_launch_records_coin(http):
    base, store = http
    import base64
    img = base64.b64encode(b"\x89PNG" + b"\x00" * 2000).decode()
    code, doc = _post(base + "/launch", {"stream": "kick:clavicular", "name": "The Boat", "ticker": "boat",
                                         "image_b64": img, "mime": "image/png", "payer": "P", "payment_sig": "S"})
    assert code == 200 and doc["coin"]["ticker"] == "BOAT"
    assert store.read("coins.json", [])[0]["mint"] == "M1"
    code, doc = _post(base + "/launch", {"stream": "x", "name": "x", "ticker": "bad", "image_b64": img,
                                         "mime": "image/png", "payer": "P", "payment_sig": "S"})
    assert code == 400 and "rejected" in doc["error"]
    code, doc = _post(base + "/launch", {"image_b64": "zz"})
    assert code == 400


def test_history_is_rate_per_tick_oldest_first():
    from agent.state import Watch, history
    from agent.streams import Stream
    from agent import detector as d
    now = 1_000_000
    w = Watch(stream=Stream(platform="kick", slug="x", name="x"), joined=now - 300)
    last = d.tick_start(now, 10) - 10
    for i in range(30):          # 3 msg/s in the last complete tick
        w.add(last + i / 3)
    for i in range(5):           # 0.5 msg/s in the one before
        w.add(last - 10 + i * 2)
    h = history(w, now, d.DEFAULTS, ticks=4)
    assert len(h) == 4
    assert h[-1] == 3.0 and h[-2] == 0.5 and h[0] == 0.0


def test_trending_ranks_by_views_with_diversity(monkeypatch):
    from agent import trending, streams as S
    mk = lambda key, views, i: {"platform": key.split(":")[0], "slug": key.split(":")[1], "key": key, "name": key, "avatar": "",
                                "id": f"{key}-{i}", "title": "t", "views": views, "duration_s": 30, "thumb": "x.jpg",
                                "url": "u", "category": "", "created": ""}
    monkeypatch.setattr(trending, "twitch_clips", lambda logins, per=4: [mk("twitch:a", 100 - i, i) for i in range(4)])
    monkeypatch.setattr(trending, "kick_clips", lambda slug, name="", avatar="", per=4: [mk(f"kick:{slug}", 50, 0), mk(f"kick:{slug}", 40, 1), mk(f"kick:{slug}", 30, 2)])
    streams = [S.Stream("twitch", "a"), S.Stream("kick", "b")]
    out = trending.collect(streams)
    assert [c["views"] for c in out] == [100, 99, 50, 40]      # two per streamer, by views
    assert all(c["thumb"] for c in out)


# ------------------------------------------------------------- treasury sweep

class _Rpc:
    def __init__(self, bal): self.bal = bal
    def balance(self, _pk): return self.bal


class _Hot:
    def pubkey(self): return "hot"


def test_sweep_keeps_float_and_backlog(monkeypatch):
    from agent.fees import Treasury
    t = Treasury(_Rpc(0), _Hot(), "cold", "", {"burn": .4, "launchers": .4, "platform": .2},
                 hot_float_sol=0.15, burn_backlog=20_000_000)
    assert t.sweep_amount(100_000_000) == 0                       # 0.1 SOL: below the float, nothing moves
    assert t.sweep_amount(170_000_000) == 0                       # float + backlog + reserve = 0.17001, nothing
    assert t.sweep_amount(200_000_000) == 200_000_000 - 150_000_000 - 20_000_000 - 10_000
    t2 = Treasury(_Rpc(0), _Hot(), "cold", "", {"burn": .4, "launchers": .4, "platform": .2}, hot_float_sol=0.15)
    assert t2.sweep_amount(150_500_000) == 0                      # 0.0005 above: not worth a transaction
    sent = []
    monkeypatch.setattr("agent.fees.sol.transfer_sol", lambda rpc, hot, to, lam: sent.append((to, lam)) or "sig")
    t3 = Treasury(_Rpc(400_000_000), _Hot(), "cold", "", {"burn": .4, "launchers": .4, "platform": .2}, hot_float_sol=0.15)
    rec = t3.sweep()
    assert rec["sig"] == "sig" and sent == [("cold", 400_000_000 - 150_000_000 - 10_000)]


# ------------------------------------------------------------- event log

def test_events_log_transitions_and_outside_writes(tmp_path):
    import json as _json
    from types import SimpleNamespace
    from agent.main import Agent, Config
    from agent.server import Store
    cfg = Config(data_dir=str(tmp_path), clipper=False)
    a = Agent(cfg, Store(str(tmp_path)))
    assert [e["kind"] for e in a.events] == ["boot"]
    s = SimpleNamespace(key="twitch:x", name="X", slug="x", platform="twitch", viewers=10, live=True)
    w = SimpleNamespace(stream=s)
    V = lambda st, m: SimpleNamespace(state=st, multiplier=m, rate=m * 2)
    a._verdict_event(w, V("QUIET", 1.0), 1000); a._verdict_event(w, V("WARM", 1.7), 1010)
    a._verdict_event(w, V("WARM", 1.8), 1020); a._verdict_event(w, V("QUIET", 0.9), 1030)
    a._verdict_event(w, V("WARM", 1.6), 1040)   # within two minutes: no second warm
    kinds = [e["kind"] for e in a.events]
    assert kinds == ["boot", "warm", "calm"]
    _json.dump([{"ticker": "AAA", "name": "a", "mint": "m1", "stream": "twitch:x", "launcher": "L"}], open(tmp_path / "coins.json", "w"))
    _json.dump([{"ts": 1, "collected": 5000000, "split": {"burn": 2000000, "launchers": 2000000, "platform": 1000000}}], open(tmp_path / "fees.json", "w"))
    a._outside_events(); a._outside_events()
    kinds = [e["kind"] for e in a.events]
    assert kinds == ["boot", "warm", "calm", "launch", "fees"]
    assert a.events[-2]["ticker"] == "AAA" and a.events[-1]["collected"] == 5000000
