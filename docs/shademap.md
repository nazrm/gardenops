# ShadeMap Integration

GardenOps can use [ShadeMap](https://shademap.app/about) for interactive sun
and shade analysis. ShadeMap is an external paid service. GardenOps does not
include, resell, proxy, sponsor, or manage ShadeMap access. Each GardenOps
operator is responsible for purchasing or requesting the right ShadeMap API
access, configuring their own keys, paying any provider costs, and following the
ShadeMap terms that apply to their deployment.

If no ShadeMap keys are configured, GardenOps still runs, but the ShadeMap panel
cannot initialize. The backend returns `503` from `/api/shademap/config` until
the required keys and tile-signing secret are present.

## What GardenOps Provides

GardenOps provides the application plumbing around ShadeMap:

- a same-origin backend API for ShadeMap config, state, calibration, and
  obstacles
- storage for per-garden ShadeMap state, selected plot, mode, preset, analysis
  timestamp, calibration, and manually entered obstacles
- signed same-origin terrain tile URLs so the browser does not receive raw
  terrain-source URLs
- PostgreSQL caching for SDK validation, building features, and terrain tiles
- rate limits, daily provider budgets, concurrency guards, and distinct-request
  guards around expensive routes
- optional local LiDAR terrain support when you keep terrain files outside Git

GardenOps does not provide:

- a ShadeMap account
- a ShadeMap API key or public client key
- bundled paid terrain/building data
- legal permission to use ShadeMap in your domain, organization, or product
- a guarantee that a particular ShadeMap pricing tier allows your use case

## Required ShadeMap Access

Use the official ShadeMap site to obtain access:

- ShadeMap product, API-key, docs, and pricing entry point:
  [https://shademap.app/about](https://shademap.app/about)

The ShadeMap page includes API-key and pricing actions. Review the current terms
there before deploying. The public GardenOps documentation intentionally does
not copy pricing details because provider plans can change.

GardenOps expects two kinds of ShadeMap key material:

| Need | GardenOps variables | Visibility |
|---|---|---|
| Server-side ShadeMap API validation | Platform-admin provider settings, or fallback env vars `SHADEMAP`, `SHADEMAP_API_KEY`, or `SHADEMAP_KEY` | Secret; stays on the server |
| Browser/client ShadeMap SDK access | `SHADEMAP_PUBLIC_API_KEY`, `SHADEMAP_PUBLIC_KEY`, or `SHADEMAP_CLIENT_KEY` | Sent to the authenticated browser by `/api/shademap/config` |

Depending on your ShadeMap account, these may be separate values or the same
provider-issued value. Treat anything named secret/private as server-only, and
confirm with ShadeMap which key is safe to expose to a browser.

Server-side ShadeMap keys are platform-admin-only. GardenOps no longer accepts
per-user ShadeMap keys from `/api/auth/me/settings` or admin user management.

## Minimum Configuration

For a production deployment with ShadeMap enabled:

```bash
APP_SECRETS_ENCRYPTION_KEY=change-me
SHADEMAP=change-me
SHADEMAP_PUBLIC_API_KEY=change-me
SHADEMAP_TILE_SIGNING_SECRET=<generate-a-unique-random-secret>
SHADEMAP_SHARE_URL=
SHADEMAP_LAT=51.50095
SHADEMAP_LNG=-0.12448
SHADEMAP_ZOOM=17
SHADEMAP_LABEL=Garden
```

Replace all `change-me` values with real deployment-specific values and replace
the example coordinates with your own garden location. You may enter the
server-side ShadeMap key through the platform admin UI instead of `SHADEMAP`;
the public/client key and tile-signing secret remain environment configuration.
The defaults in the public repository are placeholders and are not intended to
describe your site.

`SHADEMAP_TILE_SIGNING_SECRET` is not a ShadeMap key. It is a GardenOps-owned
HMAC secret used to sign expiring same-origin terrain tile URLs. Use a unique
random value per deployment and keep it private.

## How Requests Flow

1. The browser opens the GardenOps ShadeMap panel.
2. The browser calls `GET /api/shademap/config`.
3. GardenOps checks for a public/client ShadeMap key.
4. GardenOps validates that server-side ShadeMap access is configured, caching a
   successful SDK validation for a limited time.
5. GardenOps returns public config to the authenticated browser: client key,
   default location, share URL, zoom, and a signed same-origin terrain URL
   template.
6. The browser renders the ShadeMap layer and calls GardenOps routes for
   features, terrain tiles, saved state, calibration, and obstacles.

The browser should call GardenOps routes, not arbitrary upstream services.
GardenOps controls the upstream allowlist, request limits, cache behavior, and
terrain URL signing.

## Core Concepts

### Share URL

`SHADEMAP_SHARE_URL` is a ShadeMap share/bookmark URL for the initial map view.
It is not an API key. Set it to your own public-safe default area or leave it
empty and rely on `SHADEMAP_LAT`, `SHADEMAP_LNG`, and `SHADEMAP_ZOOM`.

Do not publish an exact private home/garden location unless that is intentional
for your instance.

### Location Defaults

`SHADEMAP_LAT`, `SHADEMAP_LNG`, `SHADEMAP_ZOOM`, and `SHADEMAP_LABEL` control
the initial map position when no saved state exists for the garden. They do not
grant ShadeMap access and they do not replace API keys.

### State

GardenOps stores per-garden ShadeMap state:

- `mode`: `shadow` or `sun-hours`
- `preset`: `now`, `custom`, `spring`, `summer`, `autumn`, or `winter`
- `analysis_timestamp_ms`: the selected analysis time
- `selected_plot_id`: the plot currently being analyzed

State is included in GardenOps layout export/import so a garden can keep its
ShadeMap view when moved between instances.

### Calibration

Calibration maps GardenOps garden-grid coordinates to real-world latitude and
longitude. Without calibration, the ShadeMap layer can render at the configured
location, but plot-level alignment may be wrong.

GardenOps supports two calibration shapes in its data model:

- `house-corners`: map the four house/layout corners to latitude/longitude
- `two-point`: map an origin point and axis point to latitude/longitude

For most deployments, use the house-corners workflow in the UI if your garden
layout has a known house or reference rectangle. Save calibration only after the
overlay visibly lines up with the real site.

### Obstacles

Obstacles are GardenOps-owned shade objects, such as trees or structures, that
you add when they are missing, inaccurate, or private in upstream data.

Each obstacle stores:

- label
- kind: `tree` or `structure`
- latitude and longitude
- height in meters
- crown radius in meters
- optional linked GardenOps plot
- active/inactive state

Obstacle data stays in your GardenOps database and is included in layout
export/import. It should still be treated as potentially location-sensitive.

### Building Features

GardenOps fetches building/feature data through `/api/shademap/features`.
Requests are bounded by zoom, bounding-box size, distinct-bound limits, cache
miss rate limits, daily budgets, and concurrency limits.

The default upstream building sources are Overpass API endpoints. You can
override them:

```bash
SHADEMAP_OVERPASS_URL=
SHADEMAP_OVERPASS_URLS=
```

Use HTTPS sources only. GardenOps validates upstream URLs, blocks credentials in
upstream URLs, restricts upstream hosts, and rejects private or loopback
upstream IP addresses.

### Terrain Data And PNG Tiles

Shade analysis needs terrain elevation data. There are two supported ways to get
that data into GardenOps:

- Local terrain input: a private LiDAR `.laz` file configured with
  `SHADEMAP_LOCAL_TERRAIN_PATH`.
- Remote terrain input: a URL template for a Terrarium-compatible terrain tile
  service configured with `SHADEMAP_TERRAIN_URL_TEMPLATE`.

The `.png` path below is not a file that operators need to create or commit. It
is the HTTP tile format GardenOps serves to the browser after it has loaded
terrain from local LiDAR or from the configured remote terrain source:

```text
/shademap/terrain/{z}/{x}/{y}.png
```

The signed URLs expire. GardenOps validates tile coordinates, enforces terrain
rate limits, reads cached tiles when available, and stores generated/fetched
tiles in PostgreSQL.

The default remote terrain source is a public Terrarium PNG tile template.
Override it only if you operate a compatible source:

```bash
SHADEMAP_TERRAIN_URL_TEMPLATE=https://example.com/terrarium/{z}/{x}/{y}.png
```

Remote terrain responses must be `image/png`. GardenOps rejects unexpected
remote content types before caching or serving the tile, so do not point the
template at HTML landing pages, JSON APIs, or services that return mixed
formats for missing tiles.

### Local Terrain

You can point GardenOps at a local LiDAR `.laz` point-cloud file:

```bash
SHADEMAP_LOCAL_TERRAIN_PATH=/path/to/private-terrain.laz
SHADEMAP_LOCAL_TERRAIN_RESOLUTION_M=1.0
SHADEMAP_LOCAL_TERRAIN_MAX_POINTS=2000000
```

Local terrain files are often large and location-sensitive. Keep them outside
Git. The public repository intentionally excludes `.laz`/`.las` point clouds,
generated `.png` terrain tiles, cache files, and generated terrain artifacts.

When a requested tile is fully covered by local terrain, GardenOps can generate
the browser-facing PNG terrain tile locally from the `.laz` data. If local
coverage is partial or missing, GardenOps can fall back to the configured remote
terrain source.

Uploaded LAS/LAZ terrain is checked for CRS metadata, grid size, byte size, and
point count before storage and processing. Lower
`SHADEMAP_LOCAL_TERRAIN_MAX_POINTS` if your host has limited CPU or memory.

## Rate Limits And Budgets

ShadeMap-related routes can become expensive because map movement can trigger
many terrain and feature requests. GardenOps therefore layers several controls:

| Control | Example variables | Purpose |
|---|---|---|
| Base request limits | `SHADEMAP_FEATURES_RATE_LIMIT`, `SHADEMAP_TERRAIN_RATE_LIMIT` | Cap ordinary request volume |
| Cache-miss limits | `SHADEMAP_FEATURES_MISS_RATE_LIMIT`, `SHADEMAP_TERRAIN_MISS_RATE_LIMIT` | Protect upstream services when cache misses happen |
| Daily budgets | `SHADEMAP_FEATURES_MISS_DAILY_BUDGET_USER`, `SHADEMAP_TERRAIN_MISS_DAILY_BUDGET_GARDEN` | Bound per-user and per-garden provider usage |
| Concurrency | `SHADEMAP_FEATURES_MISS_CONCURRENCY_LIMIT`, `SHADEMAP_TERRAIN_MISS_CONCURRENCY_LIMIT` | Prevent worker exhaustion during bursts |
| Distinct bounds/tiles | `SHADEMAP_FEATURES_MAX_DISTINCT_BOUNDS`, `SHADEMAP_TERRAIN_MAX_DISTINCT_TILES` | Limit broad or highly unique map scans |

For public or multi-user deployments, start with the defaults and lower limits
if your ShadeMap plan, terrain source, or Overpass source has stricter quotas.

## Nginx

The production nginx example includes separate locations for:

```text
/api/shademap/features
/shademap/terrain/
```

Keep those locations if you expose ShadeMap. They let you apply separate edge
budgets to terrain and feature traffic while ordinary API routes keep their own
limits.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/shademap/config` returns `503` with public key text | Missing `SHADEMAP_PUBLIC_API_KEY`, `SHADEMAP_PUBLIC_KEY`, or `SHADEMAP_CLIENT_KEY` | Configure the browser-safe key from your ShadeMap access |
| `/api/shademap/config` returns `503` with API key text | Missing server-side `SHADEMAP`, `SHADEMAP_API_KEY`, or `SHADEMAP_KEY` | Configure your server-side ShadeMap key |
| `/api/shademap/config` returns `503` with tile secret text | Missing `SHADEMAP_TILE_SIGNING_SECRET` | Set a unique GardenOps tile-signing secret |
| Terrain tiles return `401` | Expired or invalid signed terrain URL | Refresh the ShadeMap panel or request a fresh config |
| Feature requests return `400` for large bounds | The visible map area is too broad for the zoom | Zoom in or raise `SHADEMAP_FEATURES_MAX_BBOX_TILES` deliberately |
| Terrain or features return `429` | Rate, budget, concurrency, or distinct-request guard tripped | Wait for the window to reset or tune the matching `SHADEMAP_*` limit |
| Local terrain is ignored | Path is unset, missing, not absolute/resolvable, or does not fully cover the tile | Check `SHADEMAP_LOCAL_TERRAIN_PATH`, file permissions, CRS metadata, and coverage |

## Validation

After changing ShadeMap configuration:

```bash
set -a
. ./.env.test.local
set +a
uv run python scripts/check_env_docs.py
uv run python -m pytest tests/test_shademap.py tests/test_feature_gates.py -q --tb=short
cd frontend && npm run build
```

Keep any ShadeMap validation keys in `.env.test.local` for tests. Do not source
the runtime `.env` or production service env for pytest.

For production deployments, also check:

- `/api/shademap/config` returns only expected public configuration.
- The returned `api_key` is the browser-safe key, not a private server-only
  secret.
- Terrain URLs are same-origin and include expiring signed tokens.
- Your nginx config keeps the ShadeMap feature and terrain locations.
- Your deployment has a current ShadeMap plan/API access from
  [https://shademap.app/about](https://shademap.app/about).
