"""Where the fees go. Runs on a timer, writes every transaction to fees.json.

Each round:

    1. collect creator fees from pump.fun into the hot wallet
    2. measure what arrived (balance after minus balance before)
    3. split it: burn_share buys $WALL on Jupiter and burns it,
       launcher_share goes to the people who launched the coins, pro rata by
       each coin's trading volume since the last round,
       platform_share goes to the cold wallet
    4. append a round record with every signature

If $WALL has no mint yet (before the dev deploys it), the burn share is held
in the hot wallet (tracked as `burn_backlog`) and spent on the first round
after the mint exists. After every round the hot wallet is swept: whatever
is above its float plus the backlog (launch fees, leftovers) goes to the cold
wallet, so the server never sits on more than pocket money. Nothing is ever
moved without a signature that anyone can open on Solscan.
"""

from __future__ import annotations

import base64
import logging
import time

import requests
from solders.pubkey import Pubkey

from . import solana as sol

log = logging.getLogger("wall.fees")

JUP = "https://lite-api.jup.ag/swap/v1"
DEXSCREENER_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/{mints}"


def split(lamports: int, burn_share: float, launcher_share: float, platform_share: float) -> dict:
    """Integer split of a fee amount. Rounding dust stays with the platform."""
    if lamports <= 0:
        return {"burn": 0, "launchers": 0, "platform": 0}
    assert abs(burn_share + launcher_share + platform_share - 1.0) < 1e-9
    burn = int(lamports * burn_share)
    launchers = int(lamports * launcher_share)
    platform = lamports - burn - launchers
    return {"burn": burn, "launchers": launchers, "platform": platform}


def pro_rata(total: int, weights: dict[str, float]) -> dict[str, int]:
    """Share `total` lamports across keys by weight; dust goes to the heaviest."""
    if total <= 0 or not weights:
        return {}
    wsum = sum(max(0.0, w) for w in weights.values())
    if wsum <= 0:
        n = len(weights)
        base = total // n
        out = {k: base for k in weights}
    else:
        out = {k: int(total * max(0.0, w) / wsum) for k, w in weights.items()}
    dust = total - sum(out.values())
    if dust:
        top = max(weights, key=lambda k: weights[k])
        out[top] += dust
    return out


def volumes_since(mints: list[str], hours: float = 1.0) -> dict[str, float]:
    """Trading volume per mint over the last hour from Dexscreener (h1)."""
    out: dict[str, float] = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try:
            r = requests.get(DEXSCREENER_TOKENS.format(mints=",".join(chunk)), timeout=20)
            for p in r.json() if r.status_code == 200 else []:
                m = (p.get("baseToken") or {}).get("address")
                v = float(((p.get("volume") or {}).get("h1")) or 0)
                if m:
                    out[m] = out.get(m, 0.0) + v
        except Exception as e:  # noqa: BLE001
            log.warning("dexscreener volumes: %s", e)
    return out


