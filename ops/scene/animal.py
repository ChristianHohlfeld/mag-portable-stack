"""MAG scenes: how an animal on four legs moves.

There are no recorded motions for it here that may be passed on, so its
motion is worked out, the way an animal's body does it: the feet take turns
in the order of the gait (walk, trot, gallop, bound, stalking), each one
stands where it was put down until it is lifted and swung to where the body
will be over it, and the body rides on the legs that carry it: it vaults over
them at a walk and gives in them at a trot, the back flexes and stretches at
a gallop, hips and shoulders turn with the legs, the head stays level and
looks where it goes, and the tail follows late. Sitting down, lying down, a
pounce and the like are poses the body eases into, with the same feet on the
same ground.

Only numpy and the standard library, like motion.py, whose turns it uses.
Everything is seen from the animal: x is where it looks, y its left, z up.
"""
import math

import numpy as np

from motion import Swings, fade, qarc, qaxis, qinv, qmul, qpart, qrot, qz, reach, swell, turn2, unit, wrap

HL, HR, FL, FR = range(4)                             # the legs: hind left, hind right, fore left, fore right
X, Y, Z = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])
SAME = np.array([1.0, 0.0, 0.0, 0.0])
# A gait: when in a stride each leg is put down (hind left, hind right, fore
# left, fore right), the part of a stride a foot stands (slow .. fast), how
# far a foot stands behind and in front of its hip while the body passes
# (leg lengths), how fast it is (leg lengths a second), how much the body
# gives in the legs instead of vaulting over them, how high a foot is lifted
# (leg lengths) and how low the body is carried.
GAITS = {
    "walk": {"beat": (0.0, 0.5, 0.25, 0.75), "stand": 0.64, "reach": 1.0, "pace": 3.0, "spring": 0.0, "lift": 0.2, "low": 1.0},
    "stalk": {"beat": (0.0, 0.5, 0.25, 0.75), "stand": 0.74, "reach": 0.8, "pace": 1.2, "spring": 0.0, "lift": 0.24, "low": 0.8},
    "trot": {"beat": (0.0, 0.5, 0.5, 0.0), "stand": 0.46, "reach": 1.15, "pace": 6.4, "spring": 1.0, "lift": 0.24, "low": 1.0},
    "gallop": {"beat": (0.08, 0.0, 0.5, 0.58), "stand": 0.3, "reach": 1.3, "pace": 11.5, "spring": 1.0, "lift": 0.3, "low": 0.93},
    "bound": {"beat": (0.0, 0.0, 0.42, 0.42), "stand": 0.36, "reach": 1.15, "pace": 5.4, "spring": 1.0, "lift": 0.32, "low": 0.95},
}
# What a body can ease into: how high hips and shoulders are carried (of
# standing), how far the fore feet are put forward and the hind feet are
# tucked under the belly (leg lengths; the feet step there), how the head and
# the tail are held (radians up).
POSES = {
    "stand": {"rear": 1.0, "fore": 1.0, "ahead": 0.0, "tuck": 0.0, "nose": 0.0, "tail": 0.0},
    "sit": {"rear": 0.42, "fore": 1.03, "ahead": -0.1, "tuck": 0.55, "nose": 0.3, "tail": 0.5},
    "lie": {"rear": 0.34, "fore": 0.36, "ahead": 0.85, "tuck": 0.5, "nose": 0.05, "tail": 0.25},
    "sleep": {"rear": 0.33, "fore": 0.31, "ahead": 0.85, "tuck": 0.5, "nose": -0.55, "tail": 0.25},
    "crouch": {"rear": 0.72, "fore": 0.66, "ahead": 0.1, "tuck": 0.1, "nose": -0.1, "tail": -0.1},
    "bow": {"rear": 1.04, "fore": 0.42, "ahead": 0.75, "tuck": 0.0, "nose": 0.45, "tail": 0.9},
    "beg": {"rear": 0.46, "fore": 2.2, "ahead": 0.0, "tuck": 0.5, "nose": -0.5, "tail": 0.45},
}


class Spring:
    """A value that follows what is asked of it the way a body does: it sets
    off gently, and with `swing` it goes a little too far and comes back."""

    def __init__(self, value=0.0, rate=8.0, swing=0.0):
        self.x, self.v, self.rate, self.swing = float(value), 0.0, rate, swing

    def step(self, target, dt):
        k = self.rate * self.rate
        c = 2.0 * self.rate * (1.0 - self.swing)
        for _ in range(4):                               # small steps: it never runs away
            h = dt / 4
            self.v += (k * (target - self.x) - c * self.v) * h
            self.x += self.v * h
        return self.x


