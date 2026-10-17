# S23 control

One control API for the Samsung S23, for a person on the machine and for a room bot. It runs beside `s23pub` (`:8720`) and does not replace it. `cloudflared` is not part of this path. Nothing here is started by `ops/deploy.sh`. Switchover waits for Chris.

The H.264 UDP/WebRTC stack stays in `ChristianHohlfeld/mag-s23-remote` (`main` `4ebcbd8`, `s23-udp-av` `54377ce`). That repository's `deploy-mag.sh` replaces the old HTTPS route, so it is not copied into this deploy. This package is the control layer that can run at the same time.

## Bind and auth

`s23ctl` listens on `127.0.0.1:18721` unless `MAG_S23_BIND` and `MAG_S23_PORT` say otherwise.

- `0.0.0.0`, `::`, and port `8720` are refused.
- A Tailscale address in `100.64.0.0/10` or `fd7a:115c:a1e0::/48` is accepted only when `MAG_S23_BIND_TAILNET=1`.
- Every request needs `Authorization: Bearer` from `MAG_S23_TOKEN` or from `MAG_S23_TOKEN_FILE` (default `/run/mag-s23/token`, mode `0600`). The token is never written to the log.
- `MAG_S23_SERIAL` is required. The daemon does not scan for a phone by itself.

## Session

One session. `POST /v1/session` returns `{"session","idle_ms"}`. Input, screenshot, latency, and video need `X-S23-Session`. A second acquire gets `409` until `DELETE /v1/session` or the idle timer (`MAG_S23_SESSION_IDLE_MS`, default 30s). An expired or missing session is `440`.

## API

| Method | Path | Session | Effect |
| --- | --- | --- | --- |
| GET | `/health` | no | Process is up. Does not call adb. |
| GET | `/v1/status` | no | `adb get-state` and `wm size`. |
| POST | `/v1/session` | no | Take the lock. |
| DELETE | `/v1/session` | yes | Release the lock. |
| POST | `/v1/input/tap` | yes | `{"x","y"}` device pixels. |
| POST | `/v1/input/swipe` | yes | `{"x1","y1","x2","y2","ms"}`. |
| POST | `/v1/input/text` | yes | Visible ASCII, spaces become `%s`. |
| POST | `/v1/input/key` | yes | `{"key":"KEYCODE_HOME"}` or `{"code":3}`. |
| GET | `/v1/screenshot` | yes | PNG body from `screencap -p`. |
| GET | `/v1/latency` | yes | `adb shell echo s23`, time in `X-S23-Latency-Ms`. |
| GET | `/v1/video` | yes | WebSocket. One text JSON frame, then the PNG bytes. |

adb is `exec`'d with an argument vector. A disconnected device (`offline`, `no devices`, `not found`, `closed`) gets one `adb reconnect` and one retry. A timeout (`MAG_S23_CMD_TIMEOUT_MS`, default 4s) returns `504` and does not retry in the background. There is no watchdog thread.

## Latency

Every JSON and screenshot response carries `X-S23-Latency-Ms`, measured around that adb call. `/v1/latency` is the control-path sample: time from the request to `adb shell echo s23` returning. A video frame's text message carries the same number for that `screencap`. The JPEG interval is `MAG_S23_FRAME_MS` (default 200). That number is the screencap path, not the H.264 UDP path still living in `mag-s23-remote`.

On mag, after the daemon is started by hand:

```
curl -sS -D - -o /dev/null -H "Authorization: Bearer $(cat /run/mag-s23/token)" \
  -H "X-S23-Session: $SID" http://127.0.0.1:18721/v1/latency
```

Read `X-S23-Latency-Ms`. Do not point this curl at port 8720.

## Room tools

`s23_status`, `s23_tap`, `s23_swipe`, `s23_text`, `s23_key`, and `s23_screenshot` call `127.0.0.1` only. The token stays in the room process. If the daemon is not configured or not running, the tool returns that fact and does not touch `s23pub`. A screenshot tool result is the byte count, not the image.

## Not started by deploy

`devices/s23/mag-s23ctl.service` has no install target. `ops/deploy.sh` does not copy or start `s23ctl`. Build it with the rest of the CMake targets. Copy the binary and the unit, write the token file and `MAG_S23_SERIAL` into `/etc/mag-s23/s23ctl.env`, and start the unit only when Chris says the parallel check can begin.
