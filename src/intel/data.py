"""Read tables from a pull as pandas DataFrames."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


def load_table(pull_dir: Path, table: str, columns: list[str] | None = None) -> pd.DataFrame:
    df: pd.DataFrame = pq.read_table(pull_dir / f"{table}.parquet", columns=columns).to_pandas()
    return df
