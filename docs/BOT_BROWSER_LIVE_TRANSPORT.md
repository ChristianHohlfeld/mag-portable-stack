# Bot browser live control — transport contract (2026-10-14)

## Scope and state

The S23 and the MAG agent browsers are **different capture sources**.
The S23's native sender consumes hardware-encoded Android MediaCodec H.264 via
scrcpy, fragments frames into authenticated UDP datagrams and bridges them via
Pion WebRTC/SRTP/UDP, with separate reliable control and lossy motion channels.

The bot browsers are headless Chromium instances controlled by native C++ CDP.
Their current `/screenshot.png` returns individual PNGs over authenticated
VM HTTP. It does not generate H.264 and is not a live WebRTC stream.

This branch implements **direct viewport control** (mouse down/move/up, click,
wheel, focused keyboard input) through the current MAG room → token-protected
VM browser → CDP route. PNG preview remains; a faster requested refresh interval
is not a video transport. **Do not describe or deploy this as 1:1 WebRTC parity.**

## Target: S23-equivalent live user experience

- Each VM has its own video sender and persistent browser, never a shared VNC
  desktop, and never changes S23's UDP ports, services or Tailscale route.
- Chromium CDP `Page.startScreencast` or another proven headless-tab capture
  source provides frames. A bounded encoder produces H.264 (or negotiated
  browser-supported equivalent) using measured host/guest CPU budgets.
- Native UDP video packets have a per-session secret, agent identity,
  sequence, timestamp, fragmentation, size cap, expiry and drop-on-overflow.
  Host accepts packets only from the matching VM/IP; no cross-agent frames.
- One host media relay (independent of the MAG room inference slot) presents
  WebRTC video over SRTP/UDP to the MAG browser. Browser input uses a reliable
  ordered DataChannel for clicks/keys and an unreliable unordered DataChannel
  for cursor motion, matching the S23 control semantics.
- The media relay authenticates and authorizes session setup via the existing
  MAG room and Tailscale-only frontend. It never exposes the CDP debugging
  port, guest HTTP token or media session token to the remote browser.
- ICE/UDP ports must be separate from S23's 18768 and 18770–18830, permit only
  approved tailnet access, and be tested with the existing nftables rules.
- Open a new agent or switch the selected agent without retaining old session
  tokens, pointers or streams. Disconnecting a viewer stops media capture after
  a short grace period; **bot automation stays alive** without viewers.
- Do not change the immutable golden image in place. If encoder packages are
  required inside guests, make a newly versioned image, prove compatibility
  in a disposable VM, then opt in. Existing bot disks, memories, Git accounts,
  S23 control and warm llama.cpp are preserved.

## Release gate (not yet met)

1. Verify actual Chromium screencast output, encoder availability and CPU
   consumption on a disposable VM while two bots use tools.
2. Codec, packet fragmentation, UDP loss/reorder and secret/isolation tests.
3. Negotiated WebRTC video *and* both DataChannels through Tailscale, with
   direct user mouse, drag, wheel, keyboard and clipboard verified visually.
4. Reconnect, 2–4 viewers, switching agents, idle/no-viewer bot autonomy,
   latency/FPS/bitrate/host-load measurements and S23 regression verification.
5. Main MAG native build/tests green, no secret exposure; deploy only with
   versioned release, health checks and rollback. Never stop the model.
