# MERIDIAN — a new Echo arena

A brand-new map, designed from scratch and built as fresh geometry. Nothing is
reused from `mpl_arena_a`; only its **envelope and symmetry** are borrowed, so a
player who knows Echo reads the space correctly on arrival.

---

## 1. What the stock arena establishes

Measured off `mpl_arena_a`, not assumed:

| quantity | stock | MERIDIAN |
|---|---|---|
| length | 157 m (`z` −78.6 … 78.6) | 156 m (`y` ±78) |
| width | 32 m (`x` ±16.1) | 32 m at the belly |
| height | 20 m (`y` −11.4 … 9.0) | 20 m at the belly |
| obstacle reach | \|z\| ≤ 70 | \|y\| ≤ 72 |
| symmetry | 180° about vertical | same |

**Axes.** Blender: `X` width, `Y` the long axis, `Z` up — the stock game space
through the importer's basis (game y-up → Blender z-up).

**Symmetry.** Only `y > 0` is authored; the rest is the 180° turn about the
vertical, `(x, y) → (−x, −y)`.

---

## 2. The central idea: a hull that turns

A long tube with obstacles floating in it is a corridor, not an arena — that was
the first draft's mistake, and it read as a drainpipe with slabs in it.

MERIDIAN is instead **one continuous piece of architecture**: a hexagonal hull
that **twists 60° from end to end**, 30° each way from the middle.

That single decision does most of the design work:

* **Every hull face becomes a bank surface.** A twisted hexagon is a ruled
  surface — no face is parallel to the long axis, so a disc thrown along any wall
  is turned. There is no "dead" wall anywhere in the map.
* **The bank direction changes as you travel.** The same throw off the same face
  gives a different angle at `y = 20` than at `y = 55`. Players learn the map by
  learning *where* to bank, not just how.
* **It is legible.** The six spars running the hull's corners are visibly
  helical. You can see which way the map turns, so you can predict it. A twist
  you cannot see would just be noise.
* **It is symmetric for free.** The twist is odd about the centre
  (`θ(−y) = −θ(y)`), which is exactly the condition for the 180° symmetry to hold.

The hull section is not constant. It **bellies out** to the full 32 × 20 m at the
middle and **necks down** to a 17 × 11 m throat at each goal. The middle is where
fights happen and wants room; the approach wants to be tight so the goal is
defensible.

---

## 3. Elements

### The hull
A twisted hexagonal shell, 0.5 m thick, lofted over 96 stations. Belly 1.12 ×
nominal at the centre, tapering to 0.42 × behind each goal.

### Spars — the six corners
Chunky 1.3 m beams following each hexagon corner down the whole length, with
collar rings every 12 m. They are the map's scale reference and its handhold
structure, and their helix is what makes the twist readable.

### Ribs — transverse frames every 6 m
Not flat plates: each rib is a **wedge** in section, pointing inward, with both
exposed faces sloped ~34°. A flat rib would return a disc straight back at the
thrower, which teaches nothing; a wedge kicks it fore or aft depending on which
side was hit, so hull-hugging throws stay live and directional.

### Plating
Raised panels in each bay between ribs. Pure read — scale, surface interest, and
somewhere for light to catch.

### The Drum — `y = 0`
A hexagonal ring, 7 m radius, 2.4 m section, hanging on the axis with its six
segments each canted 25°. The hole through the middle is 9 m, so the fast centre
line stays open. Contesting the middle is a real decision because the drum's
faces throw wide — to one side lane or the other, never back down the middle.

### Vanes — `y = ±26, ±38, ±50`
Three per half. Each is a **folded plate**, 13 m × 7 m, hinged 22° along its
length, reaching inward from a hull face and rotated to follow the twist.

Folded, not curved, on purpose: a curved reflector focuses, and a focus makes
bounces unpredictable near it. Two flat facets give two clean, learnable bank
directions per vane.

### The Cowl — `y = ±62 … ±78`
The approach. The hull necks into a throat and six **petals** funnel inward
around the goal, each canted 26° and pitched 10° down.

**This is the primary bank surface and the first thing to learn.** From wide in
the attacking half with the goal screened, a petal gives a one-cushion line into
the mouth. The downward pitch converts a flat throw into a descending one, so the
disc arrives above the goalie's blocking plane.

### The goal and backboard
Ring at `y = ±72`, 2.4 m outer radius, 1.6 m mouth. Behind it a shallow convex
dome — convex so a miss is returned down the map and stays playable instead of
dying behind the goal.

---

## 4. Scoring lines the map is built for

| line | from | cushions |
|---|---|---|
| direct | on axis, attacking half | 0 |
| **petal bank** | wide, `\|x\| > 8` | 1 |
| **hull carry** | flat along a twisted face | 1 |
| **vane cross** | off a fold, into the far lane | 1 |
| **spar–petal** | corner beam, then a petal | 2 |
| drum scatter | midfield contest | 1 |

Section 6 of the build report measures which of these actually land.

---

## 5. What this does not do

* **It does not ship into Echo VR.** New instances need actor ids and
  `CActorDataResource` growth is not established — the level exporter refuses new
  objects for that reason. This is a complete, measured Blender build; wiring it
  into the game is a separate, unsolved problem.
* No lighting, texturing, handles or player-collision authoring. Geometry and
  bounce behaviour are the deliverable.
