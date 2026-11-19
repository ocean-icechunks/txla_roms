# Operations runbook

How the TXLA ROMS forecast (TAMU-CC) store is kept up to date, and what to do
when something needs a human. For why the store is shaped the way it is, see
[chunking.md](chunking.md).

## The one idea that makes everything else simple

`txla update` is _list the source archive → compare with what `main` was built
from → rebuild only if something changed_. The listing (every matching file,
with the server's modification time and size) plus the code's `LAYOUT_VERSION`
is hashed into a fingerprint that every build records on the root group of the
store. This means:

- **Any run can be killed and re-run.** Commits are atomic; an interrupted run
  leaves a valid store, and the next run rebuilds from the listing.
- **A failed or skipped scheduled run needs no retry policy**: the next run does
  the same comparison and catches up.
- **Re-running when nothing changed is a no-op**: "Nothing to do.", no commit,
  no new snapshot.
- **Changing the layout in code** means bumping `LAYOUT_VERSION` in `config.py`;
  the next scheduled run rebuilds. `update --force` rebuilds regardless.

Each build re-reads the whole archive's references and replaces both groups.
Earlier snapshots stay readable by ID or by their
`data-through-<last valid day>` tag.

|             |                                                                           |
| ----------- | ------------------------------------------------------------------------- |
| First build | about 3 minutes (70 file headers, four at a time, then three spot checks) |
| Refresh     | about 3 minutes when the listing changed; under 10 s when it did not      |
| Memory      | The build peaks well under 2 GB.                                          |

## Storage targets

Every command takes exactly one storage target, as CLI options or env vars:

| Option          | Env var            | Target                                                                   |
| --------------- | ------------------ | ------------------------------------------------------------------------ |
| `--store-path`  | `TXLA_STORE_PATH`  | Local Icechunk store directory                                           |
| `--s3-bucket`   | `TXLA_S3_BUCKET`   | S3 bucket (with the options below); Actions default `ocean-icechunks`    |
| `--s3-prefix`   | `TXLA_S3_PREFIX`   | Key prefix, default `txla-roms/txla_roms.icechunk`                       |
| `--s3-region`   | `TXLA_S3_REGION`   | Bucket region, default `us-west-2`                                       |
| `--s3-endpoint` | `TXLA_S3_ENDPOINT` | Custom endpoint (e.g. Source.coop)                                       |
| `--s3-acl`      | `TXLA_S3_ACL`      | Canned ACL on writes, default bucket-owner-full-control; `none` disables |

Flags for local work, env vars in Actions. Only `update --create` may create a
store, so a typo'd target fails instead of silently becoming a new, empty store.

## Routine operation (automated)

[`daily.yml`](../.github/workflows/daily.yml) runs nightly at 02:00 UTC (plus
`workflow_dispatch`, with optional `force` and `create` inputs) and executes:

```bash
aws s3 ls s3://ocean-icechunks/txla-roms/ --endpoint-url https://data.source.coop  # sign-in check
pixi run txla update
pixi run txla status --format markdown
```

`update` validates every new build from the consumer read path (three random
(run, lead, variable) fields compared with netCDF4 reads of the source, the runs
in the store against the runs selected from the listing, and the newest `/best`
hour against the newest run) and tags it `data-through-<last valid day>`.

One writer, always: the workflow uses
`concurrency: {group: txla-store, cancel-in-progress: false}`, and so does
`maintenance.yml`. There is no commit-conflict handling in the code; do not run
a second writer against the production store while a workflow run is active.

**Destination.** The store is
`s3://ocean-icechunks/txla-roms/txla_roms.icechunk` on Source Cooperative
(product `ocean-icechunks/txla-roms`), written through the data proxy
`https://data.source.coop`. The workflow signs in as the Source Cooperative
service account **`ocean-icechunks--txla-roms-update-store`**
(`arn:aws:iam::ocean-icechunks--txla-roms-update-store:role/FullAccess`) with
GitHub OIDC; no secret is stored. The trust is for this repository's `main`
branch, so:

- the schedule works only while `main` is the default branch;
- the jobs set no `environment:` (that would change the OIDC subject claim);
- both workflows run only in `ocean-icechunks/txla_roms` (forks and copies
  skip).

Repository variables `TXLA_S3_BUCKET`, `TXLA_S3_PREFIX`, `TXLA_S3_REGION`,
`TXLA_S3_ENDPOINT` and `TXLA_S3_ACL` override the defaults; none is required.

**First run.** The prefix starts empty, and `update` refuses to create a store
unless asked. Run the workflow once by hand from the Actions tab with **create**
ticked (or `gh workflow run daily.yml -f create=true`). Until then the nightly
run fails with "No Icechunk repository at ...", which is harmless. Optionally
set `TXLA_STATUS_ISSUE` to an issue number whose body the workflow rewrites with
the status after every run.

**Status reporting.** Every run ends with `txla status --format markdown`,
written to the run's step summary, and (if `TXLA_STATUS_ISSUE` is set) to a
pinned issue by the `report` job. The `report` job also re-enables `daily.yml`,
so GitHub's 60-day inactivity rule never disables the schedule.

## Credentials

The destination and the source are two independent configurations:

- **Destination (write)**: the workflow signs in to Source Cooperative with OIDC
  as the service account and gets short-lived AWS credentials
  (`role-duration-seconds` outlives the job's `timeout-minutes`). Icechunk reads
  them from the environment once and does not renew them mid-run, so a job must
  not outlive its credentials. No secret is stored in the repository.
- **Source (read)**: anonymous HTTPS to TAMU-CC. The repository config registers
  the single virtual chunk container
  `https://hriai01.tamucc.edu/data/MASTR_MODELS/.../history/` and is saved with
  `repo.save_config()`, so readers inherit it; every reader must still authorize
  the prefix explicitly (`HttpAccess`).

**Unverified**: that TAMU-CC's server accepts the request volume from
GitHub-hosted runners (their IP ranges change). If listing or header reads start
failing only in Actions, ask TAMU-CC, or run the update from a self-hosted
runner.

## Failure modes

- **A file whose time axis is not hourly from T01:00** stops the build with the
  file name. Check the file on the server; if it is genuinely broken, ask the
  owner whether to exclude it.
- **A changed variable set, depth levels or encoding** stops the build
  (`check_compatible`). Files must not be combined across such a change; it
  needs a layout decision.
- **Validation failed after the commit**: `main` already points at the new
  snapshot, untagged. Fix the cause and run `txla update --validate-only` to
  re-validate and tag it, or reset `main` to the previous tag
  (`repo.reset_branch("main", repo.lookup_tag("data-through-..."))`).
- **The store is missing** ("No Icechunk repository at ..."): wrong target
  variables, or the store was never created. Commands never create one without
  `--create`.

## Snapshot expiry

Every changed build adds a snapshot. `maintenance.yml` (manual dispatch only,
dry-run by default) runs `txla expire --days N`: expire snapshots older than N
days, then garbage-collect. Tags are not deleted. GC is the one destructive
operation here: dry-run it, and check it on a throwaway copy of the store before
the first real run. Which tags to keep long-term is an owner decision that has
not been made.

## Local development

```bash
uv sync                      # or: pixi install
uv run pytest                # offline tests
uv run pytest -m network     # end-to-end against TAMU-CC
uvx nox -s lint              # pre-commit hooks via prek
pixi run txla --store-path local-store update --create
```