class Build:
    """The skeleton rig.py found for an animal, seen from the animal."""

    def __init__(self, bones, front, scale=1.0):
        self.bones, self.front, self.scale = bones, front, scale
        self.facing = math.atan2(front[1], front[0])
        own = qz(-self.facing)
        self.names = [b["name"] for b in bones]
        self.parent = [b["parent"] for b in bones]
        self.head = [qrot(own, np.asarray(b["head"], dtype=float)) * scale for b in bones]
        self.tail = [qrot(own, np.asarray(b["tail"], dtype=float)) * scale for b in bones]
        self.dir = [unit(t - h) for h, t in zip(self.head, self.tail)]
        self.length = [float(np.linalg.norm(t - h)) for h, t in zip(self.head, self.tail)]
        role = {}
        for i, b in enumerate(bones):
            role.setdefault(b["role"], []).append(i)
        self.hips, self.chest, self.neck, self.skull = role["hips"][0], role["chest"][0], role["neck"][0], role["head"][0]
        self.tails = role.get("tail", [])
        legs = {}
        for i in role["leg"]:
            shin = next(k for k, b in enumerate(bones) if b["parent"] == i and b["role"] == "shin")
            legs.setdefault(bool(bones[i]["fore"]), []).append((i, shin))
        if sorted(len(v) for v in legs.values()) != [2, 2]:
            raise ValueError("an animal here walks on two hind legs and two fore legs")
        self.legs = []
        for fore in (False, True):                        # hind left, hind right, fore left, fore right
            self.legs += sorted(legs[fore], key=lambda leg: -self.head[leg[0]][1])
        self.hip = [self.head[upper] for upper, _ in self.legs]
        self.reach = [self.length[upper] + self.length[shin] for upper, shin in self.legs]
        # As the model stands, a leg has the length and the bend its shape was made with:
        # carried at that height it is itself, turned; only what goes beyond bends its middle.
        self.natural = [float(np.linalg.norm(self.tail[shin] - self.head[upper])) for upper, shin in self.legs]
        self.leg = float(np.mean(self.natural))
        self.rear, self.fore = (self.hip[HL] + self.hip[HR]) / 2, (self.hip[FL] + self.hip[FR]) / 2   # between the hips, between the shoulders
        self.trunk = float(np.linalg.norm((self.fore - self.rear)[:2]))
        self.mid = self.tail[self.hips]                   # where the back bends: the pelvis ends, the chest begins
        self.height = float(max(max(t[2], h[2]) for h, t in zip(self.head, self.tail)))
        self.long = float(max(t[0] for t in self.tail) - min(t[0] for t in self.tail))
        # How high hips and shoulders stand over the ground: the legs as long as they were made.
        self.stand = [0.99 * min(self.natural[HL], self.natural[HR]), 0.99 * min(self.natural[FL], self.natural[FR])]

    def sized(self, scale):
        return Build(self.bones, self.front, scale)


class Paw:
    def __init__(self, at):
        self.at = np.array([at[0], at[1], 0.0])           # where it is
        self.down = True                                  # it stands
        self.since = 0.0                                  # seconds it has been standing or in the air
        self.start, self.goal, self.span = np.zeros(2), np.zeros(2), 0.2   # a swing: from where to where (from its place under the body, as the animal sees it), how long
        self.high, self.from_high = 0.0, 0.0              # how high the swing lifts it, and how high it began
        self.hangs, self.start_hang = None, None          # its end of the body is up out of its reach: seconds it has been hanging there, and from where


