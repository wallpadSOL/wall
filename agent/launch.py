"""Launching a coin on pump.fun for a stream, from the platform's hot wallet.

Flow, all server-side after the launcher paid the launch fee to the hot
wallet from their own Phantom:

    1. verify the payment signature on chain (amount, recipient, unused)
    2. upload name / ticker / image to pump.fun's metadata store
    3. ask PumpPortal for an unsigned `create` transaction
    4. sign with the new mint keypair + the hot wallet, send, confirm
    5. record the coin against the stream

The hot wallet is the creator of every coin on the wall, so every coin's
creator fees accrue to it. fees.py splits them on a timer and logs each
transaction. The hot wallet holds pocket money only; the cold wallet never
touches a server.
"""

from __future__ import annotations

import logging
import re
import time

import requests
from solders.keypair import Keypair

from . import solana as sol

log = logging.getLogger("wall.launch")

PUMPPORTAL_LOCAL = "https://pumpportal.fun/api/trade-local"
PUMP_IPFS = "https://pump.fun/api/ipfs"

TICKER_RE = re.compile(r"^[A-Z0-9]{2,10}$")
NAME_RE = re.compile(r"^[\w .\-'!]{2,32}$")


class LaunchError(Exception):
    pass


def validate(name: str, ticker: str, description: str) -> tuple[str, str, str]:
    name = (name or "").strip()
    ticker = (ticker or "").strip().upper().lstrip("$")
    description = (description or "").strip()[:300]
    if not NAME_RE.match(name):
        raise LaunchError("name: 2-32 letters, digits, spaces")
    if not TICKER_RE.match(ticker):
        raise LaunchError("ticker: 2-10 letters or digits")
    return name, ticker, description


def upload_metadata(name: str, ticker: str, description: str, image: bytes, mime: str,
                    website: str = "", twitter: str = "") -> str:
    files = {"file": (f"{ticker.lower()}.{'png' if 'png' in mime else 'jpg'}", image, mime)}
    data = {"name": name, "symbol": ticker, "description": description, "showName": "true"}
    if website:
        data["website"] = website
    if twitter:
        data["twitter"] = twitter
    r = requests.post(PUMP_IPFS, data=data, files=files, timeout=60)
    if r.status_code != 200:
        raise LaunchError(f"metadata upload: http {r.status_code} {r.text[:200]}")
    uri = r.json().get("metadataUri")
    if not uri:
        raise LaunchError("metadata upload: no uri in response")
    return uri


def build_create_tx(creator_pubkey: str, mint_pubkey: str, name: str, ticker: str, uri: str,
                    dev_buy_sol: float, slippage: int = 10, priority_fee: float = 0.0005) -> bytes:
    body = {
        "publicKey": creator_pubkey,
        "action": "create",
        "tokenMetadata": {"name": name, "symbol": ticker, "uri": uri},
        "mint": mint_pubkey,
        "denominatedInSol": "true",
        "amount": dev_buy_sol,
        "slippage": slippage,
        "priorityFee": priority_fee,
        "pool": "pump",
    }
    r = requests.post(PUMPPORTAL_LOCAL, json=body, timeout=60)
    if r.status_code != 200:
        raise LaunchError(f"pumpportal create: http {r.status_code} {r.text[:200]}")
    return r.content


def build_collect_tx(creator_pubkey: str, priority_fee: float = 0.000001) -> bytes:
    body = {"publicKey": creator_pubkey, "action": "collectCreatorFee", "priorityFee": priority_fee}
    r = requests.post(PUMPPORTAL_LOCAL, json=body, timeout=60)
    if r.status_code != 200:
        raise LaunchError(f"pumpportal collect: http {r.status_code} {r.text[:200]}")
    return r.content


class Launcher:
    def __init__(self, rpc: sol.Rpc, hot: Keypair, launch_fee_sol: float, dev_buy_sol: float,
                 site: str = "", twitter: str = "", wall_mint: str = "", min_wall_hold: float = 0.0):
        self.rpc = rpc
        self.hot = hot
        self.launch_fee_lamports = int(launch_fee_sol * sol.LAMPORTS)
        self.dev_buy_sol = dev_buy_sol
        self.site = site
        self.twitter = twitter
        self.wall_mint = wall_mint
        self.min_wall_hold = min_wall_hold
        self.used_sigs: set[str] = set()

    @property
    def pubkey(self) -> str:
        return str(self.hot.pubkey())

    def check_payment(self, payment_sig: str, payer: str) -> int:
        if not payment_sig or payment_sig in self.used_sigs:
            raise LaunchError("payment: missing or already used")
        tx = self.rpc.transaction(payment_sig)
        if tx is None:
            raise LaunchError("payment: transaction not found yet, try again in a few seconds")
        got = sol.sol_received(tx, self.pubkey)
        if got < self.launch_fee_lamports:
            raise LaunchError(f"payment: got {got / sol.LAMPORTS:.4f} SOL, need "
                              f"{self.launch_fee_lamports / sol.LAMPORTS:.4f}")
        keys = [k["pubkey"] if isinstance(k, dict) else str(k)
                for k in tx["transaction"]["message"]["accountKeys"]]
        if payer not in keys:
            raise LaunchError("payment: payer wallet is not in that transaction")
        return got

    def check_hold(self, payer: str) -> None:
        if self.wall_mint and self.min_wall_hold > 0:
            have = self.rpc.token_balance(payer, self.wall_mint)
            if have < self.min_wall_hold:
                raise LaunchError(f"launching needs {self.min_wall_hold:g} $WALL in the wallet, have {have:g}")

    def create(self, *, stream_key: str, name: str, ticker: str, description: str,
               image: bytes, mime: str, payer: str, payment_sig: str) -> dict:
        name, ticker, description = validate(name, ticker, description)
        self.check_hold(payer)
        paid = self.check_payment(payment_sig, payer)
        uri = upload_metadata(name, ticker, description, image, mime, self.site, self.twitter)
        mint = Keypair()
        raw = build_create_tx(self.pubkey, str(mint.pubkey()), name, ticker, uri, self.dev_buy_sol)
        signed = sol.sign_versioned(raw, [mint, self.hot])
        sig = self.rpc.send_raw(signed)
        self.rpc.confirm(sig, 90)
        self.used_sigs.add(payment_sig)
        rec = {
            "mint": str(mint.pubkey()), "name": name, "ticker": ticker, "description": description,
            "stream": stream_key, "launcher": payer, "paid_lamports": paid, "payment_sig": payment_sig,
            "create_sig": sig, "created": int(time.time()), "uri": uri,
            "pump": f"https://pump.fun/coin/{mint.pubkey()}",
        }
        log.info("created %s (%s) for %s by %s", ticker, rec["mint"], stream_key, payer)
        return rec

    def collect_creator_fees(self) -> str:
        raw = build_collect_tx(self.pubkey)
        signed = sol.sign_versioned(raw, [self.hot])
        sig = self.rpc.send_raw(signed)
        self.rpc.confirm(sig, 90)
        return sig
