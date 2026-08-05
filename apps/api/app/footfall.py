"""Footfall CSV validation — schema: zone_name, timestamp, traffic_count."""

import csv
import io
from datetime import datetime

REQUIRED = ["zone_name", "timestamp", "traffic_count"]
MAX_ROWS = 100_000


def validate(data: bytes) -> tuple[list[str], int]:
    """Returns (errors, valid_row_count). Empty errors == valid file."""
    errors: list[str] = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ["File is not UTF-8 text — export the CSV again from your spreadsheet."], 0

    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        return [f"Missing required column(s): {', '.join(missing)}. "
                f"Expected header: {', '.join(REQUIRED)}."], 0

    rows = 0
    for i, row in enumerate(reader, start=2):  # header is line 1
        if rows >= MAX_ROWS:
            errors.append(f"File exceeds {MAX_ROWS} rows — split it or aggregate.")
            break
        if len(errors) >= 20:
            errors.append("More errors omitted — fix the above and re-upload.")
            break
        if not (row.get("zone_name") or "").strip():
            errors.append(f"Line {i}: zone_name is empty.")
            continue
        ts = (row.get("timestamp") or "").strip()
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(ts, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            errors.append(f'Line {i}: timestamp "{ts}" not understood — use YYYY-MM-DD HH:MM.')
            continue
        count = (row.get("traffic_count") or "").strip()
        if not count.isdigit():
            errors.append(f'Line {i}: traffic_count "{count}" must be a whole number ≥ 0.')
            continue
        rows += 1

    if rows == 0 and not errors:
        errors.append("File has a valid header but no data rows.")
    return errors, rows
