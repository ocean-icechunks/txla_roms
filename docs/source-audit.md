# TXLA history archive: source audit (2026-11-18)

Read-only audit of `https://hriai01.tamucc.edu/data/MASTR_MODELS/txla/history/`:
directory listings, HTTP headers, file headers and time axes only. No data
variables were read and no checksums were taken.

## Inventory

|                   |                                                                                                                   |
| ----------------- | ----------------------------------------------------------------------------------------------------------------- |
| Files             | 69 `txla2_his_f_latest_z.<YYYY-MM-DD-HHMMSS>.nc` in `2026/08` … `2026/11`; no other files                         |
| Bytes             | 662,442,578,808 (66 × 9,827,565,328 + three short files)                                                          |
| Format            | NetCDF-3 classic (`CDF\x01` magic), uncompressed, record variables interleaved                                    |
| Content           | ROMS 3.7 forecast interpolated to 28 z-levels; 670 × 190 curvilinear grid; 96 hourly records per file             |
| Schema            | identical variables, dimensions, depths and global attributes in all 69 files; Δt always 3600 s                   |
| Version attribute | none (globals: title, institution, history, author, source, type, grid_type)                                      |
| Server            | Apache 2.4.52, byte ranges (HTTP 206) and ETags; no CORS header                                                   |
| Growth            | about one 9.8 GB file per day (about 3.6 TB/year); a run appears in `history/` the night after it is in `latest/` |

## Findings

1. **Short files are complete, not truncated.** File size = 2,041,360 B of fixed
   data + n × 102,349,208 B records, exactly, for all 69 files. Short runs:
   `2026-08-28-213243` (42 records), `2026-09-05-003957` (92),
   `2026-08-30-114753` (1 record, 100 MB).
2. **Two forecast start days have two files each**: 08-30 (the 1-record file and
   a full run) and 09-01 (`2026-09-02-001301` and `2026-09-02-215634`, identical
   time ranges).
3. **19 start days have no file** (08-31, 09-03, 09-05, 09-11, 09-13, 09-17,
   09-21, 09-29, 10-01, 10-10, 10-13, 10-19, 10-26, 10-27, 10-30, 11-01, 11-05,
   11-09, 11-15). No gap between runs is longer than 72 h, so the valid-time
   line from 08-23 01:00 to 11-20 00:00 is fully covered by longer leads.
4. **The filename timestamp is the archive time (UTC), not the forecast start.**
   It is about 15 minutes after the `history` creation time, which is in US
   Central time. It is often the day after the forecast start, and the month
   folders follow it. All archived names still contain `_latest_`.
5. **Empty folders**: 2025, 2026/01–07, 2026/12 and 2027–2030 exist with no
   files.
6. **Metadata errors**: `sustr`/`svstr` units `N s-2`; `O2` long_name "Bottom
   oxygen" on a 3-D field; `coordinates` attributes name a nonexistent `depth`;
   non-CF standard_names on `sustr`, `svstr`, `O2`, `pH`, `omega`; `pH`/`omega`
   units `no unit ` and `" "`. Repaired in the virtual store's metadata only
   (README).
7. **Neighbouring products, not audited**:
   `MASTR_MODELS/{hycom,mercator,rtofs,tops_ias32,twdb_baycast}/`, plus
   `txla/latest/` and `txla/last_modified.txt`.

## Questions for TAMU

- Which of the two 09-01 runs is authoritative? Should the 1-record 08-30 file
  be in the archive?
- Are the 19 missing start days lost, or can they be backfilled? Would a
  backfill arrive as new files?
- Is `history/` kept indefinitely, or trimmed? Are files ever rewritten in
  place?
- `history/2026/11/` showed a modification at 02:17 CST on 11-18 with no new
  file. What changed?
- Are the empty 2027–2030 folders intentional?
- What request load is acceptable from readers of the virtual store?
- Are the metadata errors known, and would a fix rewrite the files in place
  (changing their bytes)?
