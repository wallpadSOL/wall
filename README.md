<a href="https://wallpad.run"><img src="https://wallpad.run/assets/brand/banner.png" width="100%" alt="WALL. Every stream gets a coin. The chat cuts the clips."></a>

# WALL

A launchpad for live streams on Solana. Live at **[wallpad.run](https://wallpad.run)**, the agent at `api.wallpad.run`, the account **[@justwallpad](https://x.com/justwallpad)** on X.

Every coin on the wall is tied to one live Kick or Twitch stream. An agent sits in that stream's chat, counts messages in ten-second ticks, and the moment the chat runs three times its own last minute it cuts a 30-second clip and writes the verdict to a public log. One rule, the same for every stream, printed on the site. The agent counts and cuts. It never posts and never trades.

The platform wallet creates every coin, so every coin's creator fees land in one place. Once an hour they are split: 40% buys and burns $WALL, 40% goes to the people who launched the coins (pro rata by last-hour volume), 20% to the platform. Every movement has a signature on the fee page.

## What is in here

```
agent/        the agent, pure python
  detector.py   the rule. one function, no state, tested
  streams.py    twitch and kick: top streams, stream info, anonymous chat readers
  state.py      per-stream watch windows, state.json builder
  clipper.py    streamlink + ffmpeg ring buffer, the cut
  trending.py   the week's most-watched clips of the streamers on the wall (twitch + kick)
  solana.py     tiny rpc client with failover, keypair, burn instruction, payment check
  launch.py     pump.fun create via PumpPortal, creator fee collect
  fees.py       the hourly round: collect, split, buy and burn, pay launchers, sweep the hot wallet to cold
  server.py     http on 127.0.0.1:8791: state, clips, coins, fees, trending, events, POST /launch
  main.py       the loop: roster, chats, verdicts, cuts, the public event log
  __main__.py   python -m agent run | fees | keygen | status | check
site/         the static site (netlify): index, launch, coin, cuts, fees
  sample/       sample data so the site renders on localhost without an agent
deploy/       install.sh, push.sh, systemd units, Caddyfile
tests/        20 tests, python -m pytest -q tests
```

## Run it on a box

Ubuntu or Debian, one command, safe to run twice:

```
git clone https://github.com/wallpadSOL/wall.git && cd wall
sudo bash deploy/install.sh
```

It installs ffmpeg, caddy and a venv, creates the `wall` user, puts the agent under `/opt/wall`, makes the hot wallet keypair at `/opt/wall/hot-wallet.json` (chmod 600, never leaves the box), and starts `wall-agent`. Then:

```
sudo -u wall /opt/wall/venv/bin/python -m agent -c /opt/wall/config.toml check
```

prints what works on that machine. A healthy box looks like this:

```
ffmpeg     /usr/bin/ffmpeg
streamlink /opt/wall/venv/bin/streamlink
websockets ok
requests   ok
solders    ok
curl_cffi  ok
twitch     ok: kaicenat 61234, xqc 40210, jynxzi 38000
kick       ok: clavicular 21400, adinross 18800, ...
```

`twitch FAIL` or `kick FAIL` means the box cannot reach that platform. Everything else works without it.

Then:

```
journalctl -u wall-agent -f                                             # watch
sudo -u wall /opt/wall/venv/bin/python -m agent -c /opt/wall/config.toml status   # the wall in text
curl -s 127.0.0.1:8791/state.json | head -c 400                         # what the site reads
```

## Turn the money on

Nothing financial runs until you say so. `[solana] enabled = false` by default.

1. Point `api.wallpad.run` at the box. Caddy gets the certificate on its own.
2. Fund the hot wallet printed by `install.sh` with about 0.2 SOL. It pays creation rent and the first buy of every coin. It is pocket money, not a treasury.
3. In `/opt/wall/config.toml` set `[solana] enabled = true`, then `sudo systemctl restart wall-agent`. The launch page switches on by itself (it reads `launch.json`).
4. When the dev deploys $WALL, set `wall_mint = "<mint>"` and `min_wall_hold`. Until then the burn share is held in the hot wallet and shown as waiting on the fee page.
5. `sudo systemctl enable --now wall-fees.timer` starts the hourly round. Try one first: `python -m agent -c /opt/wall/config.toml fees --dry-run`.

The hot wallet keeps a float (`hot_float_sol`, 0.15 by default). After every round whatever sits above the float and the parked burn share is swept to the cold wallet, so the box never holds more than pocket money.

## The site

`site/` is static. Netlify publishes it as is (`netlify.toml`). On `localhost` or `file://` it reads `site/sample/*.json`, so it renders without an agent:

```
cd site && python3 -m http.server 8765
```

In production it reads `https://api.wallpad.run/*.json` every two seconds. The chip top right says whether the agent is live, lagging, or down, from the age of `state.json`.

## The rule

```
rate       = messages in the last complete tick / 10
baseline   = messages in the 6 ticks before it / 60
multiplier = rate / max(baseline, 0.3)
SPIKE      = multiplier >= 3.0 and rate >= 2.0 and not cooling
WARM       = multiplier >= 1.5
cooldown   = 90 s after a SPIKE
warm-up    = 70 s after joining: no verdict before the minute is seen
```

Ticks are aligned to the clock, so anyone replaying a chat log gets the same counts. The numbers live in `[detector]` in the config and nowhere else. There is no per-stream setting.

## Hard rules

- The agent never posts. Clips go to a queue (`clips.json`), a person posts them from the coin's account.
- The agent never trades. The hot wallet creates coins, makes the first buy, collects fees, swaps the burn share, burns it. It never holds anyone's coin.
- Never predicts. Counts, rates, ratios. No score.
- Never tunes per stream.
- Never hides a transaction. `fees.json` carries a signature for every movement.
- The hot wallet file stays on the box. The cold wallet is never on a server.

## Config

`config.example.toml` is the whole thing. `[agent] pinned` are streams always on the wall, `auto_top_twitch` / `auto_top_kick` fill the rest with the loudest live streams, `max_streams` caps the box.

## Tests

```
python3 -m pytest -q tests
```
