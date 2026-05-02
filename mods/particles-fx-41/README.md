# particles-fx

GPU-instanced particle effects for fire, gunfire, and vehicle exhaust. Replaces the vanilla sprite-anim "Fire"/"Smoke" attachments with shader-driven particles that are tinted by surrounding tile lighting and drift with the in-world wind.

## What you see

- **Burning fires** (campfires, structure fires, fire-on-ground tiles) — flame core (peak-and-shrink) plus rare fast-rise sparks; intensity tracks `IsoFire.LifeStage` from ignition through peak burn to smoke-only decay.
- **Lit fireplaces** — flame parked at the correct edge of the tile (north-mounted vs west-mounted resolved from the fireplace's wall-collide flag); intensity scales with current fuel.
- **Lit metal drums** (`IsoThumpable` with `isLit=true` modData) — flame + smoke column above the drum.
- **Burning characters** — flame trail follows the moving zombie/player. Two stacked layers at slightly different heights for depth.
- **Gunfire** — short golden muzzle flash + grey alpha-blended smoke puff at the gun tip, drifting outward and upward.
- **Vehicle exhaust** — grey puff at the rear-corner tailpipe; pulse rate scales with engine RPM (~5 Hz idle, ~12 Hz revving). Particles stay locked to the spawn tile after the vehicle drives off, so the puff doesn't snap with the car.
- **Smoking** — thin grey wisp drifts up from a character's face while smoking a cigarette; pulses with the in-game timed action.

All particles are tinted by sampling the surrounding `IsoGridSquare.lighting[playerIndex]` so they look correct under interior darkness, dusk, lit interiors, and overcast weather. Wind drift comes from a deterministic gust simulator (three incommensurate sines) so smoke and sparks sway with the same rhythm as the world.

## What it changes

- New shader registry — emitters bake their GLSL source into static strings and register them via `NecroidShaderRegistry`; `ShaderUnit.preProcessShaderFile()` hook returns the registered source instead of hitting disk for any `media/shaders/necroid_*` lookup.
- New emitter base + 5 emitters — `Particles` is promoted from a thin wrapper to a real base class owning the storage arrays, GPU buffer, the unified write-frame loop, age-out, and per-tile dispatch. `ParticlesMuzzle`, `ParticlesGunSmoke`, `ParticlesFireSmoke`, `ParticlesExhaust`, `ParticlesFlame` each carry only a `Config`, GLSL source strings, and a `spawn(...)` method.
- New emitter registry — `NecroidEmitterRegistry.bootstrap()` enumerates the 5 emitters (the only place they're listed by name); `NecroidParticleHook.perFrame()` and `ModelManager.RenderParticles()` iterate the registry instead of hard-coding 5 dispatch blocks each.
- New tile-anchored sources — `FireSmokeSource` / `FireplaceFlameSource` / `MetalDrumFireSource` carry the spawn-pacing accumulator that used to live as inline `*Accum` floats on `IsoFire` / `IsoFireplace` / `IsoThumpable`. Each holds a `WeakReference` to its owner; the registry's per-frame source-tick auto-evicts on chunk unload.
- New owner-bound helpers — `NecroidVehicleExhaust` and `NecroidCharacterBurning` carry the per-frame accum + spawn logic that has to follow a moving entity (vehicle pose for tailpipe; character position for burning flesh). Vanilla patches in `BaseVehicle.update()` and `IsoGameCharacter.update()` are 1-line delegates.
- Per-tile rendering — `IsoCell` calls `NecroidParticleHook.enqueueTile(x, y)` once per tile during `MinusFloorCharacters`; `SpriteRenderer.drawParticles` queues the dispatch so it runs on the render thread in iso walk order. `ParticlesExhaust` keeps a `HashSet<Long>` of tiles with live particles so abandoned-by-vehicle puffs still get dispatched.

## Architecture

### Adding a new emitter

1. Create `ParticlesXxx extends Particles` with static `VERT_SRC` / `FRAG_SRC` GLSL strings and a private constructor calling `super(new Particles.Config(maxParticles, lifetime, usesTint, blendSrc, blendDst, "necroid_xxx", VERT_SRC, FRAG_SRC))`.
2. Add a `spawn(...)` method with whatever signature the call sites need — write into the inherited `x[]` / `y[]` / `z[]` / `vx[]` / `vy[]` / `vz[]` / `tileX[]` / `tileY[]` / `spawnT[]` / `hash[]` / `alive[]` / `spawnWindAccumX[]` / `spawnWindAccumY[]` arrays.
3. Optionally override `windFactor(int i)` (default `1.0F`; e.g., Muzzle returns `0.0F` for no drift, Flame splits `0.22F` core / `1.0F` spark by hash).
4. Add one line to `NecroidEmitterRegistry.bootstrap()` calling `registerEmitter(ParticlesXxx.getInstance())`.

No vanilla patches need to be touched. The render loop in `ModelManager.RenderParticles` iterates the registry; it picks up the new emitter automatically.

### Adding a new tile-anchored spawner

1. Extend `AbstractTileParticleSource`. Hold a `WeakReference` to whatever vanilla object the source represents (so it auto-expires on chunk unload).
2. Implement `tick()` — read the owner's current state (e.g. `isLit()`, `LifeStage`, fuel level), accumulate dt × your spawn rate, and call `ParticlesXxx.getInstance().spawn(...)` with the source's tile coords.
3. From the vanilla object's `update()`, lazy-create + register on first eligible frame:

   ```java
   if (!GameServer.bServer && this.square != null && this.necroidSource == null) {
      this.necroidSource = new MyTileSource(this);
      NecroidEmitterRegistry.registerSource(this.necroidSource);
   }
   ```

   That's it for the patch. The source self-evicts on `expire()` or when its WeakReference clears.

### Owner-bound spawners

When the spawn position has to be recomputed every frame from the live owner state (vehicle tailpipe pose; character location), use a per-owner helper class instead of a tile-anchored source. See `NecroidVehicleExhaust` and `NecroidCharacterBurning` — the vanilla patch is then `private final NecroidXxx helper = new NecroidXxx();` plus `this.helper.tick(this);`.

## Compatibility

- **Target:** client. `clientOnly: true` — relies on render-thread shader compilation, lighting sampling, and per-player tile dispatch. Server install is rejected.
- Stacks cleanly with `no-radio-fzzt`, `more-zoom`, `admin-xray`. Doesn't touch radio, zoom, or LOS code.
- Suppresses vanilla "Fire"/"Smoke" sprite attachments on `IsoFire`, `IsoFireplace`, and burning characters (those are what the GPU particles replace). Vanilla light-source emission is left untouched — fires still illuminate rooms the same way they did before.
- Touches `Particles.java`, `ShaderUnit.preProcessShaderFile()`, `ModelManager.RenderParticles()`, `IsoCell.performRenderTiles()`. A future PZ patch that renames any of these (or reorders the per-tile dispatch path) will require a re-derivation.

## Verification

1. `necroid test` — compile gate.
2. Light a campfire — flames at all stages (ignition, peak, decay) plus grey smoke that drifts with wind.
3. Light a fireplace mounted on a north wall, then one on a west wall — flame parks against the correct edge each time.
4. Light a metal drum (right-click → Light, with fuel) — flame + smoke above the drum; extinguish — particles stop cleanly.
5. Set a zombie on fire (Molotov) — flame trails the moving zombie's position frame-perfectly.
6. Drive a vehicle in idle, then rev — exhaust pulse rate visibly increases. Drive past a tile mid-puff — the abandoned puff finishes dissipating in place instead of snapping with the car.
7. Fire any gun — gold muzzle flash + grey smoke puff at the gun tip, drifting forward and dispersing.
8. Walk far enough to chunk-unload an active fire/fireplace/drum, then walk back — fresh source auto-registers, particles resume.
