#!/usr/bin/env bash
# Put the WALL agent on a fresh Ubuntu or Debian box. Safe to run twice.
#
#   from a copy of the project on the box (scp -r wall root@box:/root/wall):
#       sudo bash /root/wall/deploy/install.sh
#   or from GitHub:  REPO=https://github.com/<owner>/wall.git  curl -fsSL .../deploy/install.sh | sudo bash
#
# Nothing here asks for a key. The hot wallet is created in step 6 and its
# file never leaves /opt/wall.
set -euo pipefail
REPO=${REPO:-}
ROOT=/opt/wall
U=wall
SRC=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)   # the project this script sits in, if any
IP=$(curl -4 -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
HOST=$(echo "$IP" | tr . -).sslip.io      # https before the real domain exists
say(){ printf '\n\033[1m%s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo"; exit 1; }

say "1/7  packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git rsync ffmpeg ufw curl debian-keyring debian-archive-keyring apt-transport-https >/dev/null
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

say "2/7  user and directories"
id -u $U >/dev/null 2>&1 || useradd --system --home $ROOT --shell /usr/sbin/nologin $U
mkdir -p $ROOT/data/clips $ROOT/data/buf
if [ -n "$SRC" ] && [ -f "$SRC/agent/main.py" ]; then
  mkdir -p $ROOT/app
  rsync -a --delete --exclude .git --exclude __pycache__ --exclude _to_delete --exclude data --exclude 'site/sample/thumbs' "$SRC/" $ROOT/app/
elif [ -d $ROOT/app/.git ]; then git -C $ROOT/app pull --ff-only
elif [ -n "$REPO" ]; then git clone --depth 1 "$REPO" $ROOT/app
else echo "no source: run this from a copy of the project or set REPO="; exit 1; fi
# the static site, zipped the way Netlify's drop zone wants it (index.html at the root), served at /site.zip
python3 - <<PY
import shutil; shutil.make_archive("$ROOT/data/site", "zip", "$ROOT/app/site")
PY
chmod 644 $ROOT/data/site.zip
# the source tree as a bare git repo over dumb http at /wall.git, so GitHub's importer can pull it.
# never the data dir, the config or the wallet; same list as .gitignore.
SNAP=$(mktemp -d)
rsync -a --exclude .git --exclude __pycache__ --exclude .pytest_cache --exclude _to_delete --exclude data \
      --exclude 'site/sample/thumbs' --exclude 'site/sample/clips' --exclude '*.tar.gz' --exclude config.toml --exclude hot-wallet.json \
      "$ROOT/app/" "$SNAP/"
chown -R root:root "$SNAP"            # rsync kept the wall user's ownership; git refuses a repo owned by someone else
export GIT_CONFIG_PARAMETERS="'safe.directory=*'"
( cd "$SNAP" && git init -q && git add -A && git -c user.name="wallpadSOL" -c user.email="wallpadSOL@users.noreply.github.com" \
  commit -q -m "WALL: a launchpad for live streams on Solana" -m "The agent reads Kick and Twitch chats, cuts a 30 s clip the moment a room runs 3x its own last minute, and creates coins on pump.fun from one hot wallet. Fees split hourly: burn, launchers, platform. The static site reads the agent's public json." \
  && git branch -M main )
rm -rf "$ROOT/data/wall.git"
git clone -q --bare "$SNAP" "$ROOT/data/wall.git"
( cd "$ROOT/data/wall.git" && git update-server-info )
chmod -R a+rX "$ROOT/data/wall.git"
rm -rf "$SNAP"
unset GIT_CONFIG_PARAMETERS

say "3/7  python environment"
[ -d $ROOT/venv ] || python3 -m venv $ROOT/venv
$ROOT/venv/bin/pip install -q --upgrade pip
$ROOT/venv/bin/pip install -q -r $ROOT/app/requirements.txt

say "4/7  config"
[ -f $ROOT/config.toml ] || cp $ROOT/app/config.example.toml $ROOT/config.toml
sed -i "s#https://wallpad.fun#https://wallpad.run#" $ROOT/config.toml   # the domain changed after the first install
grep -q hot_float_sol $ROOT/config.toml || sed -i "s|^min_wall_hold = .*|&\nhot_float_sol = 0.15   \\# the hot wallet keeps this much, the rest is swept to cold_wallet each round|" $ROOT/config.toml
[ -f /etc/wall.env ] || { echo "WALL_LOG=INFO" > /etc/wall.env; chmod 600 /etc/wall.env; }

say "5/7  services"
cp $ROOT/app/deploy/wall-agent.service $ROOT/app/deploy/wall-fees.service $ROOT/app/deploy/wall-fees.timer /etc/systemd/system/
sed "s/__HOST__/$HOST/" $ROOT/app/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable wall-agent >/dev/null
systemctl enable --now caddy >/dev/null || true
systemctl reload caddy || systemctl restart caddy || true

say "6/7  hot wallet"
if [ ! -f $ROOT/hot-wallet.json ]; then
  cd $ROOT/app && $ROOT/venv/bin/python -m agent keygen --out $ROOT/hot-wallet.json
fi
chown -R $U:$U $ROOT
chmod 600 $ROOT/hot-wallet.json
systemctl restart wall-agent

say "7/7  firewall"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80,443/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

HOT=$(cd $ROOT/app && $ROOT/venv/bin/python -c "import json;from solders.keypair import Keypair;print(Keypair.from_bytes(bytes(json.load(open('$ROOT/hot-wallet.json')))).pubkey())" 2>/dev/null || echo "see above")
cat <<TXT

done. what runs now:
  wall-agent     reading chats, writing /opt/wall/data/state.json, http on 127.0.0.1:8791
  caddy          https://$HOST  (now)   and   https://api.wallpad.run  (once DNS points here)
  hot wallet     $HOT

check:   sudo -u wall $ROOT/venv/bin/python -m agent -c $ROOT/config.toml check
watch:   journalctl -u wall-agent -f
wall:    sudo -u wall $ROOT/venv/bin/python -m agent -c $ROOT/config.toml status

to turn launching on: fund the hot wallet printed above with ~0.2 SOL, then in
$ROOT/config.toml set [solana] enabled = true and restart: systemctl restart wall-agent
to turn the hourly fee round on: systemctl enable --now wall-fees.timer
TXT
