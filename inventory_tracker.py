from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_now():
  now = datetime.now(IST)
  return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S IST")


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
  movement = (movement_type or "").strip().upper()
  if movement not in ("SOLD", "PURCHASED"):
    movement = "SOLD" if float(qty_change) < 0 else "PURCHASED"

  resolved_code = (item_code or "").strip()
  resolved_unit = (unit or "").strip()

  if product_id and (not resolved_code or not resolved_unit):
    try:
      cursor.execute(
          "SELECT sku_code, unit, pack_size FROM products WHERE id = ?",
          (product_id,),
      )
      p_info = cursor.fetchone()
      if p_info:
        if not resolved_code:
          resolved_code = (p_info[0] or f"SG-{int(product_id):04d}").strip()
        if not resolved_unit:
          u_str = (p_info[1] or "pcs").strip()
          p_str = (p_info[2] or "").strip()
          resolved_unit = (
              f"{p_str} ({u_str})"
              if (p_str and p_str.lower() != u_str.lower())
              else u_str
          )
    except sqlite3.OperationalError:
      if not resolved_code and product_id:
        resolved_code = f"SG-{int(product_id):04d}"

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
          product_id,
          (barcode or "").strip(),
          (product_name or "Product").strip(),
          (category or "Essentials").strip(),
          movement,
          float(qty_change),
          float(stock_after) if stock_after is not None else None,
          (source or "Server").strip(),
          str(reference_id or ""),
          track_date,
          ist_ts,
          resolved_code,
          resolved_unit,
      ),
  )
  conn.commit()