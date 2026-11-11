# The recorded motions

`motions.npz` is made from recordings of the **CMU Graphics Lab Motion
Capture Database** (<http://mocap.cs.cmu.edu>):

> The data used in this project was obtained from mocap.cs.cmu.edu.
> The database was created with funding from NSF EIA-0196217.

Carnegie Mellon says of them: "This data is free for use in research and
commercial projects worldwide", and asks for the lines above. The recordings
are read from the BVH conversion by Bruce Hahne (cgspeed.com), who places
no further restrictions on it, as mirrored at
`github.com/una-dinosauria/cmu-mocap` (commit `09a07f54`).

Only what a figure of any build needs is kept (see `motion.py`): no joint
positions and no skeleton of the people who were recorded.

## Making the file again

    python3 ops/scene/mocap/build.py --bvh /some/folder

`mocap/clips.json` says which stretch of which recording is which motion,
`mocap/sources.lock` has the SHA-256 of every recording used. What is not in
the folder is fetched from the mirror and checked against it.

Other motion data may only be added if its terms allow commercial use and
passing it on: this repository is public.

## Which recording is which motion

`mocap/clips.json` is the list. Beyond standing, the gaits, the leaps, the
wave and the spin it has, since Nachtrag 39: `kick` (135_04, a front kick
with a step into it), `punch` (143_23) and `fall` (90_18, pulled off the
feet). When in a blow the hit lands is written in `scene.py` (`HIT`).
