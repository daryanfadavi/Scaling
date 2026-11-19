"""Busy day: a composite "day in the life" that combines what the other scenarios test
one at a time. Meant to run at --duration 900.

    users
     40 |                 ________
        |                |        |
     25 |       _ ...    |        |    _
        |    /  |  | \   |        |  /| |\
     10 |___/   |__|  \__|        |_/ |_| \________
        0               40%      55%        80%      100%   of the duration
          wave + bursts    spike    wave + bursts   quiet

- A smooth wave of normal traffic, 10 -> 25 -> 10 users, across the first 80% of the
  run: does the controller track a predictable pattern?
- 3-5 short bursts on top of the wave (+8 to +15 users, each 3-6% of the run, 27-54 s
  at 900 s): does it over-react or flap? They come from a fixed seed, so every
  controller gets identical traffic, and they never overlap the spike.
- One sudden spike to 40 users in total from 40% to 55%: how fast does it react?
- Back to 10 users for the last 20%, so scale-down shows.

Never above 40 users (about 9 pods at the HPA's 60% target, under the 10-pod cap).
Above that every controller saturates and they all look the same.
Everything is a fraction of the run, so --duration stretches the same pattern.
"""

import math
import random

import common
from common import AppUser

LOW, WAVE_HIGH = 10, 25   # users: the wave's base and peak
WAVE_END = 0.80           # the wave (and the bursts) end here; LOW after
SPIKE_START, SPIKE_END, SPIKE_USERS = 0.40, 0.55, 40
MAX_USERS = 40            # hard cap, see above
SEED = 7
# Bursts only go where they can't touch the spike or the quiet end (with a small gap).
BURST_WINDOWS = [(0.05, 0.37), (0.58, 0.77)]


def make_bursts(seed: int) -> list[tuple[float, float, int]]:
    """3-5 bursts as (start, end, extra_users), start and end as fractions of the run,
    sorted, non-overlapping, at least 3% of the run apart."""
    rng = random.Random(seed)
    count = rng.randint(3, 5)
    bursts = []
    while len(bursts) < count:
        window_start, window_end = rng.choice(BURST_WINDOWS)
        length = rng.uniform(0.03, 0.06)
        start = rng.uniform(window_start, window_end - length)
        end = start + length
        if all(end + 0.03 <= s or start >= e + 0.03 for s, e, _ in bursts):
            bursts.append((round(start, 4), round(end, 4), rng.randint(8, 15)))
    return sorted(bursts)


BURSTS = make_bursts(SEED)


def wave_users(progress: float) -> float:
    """One smooth cycle LOW -> WAVE_HIGH -> LOW over [0, WAVE_END)."""
    phase = progress / WAVE_END
    return LOW + (WAVE_HIGH - LOW) * (1 - math.cos(2 * math.pi * phase)) / 2


class User(AppUser):
    """Each user requests /work about twice a second (see common.py)."""


class BusyDay(common.DurationShape):
    spawn_rate = 100  # bursts and the spike start and stop within a second

    def users_at(self, progress):
        if progress >= WAVE_END:
            return LOW
        if SPIKE_START <= progress < SPIKE_END:
            return SPIKE_USERS
        users = wave_users(progress)
        for start, end, extra in BURSTS:
            if start <= progress < end:
                users += extra
        return min(users, MAX_USERS)
