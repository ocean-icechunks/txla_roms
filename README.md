# TXLA ROMS Forecast, Icechunked

The [TXLA ROMS][txla] forecast archive (Texas–Louisiana shelf; Physical
Oceanography Numerical Group, Texas A&M) as a **virtual** [Icechunk][icechunk]
store, kept up to date by GitHub Actions.

The store holds only Zarr metadata and byte-range references (about 5 MB). **The
science data stays in the original NetCDF-3 files on TAMU-CC's web server**
(`https://hriai01.tamucc.edu/data/MASTR_MODELS/txla/history/`), so every data
read depends on that server being up and on its files not being moved or
rewritten. We parse each file's byte layout with [VirtualiZarr][vz] and publish
those references; nothing is copied.

## Quick start

```python
from txla_roms_icechunk.load import open_txla  # standalone: copy src/txla_roms_icechunk/load.py anywhere

# a local build (see "Maintaining this store")
best = open_txla("best", repo_path="local-store")

# the published store, once the daily workflow is configured (anonymous, no credentials)
best = open_txla(
    "best", s3_bucket="ocean-icechunks", s3_prefix="txla_roms.icechunk", s3_endpoint="https://data.source.coop"
)

sst = best.temp.isel(Depth=0).sel(time="2026-11-01T12:00").load()
fc = open_txla("forecast", repo_path="local-store")
run = fc.zeta.sel(forecast_reference_time="2026-11-10")  # one 96-hour run
```

Without the helper, authorize the source prefix yourself. **Source and
destination access are two separate steps**: the repository needs (anonymous) S3
or local access, the data bytes need anonymous HTTPS to TAMU-CC.

```python
import icechunk as ic
import xarray as xr

storage = ic.local_filesystem_storage("local-store")  # or ic.s3_storage(..., anonymous=True)
prefixes = ic.Repository.fetch_config(storage).virtual_chunk_containers.keys()
repo = ic.Repository.open(
    storage,
    authorize_virtual_chunk_access={p: ic.credentials.HttpAccess for p in prefixes},
)
store = repo.readonly_session("main").store
best = xr.open_zarr(store, group="best", consolidated=False, zarr_format=3, chunks={})
```

## What's in here

| Group       | Dimensions                                                          | Use it for                                                                                                            |
| ----------- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `forecast/` | `forecast_reference_time` × `lead` (1–96 h) × [`Depth`] × `Y` × `X` | comparing runs, forecast skill by lead. `valid_time(forecast_reference_time, lead)`.                                  |
| `best/`     | `time` (hourly) × [`Depth`] × `Y` × `X`                             | one continuous series; each hour from the **newest run** covering it (`forecast_reference_time(time)`, `lead(time)`). |

|           |                                                                                                     |
| --------- | --------------------------------------------------------------------------------------------------- |
| Source    | 70 NetCDF-3 classic files, ~9.8 GB each (≈ 0.69 TB), listed 2026-11-19                              |
| Coverage  | runs 2026-08-23 → 2026-11-17; `/best` 2026-08-23 01:00 → 2026-11-21 00:00 UTC (as built 2026-11-19) |
| Grid      | 670 × 190 curvilinear (`lon`, `lat` 2-D), 28 fixed depths 1–1000 m                                  |
| Variables | 2-D `zeta air_u air_v sustr svstr`; 3-D `u v temp salt O2 pH omega` (float32)                       |

Two groups rather than one because the layouts answer different questions and a
user should not have to assemble the best series themselves. **This is
deliberate; don't merge them.**

- `forecast_reference_time` is the first record minus 1 h (always 00:00 UTC),
  **not** the date in the filename, which is when the file was archived.
- Short runs are kept; their missing leads read as NaN.
- Some start days have no run; `/best` still has no gaps (no gap between runs
  exceeds 72 h), so it uses longer leads there. Check `lead` if that matters.
- When two files share a forecast start, the **later-archived file wins** (owner
  decision, 2026-11-18). The excluded files are listed in each group's
  `excluded_source_files` attribute and in
  [docs/source_manifest.csv](docs/source_manifest.csv).

Metadata repairs (units, CF standard names, dropped per-file attributes) are
listed in each group's `metadata_repairs` attribute and in
[docs/chunking.md](docs/chunking.md#metadata-repairs). Values are untouched.

## Update cadence

A GitHub Actions job ([`daily.yml`](.github/workflows/daily.yml)) runs twice a
day. It lists the archive and compares it with the fingerprint recorded on
`main`; if anything changed it rebuilds both groups in **one snapshot**,
validates against the source, and tags `data-through-<last valid day>`. An
unchanged listing is a no-op. A full rebuild is the update method on purpose:
each new run changes the last three days of `/best`.

## Things to know

- **Reading data reads TAMU-CC's server.** Keep `async.concurrency` modest (the
  loader uses 32): all data reads go to one university web server.
- **Point time series of 3-D variables are expensive** and cannot be made
  cheaper without rewriting the data: every chunk is one variable at one hour
  (14.3 MB for a 3-D field), so one point over the whole record of `temp`
  fetches ≈ 30 GB. Maps and short windows are cheap. See
  [docs/chunking.md](docs/chunking.md).
- **Earlier builds stay readable** by snapshot ID or `data-through-*` tag
  (`open_txla(..., tag="data-through-2026-11-21")`).
- **Browser access is not verified**: the source server sends no
  `Access-Control-Allow-Origin`, so browser readers will see coordinates but no
  data.
- **URL stability, rewrites and retention** of `history/` files are not
  confirmed with TAMU-CC. See [docs/source-audit.md](docs/source-audit.md).
- **Library versions matter.** Tested with Python 3.13, icechunk 2.3.1,
  virtualizarr 2.8.0, xarray 2026.9.0, zarr 3.4.2, netCDF4 1.7.5.

## Citation

Cite the TXLA model and its operators, not this repository. This is a
repackaging, not a new product.

## Maintaining this store

See [docs/operations.md](docs/operations.md) for the runbook and
[docs/chunking.md](docs/chunking.md) for the layout and performance.

```bash
pixi run txla --store-path local-store update --create   # first build (~3 min)
pixi run txla --store-path local-store update            # what the scheduled Action runs
pixi run txla --store-path local-store status            # --format markdown for GitHub
pixi run txla --store-path local-store expire --days 35 --dry-run
```

`notebooks/smoke_test.ipynb` is the small end-to-end test (a few files,
temporary repository, deleted afterwards);
`notebooks/plot_surface_temperature.ipynb` is an example analysis.

## License

Apache License 2.0, see [LICENSE](LICENSE).

[txla]: https://pong.tamu.edu/
[icechunk]: https://icechunk.io
[vz]: https://virtualizarr.readthedocs.io
