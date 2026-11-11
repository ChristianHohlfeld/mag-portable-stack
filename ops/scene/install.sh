#!/usr/bin/env bash
# Scene layer of a deploy: installs the pinned Blender (ops/scene/bootstrap.sh
# fetched and verified it) under /opt/mag-scene and the worker's unit, and
# restarts the worker only when its code or unit changed. A film that is
# being rendered goes on through that restart (KillMode=process). Optional:
# without Blender the bots are told that scenes are not set up.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'root required' >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)"
owner=$(stat -c %U "$SOURCE")
home=$(getent passwd "$owner" | cut -d: -f6)
read -r VERSION SHA256 _ < "$SOURCE/ops/scene/blender.lock"
NAME="blender-$VERSION-linux-x64"
CACHED="${MAG_SCENE_CACHE:-$home/.cache/mag-stack}/blender/$NAME"
UNIT=/etc/systemd/system/mag-scene.service
if [[ ! -x /opt/mag-scene/$NAME/blender ]]; then
  if [[ ! -x $CACHED/blender || "$(cat "$CACHED/.mag-verified" 2>/dev/null)" != "$SHA256" ]]; then
    echo 'scenes: Blender is not fetched; 3D scenes stay off (run ops/scene/bootstrap.sh)' >&2
    exit 0
  fi
  # A copy of its own, root's and read-only for the worker: put in place complete.
  install -d -m 0755 /opt/mag-scene
  rm -rf -- "/opt/mag-scene/.$NAME.next"
  cp -a --no-preserve=ownership "$CACHED" "/opt/mag-scene/.$NAME.next"
  chown -R root:root "/opt/mag-scene/.$NAME.next"
  chmod -R go-w "/opt/mag-scene/.$NAME.next"
  mv "/opt/mag-scene/.$NAME.next" "/opt/mag-scene/$NAME"
fi
ln -sfn "$NAME" /opt/mag-scene/blender
# MediaPipe's face landmarker (ops/scene/face/bootstrap.sh fetched it), a
# read-only copy of root's. Without it faces are found from the head's shape.
FACE="${MAG_SCENE_CACHE:-$home/.cache/mag-stack}/face"
face_sum=$(sha256sum "$SOURCE/ops/scene/face/face.lock" | cut -d' ' -f1)
if [[ "$(cat /opt/mag-scene/face/.mag-verified 2>/dev/null)" != "$face_sum" && "$(cat "$FACE/.mag-verified" 2>/dev/null)" == "$face_sum" ]]; then
  rm -rf -- /opt/mag-scene/.face.next
  install -d -m 0755 /opt/mag-scene/.face.next
  cp -a --no-preserve=ownership "$FACE/site" "$FACE/face_landmarker.task" "$FACE/.mag-verified" /opt/mag-scene/.face.next/
  chown -R root:root /opt/mag-scene/.face.next
  chmod -R go-w,a+rX /opt/mag-scene/.face.next
  rm -rf -- /opt/mag-scene/face
  mv /opt/mag-scene/.face.next /opt/mag-scene/face
  echo 'scenes: face landmarker installed'
fi
install -d -o magbot -g magbot -m 0700 /var/lib/mag-scene /var/lib/mag-scene/jobs
changed=0
if ! cmp -s "$SOURCE/ops/scene/mag-scene.service" "$UNIT"; then
  install -m 0644 "$SOURCE/ops/scene/mag-scene.service" "$UNIT"
  systemctl daemon-reload
  changed=1
fi
systemctl enable mag-scene.service >/dev/null 2>&1 || :
# The worker runs from the release. scene.py and rig.py are read anew by
# every Blender run; only a changed worker needs the restart.
rev=$(cat "$SOURCE/ops/scene/worker.py" "$UNIT" | sha256sum | cut -d' ' -f1)
if (( changed )) || [[ $(cat /run/mag-scene.rev 2>/dev/null) != "$rev" ]] || ! systemctl is-active --quiet mag-scene.service; then
  systemctl restart mag-scene.service
  printf '%s\n' "$rev" > /run/mag-scene.rev
fi
for _ in $(seq 1 40); do
  if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8795/api/scene/health 2>/dev/null | grep -q '"ok": true'; then
    echo "scenes: mag-scene is ready (Blender $VERSION, CPU)"
    exit 0
  fi
  sleep 0.5
done
echo 'scenes: mag-scene does not answer (journalctl -u mag-scene)' >&2
exit 1
