ROOTFS=/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs
BOTDIR="$ROOTFS/root/botmirror"
pkill -9 -f "botmirror/venv/bin/python /root/botmirror/bot.py"
sleep 2
: > /tmp/mirrorbot.log
nohup proot -S "$ROOTFS" -b "$BOTDIR:/root/botmirror" -b /dev -b /proc -b /sys -0 -w /root/botmirror \
  env VIRTUAL_ENV=/root/botmirror/venv HOME=/root PYTHONUNBUFFERED=1 \
  /root/botmirror/venv/bin/python /root/botmirror/bot.py > /tmp/mirrorbot.log 2>&1 &
disown
echo "relaunched, restarting boot (slow under nested proot)..."