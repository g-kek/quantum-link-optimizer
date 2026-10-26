"""Strict row boundaries shared by the synthetic and external CSV readers."""

import csv


def strict_rows(stream, columns):
    reader = csv.DictReader(stream)
    if tuple(reader.fieldnames or ()) != columns:
        raise ValueError(f"Required CSV columns: {columns}")
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(
                f"CSV row {reader.line_num}: expected exactly {len(columns)} fields"
            )
        yield row
