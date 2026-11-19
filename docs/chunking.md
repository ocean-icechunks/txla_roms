# Layout and chunking

Why the store looks the way it does. Nothing here can be changed without
rewriting the source data, which this project does not do: a virtual store
preserves the source's chunk layout exactly.

## Source layout

Each file is one forecast run: up to 96 hourly records starting at 01:00 UTC,
NetCDF-3 classic, uncompressed, big-endian, record variables interleaved. In
NetCDF-3 every record of every variable is one contiguous byte range, so the
natural (and only) virtual chunk is **one variable at one hour**:

| Variable kind                        | Chunk shape        | Bytes   |
| ------------------------------------ | ------------------ | ------- |
| 3-D (`u v temp salt O2 pH omega`)    | 1 × 28 × 190 × 670 | 14.3 MB |
| 2-D (`zeta air_u air_v sustr svstr`) | 1 × 190 × 670      | 0.5 MB  |

Any time layout is chunk-aligned, which is what makes the two groups possible:
`/forecast` and `/best` are two arrangements of the same byte ranges. Missing
leads of short runs and hours no run covers have no chunk and read as the fill
value (NaN).

Coordinates (`Depth`, `lat`, `lon`, the time coordinates) are materialized, each
as **one chunk**, so opening a group reads a handful of small objects instead of
one per time step.

## Performance

Measured 2026-11-18 from a JupyterHub, local repository, `async.concurrency` =
32:

| Operation                               | Time                                       |
| --------------------------------------- | ------------------------------------------ |
| Open repository + group (metadata only) | 0.5 s                                      |
| One 3-D field, one hour (14.3 MB)       | about 1 s                                  |
| `zeta` at one point, 144 hours          | 2.4 s                                      |
| Surface `temp` at one point, 24 hours   | 2.5 s (fetches 24 full 3-D fields, 340 MB) |

**Point time series of 3-D variables are expensive**: one point over the full
record of `temp` fetches every 14.3 MB field, ≈ 30 GB. Maps and short windows
are cheap. Keep concurrency modest: all data reads go to one university web
server.

## Metadata repairs

Only metadata is changed; the bytes are untouched. Applied in `build.py`
(`ATTR_REPAIRS`):

| Variable         | Source                                                                    | In the store                                                                  | Why                                       |
| ---------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------- |
| `sustr`, `svstr` | units `N s-2`, standard_name `eastward_stress`/`northward_stress`         | `N m-2`, `surface_downward_eastward_stress`/`…northward…`                     | wrong unit; not CF names                  |
| `O2`             | long_name "Bottom oxygen concentration", standard_name `dissolved_oxygen` | "Dissolved oxygen concentration", `mass_concentration_of_oxygen_in_sea_water` | field is 3-D; not a CF name               |
| `pH`             | standard_name `potential_hydrogen`, units `no unit `                      | removed, `1`                                                                  | not CF; the pH scale is not stated        |
| `omega`          | standard_name `carbonate_saturation_aragonite`, units `" "`               | removed, `1`                                                                  | not CF                                    |
| `Depth`          |                                                                           | `axis: Z`                                                                     |                                           |
| all              | `_CoordinateAxes`, `field`, `time`, `coordinates`                         | removed                                                                       | `coordinates` named a nonexistent `depth` |
| global           | `history`, `type`                                                         | removed                                                                       | per-file; `type` claimed NETCDF3_CLASSIC  |

Standard names were checked against CF standard name table v95. Changing any of
this means bumping `LAYOUT_VERSION` so the next update rebuilds.
