"""Deterministic Parquet output.

Idempotency requirement from the brief: running ingestion twice must produce no
change. That needs more than stable row order -- Parquet embeds metadata, and
pandas writes an index by default. The settings below pin everything we can.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Row groups must be large enough that the whole table lands in one, otherwise
# a differently-sized input silently changes the file layout.
ROW_GROUP_SIZE = 1_000_000


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Write a frame reproducibly.

    ``store_schema=False`` drops the pandas metadata blob, which embeds
    library versions and would make otherwise-identical runs differ.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    table = table.replace_schema_metadata(None)
    pq.write_table(
        table,
        path,
        compression="zstd",
        compression_level=9,
        row_group_size=ROW_GROUP_SIZE,
        write_statistics=True,
        store_schema=False,
        version="2.6",
    )


def read_parquet(path: Path) -> pd.DataFrame:
    return pq.read_table(path).to_pandas()


def write_json(payload: Any, path: Path) -> None:
    """Stable JSON: sorted keys, fixed separators, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