class Animal:
    """One animal through a scene, frame by frame. Like a Performer of
    motion.py it always does something: what was asked for, or it stands,
    breathes, looks about and shifts its weight."""
    SOCKET = 0.28                                     # how far a leg's joint slides under the skin (leg lengths)
    GATHER = 0.18                                     # seconds it gathers itself before a leap
    WHIP = 32.0                                       # the fastest a leg's bone turns (radians a second)

    def __init__(self, build, fps, xy, heading, seed=0):
        self.build, self.fps, self.dt, self.seed = build, fps, 1.0 / fps, seed
        self.heading = float(heading)
        self.xy = np.array(xy, dtype=float)               # the middle of the body over the ground
        self.speed, self.turning = 0.0, 0.0               # leg lengths a second forwards, radians a second
        self.gait, self.clock = GAITS["walk"], 0.0        # the stride: which gait, how far through a stride
        self.steps, self.wanted = False, 0.0              # the legs are stepping; the pace that is asked for
        self.aim = dict(POSES["stand"])                   # the pose it is easing into
        self.later = []                                   # ... and what of it comes a little later: (from which frame, what, how much)
        self.pose = {key: Spring(value, 5.0, 0.2) for key, value in POSES["stand"].items()}
        self.paws = [Paw(self._home(k)) for k in range(4)]
        self.carried = [None] * 4                         # a foot off the ground and held: where, from its hip
        self.low = Spring(1.0, 6.0)                       # how low the gait carries the body
        self.flex, self.arch = Spring(0.0, 14.0, 0.2), 0.0
        self.pitch, self.roll = Spring(0.0, 9.0, 0.3), Spring(0.0, 9.0, 0.3)   # what is asked on top: nose up, leaning left
        self.tilt = {"pitch": 0.0, "roll": 0.0}
        self.nose = {"aside": Spring(0.0, 5.0, 0.15), "up": Spring(0.0, 5.0, 0.15)}   # where the head looks, from straight ahead
        self.gaze = {"aside": 0.0, "up": 0.0}
        self.wag, self.wagging = Spring(0.0, 6.0), 0.0    # how much the tail wags
        self.tail = [[Spring(0.0, 16.0, 0.55), Spring(0.0, 16.0, 0.55)] for _ in build.tails]   # each bone: aside, up
        self.rise = 0.0                                   # off the ground in a leap: how high the body is carried over standing
        self.sink = [0.0, 0.0]                            # how far hips and shoulders have come down to a foot that stands far off
        self.slide = [0.0] * 4                            # how far each leg's joint has gone forwards under the skin, towards its foot
        self.over = [build.stand[0]] * 2 + [build.stand[1]] * 2   # how high each leg's joint is over the ground
        self.limbs = {}                                   # where the legs' bones pointed a frame ago
        self.shake = 0.0                                  # it shakes itself: how hard
        self.before = None                                # how the pelvis was turned a frame ago (the tail follows late)
        self.looks, self.heads, self.sways = [], [], []
        self.t = seed * 2.3                               # its own time: two animals do not breathe in step
        self.watch = False
        self.frames = []

    # ---- where things are ---------------------------------------------------------
    def _home(self, k):
        """Where a foot belongs on the ground: under its hip, or where the pose the animal is easing into has it."""
        b = self.build
        off = b.hip[k][:2] - (b.rear[:2] + b.fore[:2]) / 2
        return self.xy + turn2(self.heading, [off[0] + self.aim["ahead" if k >= 2 else "tuck"] * b.leg, off[1]])

    def _phase(self, k):
        """How far a leg is through its own stride: 0 when it is put down."""
        return (self.clock - self.gait["beat"][k]) % 1.0

    def _stand(self):
        """The part of a stride a foot stands at this pace: the slower, the longer."""
        g = self.gait
        slow = 1.0 - min(1.0, abs(self.speed) / g["pace"])
        return g["stand"] + slow * (0.12 if g["spring"] > 0.5 else 0.5 * (0.8 - g["stand"]))

    def _rate(self):
        """Strides a second: at this pace (and turning this fast) the body passes a foot's reach while it stands."""
        b, g = self.build, self.gait
        pace = abs(self.speed) + abs(self.turning) * 0.5 * b.trunk / b.leg
        return min(3.4, max(1.3, pace * self._stand() / g["reach"]))

    def _drift(self, k):
        """How fast a foot's place under the body moves over the ground, as the animal sees it."""
        b = self.build
        off = b.hip[k][:2] - (b.rear[:2] + b.fore[:2]) / 2
        return np.array([self.speed * b.leg - self.turning * off[1], self.turning * off[0]])

    # ---- one frame ------------------------------------------------------------------
    def tick(self, drive=None):
        """One frame further. `drive`: asked for the pace wanted (leg lengths a second) and the turn (radians a second)."""
        b, dt, f = self.build, self.dt, len(self.frames)
        self.t += dt
        for due in [x for x in self.later if x[0] <= f]:  # the second half of easing into a pose
            self.aim[due[1]] = due[2]
            self.later.remove(due)
        pace, turn = drive(self) if drive else (0.0, 0.0)
        self.wanted = abs(pace)
        # A body does not change its pace at once.
        most = (9.0 if self.gait["spring"] > 0.5 else 5.0) * dt
        self.speed += min(most, max(-most * 1.6, pace - self.speed))
        self.turning += (turn - self.turning) * min(1.0, 10.0 * dt)
        self.xy = self.xy + turn2(self.heading + 0.5 * self.turning * dt, [self.speed * b.leg * dt, 0.0])
        self.heading += self.turning * dt
        self._step(dt)
        frame = self._body(f)
        self.frames.append(frame)

    def _step(self, dt):
        """The feet: which stand, which swing, where to."""
        b, g = self.build, self.gait
        moving = abs(self.speed) > 0.15 or abs(self.turning) > 0.25
        away = [float(np.linalg.norm(self.paws[k].at[:2] - self._home(k))) for k in range(4)]
        if not self.steps and (moving or max(away) > 0.3 * b.leg) and not any(c is not None for c in self.carried):
            self.steps = True                             # the first foot of the gait goes first, or the one furthest from its place
            first = int(np.argmin(g["beat"])) if abs(self.speed) > 0.15 or self.wanted > 0.15 else int(np.argmax(away))
            self.clock = (self._stand() + g["beat"][first]) % 1.0
        if self.steps and not moving and all(p.down for p in self.paws) and max(away) < 0.12 * b.leg:
            self.steps = False                            # everybody stands where it belongs
        stand, rate = self._stand(), self._rate()
        if self.steps:
            self.clock = (self.clock + rate * dt) % 1.0
        swing = (1.0 - stand) / rate
        for k, paw in enumerate(self.paws):
            paw.since += dt
            # A foot whose end of the body is up out of its reach (begging) hangs from it, and is put down when it comes back.
            if paw.hangs is not None:
                paw.hangs += dt
                if self.over[k] < 0.99 * b.reach[k]:
                    held = self.carried[k]
                    paw.hangs, self.carried[k] = None, None
                    paw.down, paw.since, paw.span, paw.high = False, 0.0, 0.22, 0.0
                    paw.start, paw.from_high = np.array([held[0], held[1]]), max(0.0, self.over[k] + held[2])
                else:
                    u = fade(paw.hangs / 0.35)
                    self.carried[k] = paw.start_hang + (np.array([0.3 * b.reach[k], 0.0, -0.78 * b.reach[k]]) - paw.start_hang) * u
                    continue
            if self.carried[k] is not None:
                continue
            phase = self._phase(k)
            if paw.down:
                # It is lifted when its turn comes, if there is anywhere to go, or when the body has left it behind.
                turn = self.steps and stand <= phase < 1.0 - 0.25 * (1.0 - stand) and (moving or away[k] > 0.1 * b.leg)
                if (turn or away[k] > 1.15 * b.reach[k]) and paw.since > 0.06:
                    paw.down, paw.since, paw.from_high = False, 0.0, 0.0
                    paw.start = turn2(-self.heading, paw.at[:2] - self._home(k))      # from its place under the body, as the animal sees it
                    paw.span = max(0.08, (1.0 - phase) / rate if turn else swing)       # down again when its turn to stand comes
                    paw.high = g["lift"] * b.leg * (0.55 + 0.45 * min(1.0, abs(self.speed) / g["pace"])) if moving else 0.12 * b.leg
            if not paw.down:
                # It swings from behind its place under the body to as far in front of it as
                # the body will pass over it while it stands: the body is over it in the middle of that.
                u = min(1.0, paw.since / paw.span)
                if u < 0.85:                              # (the hind legs push from behind their place, the fore legs reach in front of it)
                    paw.goal = (0.5 * stand / rate * self._drift(k) + [(0.08 if k >= 2 else -0.08) * b.leg * min(1.0, abs(self.speed)), 0.0]) if moving else np.zeros(2)
                at = self._home(k) + turn2(self.heading, paw.start + (paw.goal - paw.start) * fade(u))
                paw.at = np.array([at[0], at[1], paw.high * math.sin(math.pi * min(1.0, u ** 0.85)) ** 1.2 + paw.from_high * (1.0 - fade(u))])
                if u >= 1.0:
                    paw.down, paw.since, paw.at[2] = True, 0.0, 0.0

    def _support(self, a, c):
        """How the two legs of one end carry the body: how far through its
        turn the leg that carries it is (0 just put down .. 1 in the middle
        .. 0 about to be lifted), and how much that end leans to the left."""
        stand = self._stand()
        carries, lean = 0.0, 0.0
        for k, side in ((a, 1.0), (c, -1.0)):
            paw = self.paws[k]
            if self.carried[k] is not None:
                continue
            if paw.down and self.steps:
                carries = max(carries, math.sin(math.pi * min(1.0, self._phase(k) / stand)))
            elif not paw.down:
                lean -= side * math.sin(math.pi * min(1.0, paw.since / paw.span))   # the hip of the leg in the air sinks
        return carries, lean

    def _body(self, f):
        """Hips, back, legs, neck, head and tail of this frame."""
        b, g, dt = self.build, self.gait, self.dt
        go = min(1.0, abs(self.speed) / max(1e-6, g["pace"])) if self.steps else 0.0
        pose = {key: spring.step(self.aim[key], dt) for key, spring in self.pose.items()}
        low = self.low.step(g["low"] if go > 0.05 else 1.0, dt)
        # how high hips and shoulders are carried
        ends, lean = [], []
        for a, c, key, stand in ((HL, HR, "rear", b.stand[0]), (FL, FR, "fore", b.stand[1])):
            carries, side = self._support(a, c)
            spring = g["spring"]                          # at a walk the body vaults over the leg that carries it, in a springing gait it gives
            wave = go * b.leg * ((1.0 - spring) * 0.035 * (carries - 0.6) - spring * (0.05 + 0.04 * go) * (carries - 0.5))
            ends.append(stand * pose[key] * low + wave + self.rise)
            lean.append(side)
        breath = 0.006 * b.leg * math.sin(2 * math.pi * 0.35 * self.t)
        ends[1] += breath
        ends[0], ends[1] = ends[0] - self.sink[0], ends[1] - self.sink[1]   # (a foot that stands must be within reach)
        # the back: flexed when the hind legs reach under the body at a gallop, stretched when they push off
        together = abs(math.cos(math.pi * (g["beat"][HL] - g["beat"][HR]))) * abs(math.cos(math.pi * (g["beat"][FL] - g["beat"][FR])))
        hind = (self.clock - (g["beat"][HL] + g["beat"][HR]) / 2) % 1.0
        flex = self.flex.step(go * together * math.radians(15.0) * math.cos(2 * math.pi * hind) + self.arch, dt)
        alternate = 1.0 - together
        swing_b = -go * alternate * math.radians(5.0) * math.cos(2 * math.pi * self._phase(HL))    # the hip of the leg that reaches forward goes forward
        swing_c = -go * alternate * math.radians(3.5) * math.cos(2 * math.pi * self._phase(FL))
        ask_pitch, ask_roll = self.pitch.step(self.tilt["pitch"], dt), self.roll.step(self.tilt["roll"], dt)
        ends[0] -= 0.5 * b.trunk * math.sin(ask_pitch)    # nose up: about the middle of the body
        ends[1] += 0.5 * b.trunk * math.sin(ask_pitch)
        shiver = self.shake * math.radians(13.0) * math.sin(2 * math.pi * 5.0 * self.t)
        for start, n, amount in self.sways:               # it sways
            if start <= f < start + n:
                u = (f - start) / n
                ask_roll += amount * math.sin(u * math.pi * 6) * (1 - u) * min(1.0, 8 * u)
        # The pelvis is turned so that the shoulders come to their height (the second try is close enough).
        nose_up = math.asin(max(-0.95, min(0.95, (ends[1] - ends[0] - (b.fore[2] - b.rear[2])) / b.trunk)))
        for attempt in range(2):
            pelvis = qmul(qz(swing_b), qmul(qaxis(Y, -(nose_up + 0.5 * flex)), qaxis(X, ask_roll + math.radians(3.0) * go * lean[0] + shiver)))
            chest = qmul(qz(swing_c), qmul(qaxis(Y, -(nose_up - 0.5 * flex)), qaxis(X, ask_roll + math.radians(3.0) * go * lean[1] - 0.6 * shiver)))
            rear = qrot(pelvis, b.rear - b.head[b.hips])                               # from where the pelvis begins
            fore = qrot(pelvis, b.mid - b.head[b.hips]) + qrot(chest, b.fore - b.mid)
            if attempt == 0:
                nose_up += math.asin(max(-0.95, min(0.95, ((ends[1] - ends[0]) - (fore[2] - rear[2])) / b.trunk)))
        turn = qz(self.heading)
        # the body's middle is between hips and shoulders
        span = float(np.linalg.norm((fore - rear)[:2]))
        base = np.array([self.xy[0], self.xy[1], ends[0]]) - qrot(turn, np.array([0.5 * span, 0.0, 0.0])) - qrot(turn, rear)
        origin = base - qrot(turn, b.head[b.hips])
        whole = {b.hips: pelvis, b.chest: chest}
        at = {b.hips: base}                                # where every bone begins (the world)
        at[b.chest] = base + qrot(turn, qrot(pelvis, b.mid - b.head[b.hips]))
        limbs, hips = {}, []
        back = qz(-self.heading)
        for k, (upper, shin) in enumerate(b.legs):
            parent = b.hips if k < 2 else b.chest
            hip = at[parent] + qrot(turn, qrot(whole[parent], b.hip[k] - b.head[parent]))
            paw = self.paws[k]
            if self.carried[k] is None and paw.hangs is None and hip[2] > 1.03 * b.reach[k] and abs(self.speed) < 0.15:
                paw.hangs, paw.start_hang = 0.0, qrot(back, paw.at - hip)    # its end of the body goes up out of its reach: from now on it hangs
                self.carried[k] = paw.start_hang.copy()
            if self.carried[k] is not None:
                goal, self.slide[k] = hip + qrot(turn, self.carried[k]), self.slide[k] * 0.7
            else:
                goal = self.paws[k].at.copy()
                # Shoulder and hip go with the leg under the skin: towards the foot, almost half of the way.
                along = float(qrot(back, goal - hip)[0])
                self.slide[k] = min(self.SOCKET * b.leg, max(-self.SOCKET * b.leg, 0.45 * along))
            hip = hip + qrot(turn, qrot(whole[parent], X * self.slide[k]))
            hips.append(hip)
            self.over[k] = float(hip[2])
            goal[2] = max(goal[2], 0.0)
            # A hind leg bends backwards in its middle, a fore leg forwards; one that is stretched
            # out along the ground, where that says nothing, bends upwards.
            way = unit(qrot(back, goal - hip))
            flat = way[0] * way[0]
            bend = qrot(turn, unit(np.array([(1.0 if k >= 2 else -1.0) * (1.0 - flat), 0.0, flat])))
            thigh, shank = reach(hip, goal, hip + bend * b.length[upper], b.length[upper], b.length[shin], bend, soft=0.97)
            # No leg whips round faster than a leg can, at its joint to the body or in its middle:
            # a folded one that would is a frame late instead.
            # (Not where that would put its foot into the ground: coming down from a leap a leg is as fast as it has to be.)
            wanted, late, carried = (qrot(back, thigh), qrot(back, shank)), [], None
            for bone, way in zip((upper, shin), wanted):
                was = self.limbs.get(bone)
                if was is not None:
                    if carried is not None:
                        was = qrot(carried, was)                       # (the lower bone goes along with the upper one first)
                    turned = math.acos(max(-1.0, min(1.0, float(was @ way))))
                    if turned > self.WHIP * dt:
                        way = qrot(qpart(qarc(was, way), self.WHIP * dt / turned), was)
                    carried = qarc(self.limbs[bone], way) if bone == upper else None
                late.append(way)
            if hip[2] + late[0][2] * b.length[upper] + late[1][2] * b.length[shin] < -0.01 * b.leg:
                late = wanted
            limbs[upper], limbs[shin] = self.limbs[upper], self.limbs[shin] = late[0], late[1]
        for end, legs in ((0, (HL, HR)), (1, (FL, FR))):
            need = 0.0
            for k in legs:
                if self.paws[k].down and self.carried[k] is None:
                    flat = float(np.hypot(*(self.paws[k].at[:2] - hips[k][:2])))
                    need = max(need, hips[k][2] + self.sink[end] - math.sqrt(max(0.0, (0.985 * b.reach[k]) ** 2 - flat * flat)))
            need = min(need, 0.3 * b.leg)
            self.sink[end] += (need - self.sink[end]) * (0.6 if need > self.sink[end] else 0.15)
        frame = {"origin": origin, "heading": self.heading, "build": b, "root": np.array([self.xy[0], self.xy[1], ends[0]]), "whole": whole, "limbs": limbs,
                 "slide": list(self.slide),
                 "nose": (self.nose["aside"].step(self.gaze["aside"], dt), self.nose["up"].step(self.gaze["up"] + pose["nose"], dt)),
                 "go": go, "bob": math.radians(2.5) * go * (1.0 - g["spring"]) * math.sin(4 * math.pi * self._phase(FL)),
                 "shiver": shiver, "tail": self._tail(pelvis, pose["tail"], go, dt), "t": self.t}
        if self.watch:
            frame["paws"] = [(p.at.copy(), p.down, self.carried[k] is not None) for k, p in enumerate(self.paws)]
            frame["stands"] = [p.at[:2].copy() if p.down and self.carried[k] is None else None for k, p in enumerate(self.paws)]
            frame["hips"] = hips
            frame["ends"] = list(ends)
        return frame

    def _tail(self, pelvis, lift, go, dt):
        """The tail: every bone aside and up from the one before. It follows the hips late, streams out at speed, and wags."""
        ahead = qrot(pelvis, X)
        aside, up = math.atan2(ahead[1], ahead[0]), math.asin(max(-1.0, min(1.0, float(ahead[2]))))
        was = self.before if self.before is not None else (aside, up)
        self.before = (aside, up)
        swung, risen = (aside - was[0]) / dt, (up - was[1]) / dt
        wag = self.wag.step(self.wagging, dt)
        out = []
        for i, (a, u) in enumerate(self.tail):
            beat = wag * math.radians(34.0 + 14.0 * i) * math.sin(2 * math.pi * 3.6 * self.t - 0.9 * i)
            idle = math.radians(5.0) * math.sin(2 * math.pi * 0.23 * self.t + 1.3 * i + self.seed) * (1.0 - go)
            # what the hips do the tail does later: it is thrown the other way first
            out.append((a.step(beat + idle - 0.05 * (1 + i) * swung + self.turning * 0.12 * (1 + i), dt),
                        u.step(lift * (0.7 if i == 0 else 0.5) + go * math.radians(10.0 + 8.0 * self.gait["spring"]) * (1 if i == 0 else 0.6) + up * (0.6 if i == 0 else 0.3)
                               - 0.04 * (1 + i) * risen, dt)))
        return out

    # ---- what a scene asks of it -----------------------------------------------------
    def wait(self, frame):
        while len(self.frames) < frame:
            self.tick()
        return len(self.frames)

    def go(self, name, goal, stop=0.0, seconds=None, on=False, limit=40.0):
        """Goes somewhere in a gait. `goal`: asked every frame for the place,
        `stop`: how far from it the animal stops, `on`: it does not slow
        down there. Returns the frame it has arrived at."""
        b, dt = self.build, self.dt
        self.up()
        self.gait = GAITS[name]
        pace = self.gait["pace"]
        if seconds:
            far = max(0.0, float(np.linalg.norm(np.asarray(goal(len(self.frames)), dtype=float) - self.xy)) - stop)
            pace = min(1.5 * pace, max(0.45 * pace, far / b.leg / max(0.3, seconds - 0.4)))
        for _ in range(int(limit * self.fps)):
            way = np.asarray(goal(len(self.frames)), dtype=float) - self.xy
            ahead = float(np.linalg.norm(way)) - stop
            # so slow that it can stop where it is to stop
            brake = self.speed * self.speed * b.leg / (2 * 5.0 * 1.6 * (1.8 if self.gait["spring"] > 0.5 else 1.0))
            if ahead <= (0.0 if on else max(0.02 * b.leg, brake)):
                break
            off = wrap(math.atan2(way[1], way[0]) - self.heading)
            want = pace * max(0.25, math.cos(min(abs(off), 1.3)))
            self.tick(lambda _, want=want, off=off: (want, min(3.2, max(-3.2, off * 4.0))))
        if not on:
            for _ in range(int(2.5 * self.fps)):          # it comes to a stand, every foot under its place
                self.tick()
                if not self.steps and abs(self.speed) < 0.05:
                    break
        return len(self.frames)

    def face(self, angle, seconds=None):
        """Turns on the spot, stepping round. Returns the frame it is done at."""
        self.up()
        off = wrap(angle - self.heading) if abs(angle - self.heading) <= math.pi else angle - self.heading
        self.gait = GAITS["walk"]
        n = max(4, int(round(max(seconds or 0.0, 0.3 + abs(off) / math.radians(115.0)) * self.fps)))
        start = self.heading
        for i in range(1, n + 8):                         # (a few frames more: a body follows what is asked a little late)
            wanted = start + off * fade(min(1.0, i / n))
            self.tick(lambda _, wanted=wanted: (0.0, (wanted - self.heading) * 9.0))
        for _ in range(int(1.5 * self.fps)):              # until every foot is under its place again
            self.tick()
            if not self.steps:
                break
        return len(self.frames)

    def up(self):
        """Gets up, if it sits or lies: what follows is done on four legs."""
        if any(abs(self.aim[key] - POSES["stand"][key]) > 1e-9 for key in ("rear", "fore", "ahead", "tuck")):
            self.ease("stand", -0.5)

    def ease(self, name, seconds=0.0):
        """Eases into a pose (sitting, lying, ...) and stays in it for a
        while. An end of the body that goes down has its feet put where
        they belong first; one that comes up puts them there after: legs
        that are folded do not step. Returns the frame after."""
        pose, now = POSES[name], len(self.frames)
        self.later = []
        for height, feet in (("rear", "tuck"), ("fore", "ahead")):
            steps = abs(pose[feet] - self.aim[feet]) > 0.25          # the feet have to go somewhere else
            first, then = (feet, height) if pose[height] < self.aim[height] else (height, feet)
            self.aim[first] = pose[first]
            self.later.append((now + (int(0.6 * self.fps) if steps else 0), then, pose[then]))
        self.aim["nose"], self.aim["tail"] = pose["nose"], pose["tail"]
        return self.wait(now + max(1, int(round((1.9 + seconds) * self.fps))))

    def hop(self, far=0.0, high=None, over=0.0, on=False, dive=False):
        """A leap: off all four feet, through the air, down on them again.
        `far`: how far (the ground), `high`: how high the body rises, `over`:
        what its feet have to clear. Returns the frame it stands again."""
        b, dt = self.build, self.dt
        if not on:
            self.up()
        rise = max(high if high is not None else 0.45 * b.height, over + 0.35 * b.leg)
        span = max(0.4, 2.7 * math.sqrt(2.0 * rise / 9.81))               # a little slower than a real fall, so it reads
        n = max(6, int(round(span * self.fps)))
        was = dict(self.aim)
        # it gathers itself ...
        self.aim = dict(was, rear=was["rear"] * 0.72, fore=was["fore"] * 0.8)
        self.tilt["pitch"] = math.radians(6.0)
        for _ in range(max(3, int(self.GATHER * self.fps))):
            self.tick((lambda _: (self.speed, 0.0)) if on else None)
        # ... pushes off with the hind legs, tucks all four, stretches the fore legs for the ground
        speed = far / (n * dt) / b.leg if far else self.speed
        self.aim = dict(was)
        for i in range(1, n + 1):
            u = i / n
            self.rise = rise * 4 * u * (1 - u)
            self.tilt["pitch"] = (math.radians(38.0) * math.cos(math.pi * u ** 0.8) if dive else math.radians(24.0) * math.cos(math.pi * u) * (1.0 if far or on else 0.35))
            self.arch = math.radians(14.0) * math.sin(math.pi * u)
            tuck = math.sin(math.pi * min(1.0, u * 1.15))
            for k in range(4):
                fore_leg = k >= 2
                stretch = (0.45 if fore_leg else -0.35) * (u - 0.5) * 2.0 if far or on else 0.0
                self.carried[k] = np.array([b.reach[k] * (stretch + (0.12 if fore_leg else -0.05)), 0.0, -b.reach[k] * (0.95 - 0.4 * tuck)])
            self.speed = speed if far else self.speed
            self.tick(lambda _, s=self.speed: (s, 0.0))
        # ... and lands: the feet stand where they come down, the body gives
        self.rise, self.arch = 0.0, 0.0
        self.tilt["pitch"] = 0.0
        running = on and abs(self.speed) > 0.5
        if running:                                       # out of a run it lands running: the fore feet are just down, the hind feet follow
            self.steps, self.clock = True, (self.gait["beat"][FL] + 0.04) % 1.0
        stand, rate = self._stand(), self._rate()
        for k in range(4):
            held, paw, phase = self.carried[k], self.paws[k], self._phase(k)
            self.carried[k], paw.since = None, 0.0
            if not running:
                paw.at, paw.down = np.append(self._home(k), 0.0), True
            elif phase < stand:                           # it stands: where a foot stands that is this far through its turn
                at = self._home(k) + turn2(self.heading, (0.5 - phase / stand) * stand / rate * self._drift(k))
                paw.at, paw.down = np.array([at[0], at[1], 0.0]), True
            else:                                         # it is still in the air and comes down when its turn comes
                paw.down, paw.span, paw.high = False, max(0.06, (1.0 - phase) / rate), 0.0
                paw.start, paw.from_high = held[:2].copy(), max(0.0, self.over[k] + held[2])
        for key in ("rear", "fore"):                      # the legs take the weight: the body dips, the harder the higher it fell
            self.pose[key].v -= min(2.4, 1.2 + 0.5 * rise / b.height)
        if not on:
            self.speed *= 0.3
            for _ in range(int(0.5 * self.fps)):
                self.tick()
        return len(self.frames)

    def leap(self, towards, over, span):
        """A leap over something out of a run (`go` with on), and to a stand behind it. Returns the frame it stands at."""
        self.hop(far=span, over=over, on=True)
        for _ in range(int(3.0 * self.fps)):
            self.tick()
            if not self.steps and abs(self.speed) < 0.05:
                break
        return len(self.frames)

    def greet(self, seconds=2.4):
        """Lifts a fore paw and waves it. Returns the frame it stands on four feet again."""
        b, k = self.build, FR
        n = max(12, int(round(seconds * self.fps)))
        self.wait(len(self.frames) + 2)
        for i in range(1, n + 1):
            on = swell(i, n, 0.3 * self.fps)
            beat = math.sin(2 * math.pi * 2.3 * i / self.fps)
            self.tilt["roll"] = math.radians(4.0) * on    # its weight goes to the other side
            self.carried[k] = np.array([b.reach[k] * (0.1 + 0.55 * on), 0.0, -b.reach[k] * (0.97 - on * (0.5 + 0.12 * beat))])
            self.tick()
        self.carried[k], self.tilt["roll"] = None, 0.0
        self.paws[k].at, self.paws[k].down, self.paws[k].since = np.append(self._home(k), 0.0), True, 0.0
        return self.wait(len(self.frames) + 3)

    def wags(self, seconds=1.5):
        self.wagging = 1.0
        end = self.wait(len(self.frames) + max(6, int(round(seconds * self.fps))))
        self.wagging = 0.0
        return end

    def shakes(self, seconds=1.4):
        """Shakes itself, from the head to the tail."""
        n = max(8, int(round(seconds * self.fps)))
        for i in range(1, n + 1):
            self.shake = swell(i, n, 0.3 * self.fps)
            self.tick()
        self.shake = 0.0
        return self.wait(len(self.frames) + 4)

    def sniffs(self, seconds=2.2, eats=False):
        """Puts its nose to the ground and noses about (or eats what is there)."""
        self.up()
        n = max(12, int(round(seconds * self.fps)))
        was = dict(self.aim)
        self.aim = dict(was, fore=was["fore"] * 0.86, tail=-0.15)
        for i in range(1, n + 1):
            on = swell(i, n, 0.45 * self.fps)
            self.gaze["up"] = -1.05 * on + (0.07 * math.sin(2 * math.pi * 3.0 * i / self.fps) * on if eats else 0.0)
            self.gaze["aside"] = 0.0 if eats else 0.3 * on * math.sin(2 * math.pi * 0.9 * i / self.fps)
            self.tick()
        self.aim, self.gaze = was, {"aside": 0.0, "up": 0.0}
        return self.wait(len(self.frames) + 5)

    def howls(self, seconds=2.4):
        """Lifts its nose to the sky."""
        n = max(12, int(round(seconds * self.fps)))
        for i in range(1, n + 1):
            self.gaze["up"] = 1.0 * swell(i, n, 0.5 * self.fps)
            self.tick()
        self.gaze["up"] = 0.0
        return self.wait(len(self.frames) + 5)

    def digs(self, seconds=2.4):
        """Scrapes the ground with its fore paws, one after the other."""
        b = self.build
        self.up()
        n = max(12, int(round(seconds * self.fps)))
        was = dict(self.aim)
        self.aim = dict(was, fore=was["fore"] * 0.8, nose=-0.6, tail=0.5)
        self.wait(len(self.frames) + int(0.3 * self.fps))
        for i in range(1, n + 1):
            on = swell(i, n, 0.2 * self.fps)
            for k, shift in ((FL, 0.0), (FR, 0.5)):
                u = (3.2 * i / self.fps + shift) % 1.0     # forwards through the air, back over the ground
                x = (0.45 - 0.55 * u / 0.6) if u < 0.6 else (-0.1 + 0.55 * (u - 0.6) / 0.4)
                z = 0.0 if u < 0.6 else 0.22 * math.sin(math.pi * (u - 0.6) / 0.4)
                self.carried[k] = np.array([b.reach[k] * x * on, 0.0, -self.over[k] * (1.0 - z * on)])
            self.tick()
        for k in (FL, FR):
            self.carried[k] = None
            self.paws[k].at, self.paws[k].down, self.paws[k].since = np.append(self._home(k), 0.0), True, 0.0
        self.aim = was
        return self.wait(len(self.frames) + 6)

    def pounce(self, far=None):
        """Crouches, wiggles, leaps high and comes down nose first, as a fox does on a mouse. Returns the frame it stands again."""
        b = self.build
        self.up()
        was = dict(self.aim)
        self.aim = dict(POSES["crouch"], nose=-0.2)
        n = int(0.7 * self.fps)
        for i in range(n):                                # it takes aim
            self.tilt["roll"] = math.radians(3.0) * math.sin(2 * math.pi * 3.0 * i / self.fps) * swell(i, n, 4)
            self.tick()
        self.tilt["roll"] = 0.0
        self.aim = was
        self.hop(far=far if far is not None else 1.1 * b.long, high=0.95 * b.height, dive=True)
        self.aim = dict(was, fore=was["fore"] * 0.6, nose=-0.75, tail=0.7)       # nose and fore paws on what it caught
        self.wait(len(self.frames) + int(0.7 * self.fps))
        self.aim = was
        return self.wait(len(self.frames) + int(0.5 * self.fps))

    def look(self, target, start, end, part=1.0):
        self.looks.append((int(start), int(end), target, part))

    def nod(self, kind, start, frames, times):
        self.heads.append((int(start), int(frames), kind, times))

    def sway(self, seconds, lean=0.16):
        n = max(6, int(round(seconds * self.fps)))
        self.sways.append((len(self.frames), n, lean))
        return self.wait(len(self.frames) + n)

    def resize(self, scale):
        """Grows or shrinks where it stands."""
        ratio = scale / self.build.scale
        at = self.origin
        self.build = self.build.sized(scale)
        self.xy = at + (self.xy - at) * ratio
        for paw in self.paws:
            paw.at[:2] = at + (paw.at[:2] - at) * ratio
            paw.start, paw.goal = paw.start * ratio, paw.goal * ratio

    def move(self, by):
        by = np.asarray(by, dtype=float)
        self.xy = self.xy + by
        shift = np.array([by[0], by[1], 0.0])
        for paw in self.paws:
            paw.at = paw.at + shift
        for frame in self.frames:
            frame["origin"], frame["root"] = frame["origin"] + shift, frame["root"] + shift
            if "paws" in frame:
                frame["paws"] = [(at + shift, down, held) for at, down, held in frame["paws"]]
                frame["hips"] = [h + shift for h in frame["hips"]]

    @property
    def origin(self):
        """Where the model's origin is on the ground now."""
        if self.frames:
            return self.frames[-1]["origin"][:2].copy()
        b = self.build
        return self.xy - turn2(self.heading, (b.rear[:2] + b.fore[:2]) / 2)

    def finish(self, last):
        """For every frame up to `last`: where the model's origin is, how far
        it is turned, how large it is, the turns of its bones, and how far
        the legs' joints have moved from their places (as the model stands)."""
        self.wait(last + 1)
        out, swings = [], Swings()
        for f, frame in enumerate(self.frames[:last + 1]):
            b = frame["build"]
            whole = dict(frame["whole"])
            self._head(f, frame, whole)
            turns = {b.hips: whole[b.hips], b.chest: qmul(qinv(whole[b.hips]), whole[b.chest]),
                     b.neck: qmul(qinv(whole[b.chest]), whole[b.neck]), b.skull: qmul(qinv(whole[b.neck]), whole[b.skull])}
            before = whole[b.hips]
            for i, (aside, up) in zip(b.tails, frame["tail"]):
                turns[i] = qmul(qz(aside), qaxis(Y, up))         # from the bone before it (it points backwards: this lifts its end)
                whole[i] = qmul(before, turns[i])
                before = whole[i]
            for k, (upper, shin) in enumerate(b.legs):
                parent = b.hips if k < 2 else b.chest
                for bone, of in ((upper, parent), (shin, upper)):
                    whole[bone] = qmul(swings.turn(bone, qrot(whole[of], b.dir[bone]), frame["limbs"][bone]), whole[of])
                    turns[bone] = qmul(qinv(whole[of]), whole[bone])
            to_model, back = qz(b.facing), qz(-b.facing)
            moves = {b.names[upper]: qrot(to_model, X * (frame["slide"][k] / b.scale)) for k, (upper, _) in enumerate(b.legs)}
            out.append((frame["origin"], frame["heading"] - b.facing, b.scale, {b.names[i]: qmul(qmul(to_model, q), back) for i, q in turns.items()}, moves))
            if self.watch:
                frame["joints"] = self._joints(frame, whole)
        return out

    def _head(self, f, frame, whole):
        """Neck and head of a frame: level whatever the body does, looking
        where it goes or at what it was told to look at, nodding with the steps."""
        b = frame["build"]
        chest = whole[b.chest]
        ahead = qrot(chest, X)
        aside, up = frame["nose"]
        for start, end, target, part in self.looks:
            if start <= f < end:
                at = target(f)
                if at is None:
                    continue
                w = part * swell(f - start, end - start, 0.4 * self.fps)
                eye = frame["origin"] + qrot(qz(frame["heading"]), b.head[b.skull]) * np.array([1.0, 1.0, 0.0]) + np.array([0.0, 0.0, frame["root"][2] + 0.3 * b.leg])
                to = qrot(qz(-frame["heading"]), np.asarray(at, dtype=float) - eye)
                aside += w * (min(1.5, max(-1.5, math.atan2(to[1], to[0]))) - aside)
                up += w * (min(0.9, max(-0.9, math.atan2(to[2], max(1e-6, math.hypot(to[0], to[1]))))) - up)
        for start, n, kind, times in self.heads:
            if start <= f < start + n:
                u = (f - start) / n
                if kind == "nod":
                    up -= math.radians(24.0) * (0.5 - 0.5 * math.cos(2 * math.pi * times * u))
                else:
                    aside += math.radians(28.0) * math.sin(2 * math.pi * times * u) * min(1.0, 6 * min(u, 1 - u))
        idle = 1.0 - frame["go"]
        t = frame["t"]
        aside += idle * math.radians(16.0) * math.sin(2 * math.pi * 0.11 * t + self.seed) * math.sin(2 * math.pi * 0.047 * t + 2.0 * self.seed)
        up += idle * math.radians(4.0) * math.sin(2 * math.pi * 0.17 * t + 0.7 * self.seed) + frame["bob"]
        # Seen from the animal the head is level: what the chest does is taken out again, most of it.
        lean = math.asin(max(-1.0, min(1.0, float(ahead[2]))))
        swung = math.atan2(ahead[1], ahead[0])
        steady = 0.75
        roll = qaxis(X, 1.6 * frame["shiver"])
        neck = qmul(qmul(qz(0.45 * aside - 0.5 * steady * swung), qaxis(Y, -(0.45 * up - 0.5 * steady * lean))), chest)
        head = qmul(qmul(qmul(qz(aside - steady * swung), qaxis(Y, -(up - steady * lean))), chest), roll)
        whole[b.neck], whole[b.skull] = neck, head

    def _joints(self, frame, whole):
        """Where the bones' ends are (the world), to look at them."""
        b, turn, at = frame["build"], qz(frame["heading"]), {}
        slid = {upper: frame["slide"][k] for k, (upper, _) in enumerate(b.legs)}
        for i in range(len(b.names)):
            p = b.parent[i]
            start = frame["origin"] + qrot(turn, b.head[i]) if p < 0 else at[p][0] + qrot(turn, qrot(whole.get(p, SAME), b.head[i] - b.head[p] + X * slid.get(i, 0.0)))
            at[i] = (start, start + qrot(turn, qrot(whole.get(i, whole.get(p, SAME)), b.tail[i] - b.head[i])))
        return {b.names[i]: v for i, v in at.items()}
