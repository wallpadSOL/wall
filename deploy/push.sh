#!/usr/bin/env bash
# Copy this project to the VPS and run the installer there, in one ssh session. Run on the Mac:
#   bash ~/Documents/Claude/Projects/post/wall/deploy/push.sh
# Asks for the root password once. Nothing is stored anywhere.
set -e
BOX=${BOX:-root@162.35.122.197}
SRC="$(cd "$(dirname "$0")/.." && pwd)"
echo "sending $SRC to $BOX and installing (3-5 min)..."
tar czf - -C "$(dirname "$SRC")" \
  --exclude='*/__pycache__' --exclude='wall/_to_delete' --exclude='wall/data' --exclude='wall/site/sample/thumbs' --exclude='wall/.git' \
  "$(basename "$SRC")" \
| ssh -o StrictHostKeyChecking=accept-new "$BOX" 'rm -rf /root/wall && tar xzf - -C /root && bash /root/wall/deploy/install.sh'
