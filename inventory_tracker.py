from datetime import datetime
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
          ist_timestamp TEXT NOT NULL
      )
  """)
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
):
  ensure_inventory_track_table(cursor, conn)
  track_date, ist_ts = ist_now()
  movement = movement_type.strip().upper()
  if movement not in ("SOLD", "PURCHASED"):
    movement = "SOLD" if float(qty_change) < 0 else "PURCHASED"

  cursor.execute(
      """
      INSERT INTO inventory_tracks (
          product_id, barcode, product_name, category,
          movement_type, qty_change, stock_after,
          source, reference_id, track_date, ist_timestamp
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
      ),
  )
  conn.commit()