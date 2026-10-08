"""Thin Solana helpers: one RPC client, keypairs, signing, a burn instruction.

Kept small on purpose so every on-chain action the agent takes is readable
in one file. No framework, no hidden retries, no background threads.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time

import requests
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction, VersionedTransaction

log = logging.getLogger("wall.solana")

LAMPORTS = 1_000_000_000
SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


def load_keypair(path: str | None = None, env: str = "WALL_HOT_KEY") -> Keypair:
    """Solana CLI json array file, or a base58 secret in an env var."""
    if path and os.path.exists(path):
        with open(path) as f:
            raw = json.load(f)
        return Keypair.from_bytes(bytes(raw))
    secret = os.environ.get(env, "")
    if not secret:
        raise RuntimeError(f"no keypair: set {env} or provide a keypair file")
    return Keypair.from_base58_string(secret)


PUBLIC_RPCS = ["https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"]


class RpcError(RuntimeError):
    """The node answered and said no: a real error, not a reason to try another node."""


class Rpc:
    """JSON-RPC with failover: `url` may be one endpoint or several separated by commas.
    A 403/429/5xx or a network error on one endpoint moves to the next for that call."""

    def __init__(self, url: str):
        urls = [u.strip() for u in (url or "").split(",") if u.strip()] or PUBLIC_RPCS[:]
        for u in PUBLIC_RPCS:
            if u not in urls:
                urls.append(u)
        self.urls = urls
        self.url = urls[0]
        self.s = requests.Session()

    def call(self, method: str, params: list | None = None):
        last: Exception | None = None
        for url in self.urls:
            try:
                r = self.s.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                           "params": params or []}, timeout=30)
                if r.status_code in (403, 429) or r.status_code >= 500:
                    raise RuntimeError(f"http {r.status_code}")
                r.raise_for_status()
                j = r.json()
                if "error" in j:
                    code = (j["error"] or {}).get("code") if isinstance(j["error"], dict) else None
                    if code in (403, 429, -32005):          # forbidden, rate limited, node behind
                        raise RuntimeError(f"rpc {method}: {j['error']}")
                    raise RpcError(f"rpc {method}: {j['error']}")
                self.url = url
                return j["result"]
            except RpcError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                continue
        raise RuntimeError(f"rpc {method}: every endpoint failed ({last})")

    def balance(self, pubkey: str) -> int:
        return int(self.call("getBalance", [pubkey, {"commitment": "confirmed"}])["value"])

    def blockhash(self) -> Hash:
        v = self.call("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"]
        return Hash.from_string(v)

    def send_raw(self, tx_bytes: bytes, skip_preflight: bool = False) -> str:
        return self.call("sendTransaction", [base64.b64encode(tx_bytes).decode(),
                                             {"encoding": "base64", "skipPreflight": skip_preflight,
                                              "maxRetries": 3}])

    def confirm(self, sig: str, timeout_s: int = 60) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            st = self.call("getSignatureStatuses", [[sig]])["value"][0]
            if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                if st.get("err"):
                    raise RuntimeError(f"tx {sig} failed: {st['err']}")
                return True
            time.sleep(2)
        return False

    def transaction(self, sig: str) -> dict | None:
        return self.call("getTransaction", [sig, {"encoding": "jsonParsed",
                                                  "maxSupportedTransactionVersion": 0,
                                                  "commitment": "confirmed"}])

    def token_balance(self, owner: str, mint: str) -> float:
        res = self.call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        total = 0.0
        for acc in res.get("value", []):
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += float(info.get("uiAmount") or 0)
        return total

    def token_raw_balance(self, owner: str, mint: str) -> tuple[int, int]:
        """(raw amount, decimals) summed over the owner's accounts for a mint."""
        res = self.call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        amt, dec = 0, 0
        for acc in res.get("value", []):
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            amt += int(info.get("amount") or 0)
            dec = int(info.get("decimals") or dec)
        return amt, dec


def ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    addr, _ = Pubkey.find_program_address([bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM)
    return addr


def burn_instruction(owner: Pubkey, mint: Pubkey, amount_raw: int) -> Instruction:
    """SPL Token `Burn` (index 8): burns amount_raw from the owner's ATA."""
    data = bytes([8]) + struct.pack("<Q", amount_raw)
    return Instruction(TOKEN_PROGRAM, data, [
        AccountMeta(ata(owner, mint), is_signer=False, is_writable=True),
        AccountMeta(mint, is_signer=False, is_writable=True),
        AccountMeta(owner, is_signer=True, is_writable=False),
    ])


def send_legacy(rpc: Rpc, payer: Keypair, instructions: list[Instruction]) -> str:
    msg = Message.new_with_blockhash(instructions, payer.pubkey(), rpc.blockhash())
    tx = Transaction.new_unsigned(msg)
    tx.sign([payer], msg.recent_blockhash)
    sig = rpc.send_raw(bytes(tx))
    rpc.confirm(sig)
    return sig


def transfer_sol(rpc: Rpc, payer: Keypair, to: str, lamports: int) -> str:
    ix = transfer(TransferParams(from_pubkey=payer.pubkey(), to_pubkey=Pubkey.from_string(to),
                                 lamports=int(lamports)))
    return send_legacy(rpc, payer, [ix])


def sign_versioned(tx_bytes: bytes, signers: list[Keypair]) -> bytes:
    tx = VersionedTransaction.from_bytes(tx_bytes)
    return bytes(VersionedTransaction(tx.message, signers))


def sol_received(tx: dict | None, to: str) -> int:
    """Lamports that `to` gained in a parsed transaction, 0 if none/failed."""
    if not tx or (tx.get("meta") or {}).get("err"):
        return 0
    keys = [k["pubkey"] if isinstance(k, dict) else str(k)
            for k in tx["transaction"]["message"]["accountKeys"]]
    if to not in keys:
        return 0
    i = keys.index(to)
    pre, post = tx["meta"]["preBalances"][i], tx["meta"]["postBalances"][i]
    return max(0, int(post) - int(pre))
