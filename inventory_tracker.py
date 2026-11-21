from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = "store_pos.db"


def ist_now():
  """Return (track_date_iso, ist_timestamp_24h) in Nagpur / IST (Asia/Kolkata)."""
  now = datetime.now(IST)
  return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S IST")


def ist_display_12h():
  """Return formatted Nagpur / IST timestamp with 12-hour clock and AM/PM."""
  now = datetime.now(IST)
  return now.strftime("%Y-%m-%d %I:%M:%S %p IST")


def _safe_float(val, default=None):
  if val is None:
    return default
  if isinstance(val, (int, float)):
    return float(val)
  cleaned = str(val).strip()
  if not cleaned:
    return default
  try:
    return float(cleaned)
  except ValueError:
    return default


def _extract_field(row, key, idx, default=""):
  if not row:
    return default
  if isinstance(row, sqlite3.Row):
    try:
      val = row[key]
      return default if val is None else val
    except (IndexError, KeyError):
      pass
  if isinstance(row, dict):
    val = row.get(key)
    return default if val is None else val
  try:
    val = row[idx]
    return default if val is None else val
  except (IndexError, TypeError):
    return default


def ensure_inventory_track_table(cursor, conn):
  cursor.execute("""
      CREATE TABLE IF NOT EXISTS inventory_tracks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          product_id INTEGER,
          barcode TEXT DEFAULT '',
          product_name TEXT NOT NULL,
          category TEXT DEFAULT 'Essentials',
          movement_type TEXT NOT NULL,
          qty_change REAL NOT NULL,
          stock_after REAL,
          source TEXT DEFAULT 'Server',
          reference_id TEXT DEFAULT '',
          track_date TEXT NOT NULL,
          ist_timestamp TEXT NOT NULL,
          item_code TEXT DEFAULT '',
          unit TEXT DEFAULT 'pcs'
      )
  """)
  for col_sql in [
      "ALTER TABLE inventory_tracks ADD COLUMN item_code TEXT DEFAULT ''",
      "ALTER TABLE inventory_tracks ADD COLUMN unit TEXT DEFAULT 'pcs'",
  ]:
    try:
      cursor.execute(col_sql)
    except sqlite3.OperationalError:
      pass

  for idx_sql in [
      "CREATE INDEX IF NOT EXISTS idx_tracks_date ON inventory_tracks(track_date)",
      "CREATE INDEX IF NOT EXISTS idx_tracks_type ON inventory_tracks(movement_type)",
      "CREATE INDEX IF NOT EXISTS idx_tracks_product ON inventory_tracks(product_id)",
  ]:
    try:
      cursor.execute(idx_sql)
    except sqlite3.OperationalError:
      pass

  conn.commit()


def record_inventory_track(
    cursor,
    conn,
    *,
    product_id=None,
    barcode="",
    product_name,
    category="Essentials",
    movement_type,
    qty_change,
    stock_after=None,
    source="Server",
    reference_id="",
    item_code="",
    unit="",
):
  ensure_inventory_track_table(cursor, conn)
  track_date, ist_ts = ist_now()

  qty_num = _safe_float(qty_change, 0.0)
  stock_after_num = _safe_float(stock_after, None)

  movement = (movement_type or "").strip().upper()
  if movement not in ("SOLD", "PURCHASED"):
    movement = "SOLD" if qty_num < 0 else "PURCHASED"

  resolved_code = (item_code or "").strip()
  resolved_unit = (unit or "").strip()

  parsed_pid = None
  if product_id is not None and str(product_id).strip() != "":
    try:
      parsed_pid = int(float(str(product_id).strip()))
    except ValueError:
      parsed_pid = None

  if parsed_pid and (not resolved_code or not resolved_unit):
    try:
      cursor.execute(
          "SELECT sku_code, unit, pack_size FROM products WHERE id = ?",
          (parsed_pid,),
      )
      p_info = cursor.fetchone()
      if p_info:
        sku_val = str(_extract_field(p_info, "sku_code", 0, "")).strip()
        unit_val = str(_extract_field(p_info, "unit", 1, "pcs")).strip() or "pcs"
        pack_val = str(_extract_field(p_info, "pack_size", 2, "")).strip()
        if not resolved_code:
          resolved_code = sku_val or f"SG-{parsed_pid:04d}"
        if not resolved_unit:
          resolved_unit = (
              f"{pack_val} ({unit_val})"
              if (pack_val and pack_val.lower() != unit_val.lower())
              else unit_val
          )
    except sqlite3.OperationalError:
      if not resolved_code:
        resolved_code = f"SG-{parsed_pid:04d}"

  if not resolved_code and parsed_pid:
    resolved_code = f"SG-{parsed_pid:04d}"
  if not resolved_unit:
    resolved_unit = "pcs"

  cursor.execute(
      """
      INSERT INTO inventory_tracks (
          product_id, barcode, product_name, category,
          movement_type, qty_change, stock_after,
          source, reference_id, track_date, ist_timestamp,
          item_code, unit
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
          parsed_pid,
          (barcode or "").strip(),
          (product_name or "Product").strip(),
          (category or "Essentials").strip(),
          movement,
          qty_num,
          stock_after_num,
          (source or "Server").strip(),
          str(reference_id or "").strip(),
          track_date,
          ist_ts,
          resolved_code,
          resolved_unit,
      ),
  )
  conn.commit()