class Treasury:
    def __init__(self, rpc: sol.Rpc, hot, cold: str, wall_mint: str, shares: dict,
                 min_round_sol: float = 0.01, slippage_bps: int = 300, dry_run: bool = False,
                 hot_float_sol: float = 0.15, burn_backlog: int = 0):
        self.rpc = rpc
        self.hot = hot
        self.cold = cold
        self.wall_mint = wall_mint
        self.shares = shares
        self.min_round = int(min_round_sol * sol.LAMPORTS)
        self.slippage_bps = slippage_bps
        self.dry_run = dry_run
        self.hot_float = int(hot_float_sol * sol.LAMPORTS)   # what the hot wallet keeps to create coins
        self.burn_backlog = int(burn_backlog)                 # burn share parked until $WALL has a mint

    @property
    def pubkey(self) -> str:
        return str(self.hot.pubkey())

    # --------------------------------------------------------- buyback

    def buy_wall(self, lamports: int) -> tuple[str, int]:
        """Swap SOL -> $WALL on Jupiter. Returns (signature, raw tokens bought)."""
        q = requests.get(f"{JUP}/quote", params={
            "inputMint": sol.SOL_MINT, "outputMint": self.wall_mint, "amount": lamports,
            "slippageBps": self.slippage_bps, "restrictIntermediateTokens": "true",
        }, timeout=30)
        if q.status_code != 200:
            raise RuntimeError(f"jupiter quote: {q.status_code} {q.text[:200]}")
        quote = q.json()
        s = requests.post(f"{JUP}/swap", json={
            "quoteResponse": quote, "userPublicKey": self.pubkey, "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": "auto",
        }, timeout=30)
        if s.status_code != 200:
            raise RuntimeError(f"jupiter swap: {s.status_code} {s.text[:200]}")
        raw = base64.b64decode(s.json()["swapTransaction"])
        sig = self.rpc.send_raw(sol.sign_versioned(raw, [self.hot]))
        self.rpc.confirm(sig, 90)
        return sig, int(quote.get("outAmount") or 0)

    def burn_wall(self) -> tuple[str, int]:
        amt, _dec = self.rpc.token_raw_balance(self.pubkey, self.wall_mint)
        if amt <= 0:
            return "", 0
        ix = sol.burn_instruction(self.hot.pubkey(), Pubkey.from_string(self.wall_mint), amt)
        sig = sol.send_legacy(self.rpc, self.hot, [ix])
        return sig, amt

    # --------------------------------------------------------- a round

    def run_round(self, coins: list[dict], collect) -> dict:
        """One pass. `collect` is a callable that claims creator fees and returns a sig."""
        rec = {"ts": int(time.time()), "collect_sig": None, "collected": 0, "split": {},
               "burn": None, "launchers": [], "platform": None, "note": None, "dry_run": self.dry_run}
        before = self.rpc.balance(self.pubkey)
        if not self.dry_run:
            try:
                rec["collect_sig"] = collect()
            except Exception as e:  # noqa: BLE001
                rec["note"] = f"collect: {e}"
                log.warning("collect: %s", e)
        after = self.rpc.balance(self.pubkey)
        collected = max(0, after - before)
        rec["collected"] = collected
        if collected < self.min_round:
            rec["note"] = (rec["note"] or "") + f" below minimum ({collected} lamports), carried over"
            rec["sweep"] = self.sweep()
            rec["burn_backlog"] = self.burn_backlog
            return rec
        parts = split(collected, self.shares["burn"], self.shares["launchers"], self.shares["platform"])
        rec["split"] = parts
        if self.dry_run:
            return rec

        # burn share (plus whatever was parked while $WALL had no mint)
        burn_lamports = parts["burn"] + self.burn_backlog
        if self.wall_mint:
            try:
                buy_sig, bought = self.buy_wall(burn_lamports)
                burn_sig, burned = self.burn_wall()
                rec["burn"] = {"lamports": burn_lamports, "buy_sig": buy_sig, "burn_sig": burn_sig,
                               "tokens_raw": burned}
                self.burn_backlog = 0
            except Exception as e:  # noqa: BLE001
                rec["burn"] = {"lamports": burn_lamports, "error": str(e)}
                self.burn_backlog = burn_lamports
                log.warning("buyback: %s", e)
        else:
            self.burn_backlog = burn_lamports
            rec["burn"] = {"lamports": burn_lamports, "held": True, "note": "waiting for $WALL mint"}

        # launcher share, pro rata by last-hour volume
        mints = [c["mint"] for c in coins]
        vols = volumes_since(mints)
        weights = {c["launcher"]: 0.0 for c in coins}
        for c in coins:
            weights[c["launcher"]] += vols.get(c["mint"], 0.0)
        for who, lam in pro_rata(parts["launchers"], weights).items():
            if lam < 5000:
                continue
            try:
                sig = sol.transfer_sol(self.rpc, self.hot, who, lam)
                rec["launchers"].append({"to": who, "lamports": lam, "sig": sig})
            except Exception as e:  # noqa: BLE001
                rec["launchers"].append({"to": who, "lamports": lam, "error": str(e)})

        # platform share
        try:
            sig = sol.transfer_sol(self.rpc, self.hot, self.cold, parts["platform"])
            rec["platform"] = {"to": self.cold, "lamports": parts["platform"], "sig": sig}
        except Exception as e:  # noqa: BLE001
            rec["platform"] = {"to": self.cold, "lamports": parts["platform"], "error": str(e)}
        rec["sweep"] = self.sweep()
        rec["burn_backlog"] = self.burn_backlog
        return rec

    # --------------------------------------------------------- sweep

    def sweep_amount(self, balance: int) -> int:
        """Lamports above the float and the parked burn share, minus a fee reserve. 0 when not worth a tx."""
        keep = self.hot_float + self.burn_backlog + 10_000
        extra = balance - keep
        return extra if extra >= 1_000_000 else 0   # 0.001 SOL: below that the transfer is noise

    def sweep(self) -> dict | None:
        """Launch fees and leftovers go to the cold wallet; the hot wallet keeps its float."""
        try:
            bal = self.rpc.balance(self.pubkey)
        except Exception as e:  # noqa: BLE001
            return {"error": f"balance: {e}"}
        lam = self.sweep_amount(bal)
        if lam <= 0:
            return None
        if self.dry_run:
            return {"to": self.cold, "lamports": lam, "dry_run": True}
        try:
            sig = sol.transfer_sol(self.rpc, self.hot, self.cold, lam)
            return {"to": self.cold, "lamports": lam, "sig": sig}
        except Exception as e:  # noqa: BLE001
            return {"to": self.cold, "lamports": lam, "error": str(e)}
