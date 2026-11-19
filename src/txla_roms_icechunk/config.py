"""Source and store layout constants for the TXLA ROMS virtual Icechunk store."""

from __future__ import annotations

import re

import numpy as np

# --- Source ------------------------------------------------------------------

# TAMU-CC's Apache server. Must end in "/": it is also the virtual chunk container prefix.
SOURCE_PREFIX = "https://hriai01.tamucc.edu/data/MASTR_MODELS/txla/history/"

# One forecast run per file; the timestamp is the UTC time the file was archived.
NAME_RE = re.compile(r"^txla2_his_f_latest_z\.(\d{4}-\d{2}-\d{2}-\d{6})\.nc$")

# Hourly records per full run (leads 1..96 h).
N_LEAD = 96

FILL = np.float32(1e37)

# Parallel header reads against the source. A few only: someone else's server.
DEFAULT_WORKERS = 4

# zarr async.concurrency for validation reads and for the loader.
READ_CONCURRENCY = 32

# --- Store -------------------------------------------------------------------

FORECAST_GROUP = "forecast"
BEST_GROUP = "best"
GROUPS = (FORECAST_GROUP, BEST_GROUP)

# Bump when the layout, encodings or metadata repairs change. It is part of the
# source fingerprint, so the next `txla update` rebuilds even if the source did not change.
LAYOUT_VERSION = 1

# Root-group attributes written with every build.
FINGERPRINT_ATTR = "source_fingerprint"
LAST_UPDATE_ATTR = "last_update_utc"
DATA_THROUGH_ATTR = "data_through"

TIME_ENC = {"units": "seconds since 1970-01-01 00:00:00", "calendar": "gregorian", "dtype": "float64"}
