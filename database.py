import os
import sqlite3
import firebase_admin
from firebase_admin import credentials, firestore

# --- INITIALIZE FIREBASE (NON-BLOCKING) ---
cred_path = "serviceAccountKey.json"
if os.path.exists(cred_path) and not firebase_admin._apps:
  try:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
  except Exception as e:
    print(f"Firebase Init Notice: {e}")

db = firestore.client() if firebase_admin._apps else None


# Initialize Local SQLite Database for Marg ERP Suite
def init_db():
  conn = sqlite3.connect("store_pos.db")
  cursor = conn.cursor()

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL,
            gst_rate REAL DEFAULT 5.0,
            batch_no TEXT DEFAULT 'GENERAL',
            expiry_date TEXT DEFAULT '2027-12-31',
            rack_location TEXT DEFAULT 'Rack-1 A',
            category TEXT DEFAULT 'Essentials',
            image_url TEXT DEFAULT ''
        )
    """)

  for col, def_val in [
      ("batch_no", "'GENERAL'"),
      ("expiry_date", "'2027-12-31'"),
      ("rack_location", "'Rack-1 A'"),
      ("category", "'Essentials'"),
      ("image_url", "''"),
  ]:
    try:
      cursor.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT DEFAULT {def_val}")
    except Exception:
      pass

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            credit_limit REAL DEFAULT 2000.0,
            loyalty_points REAL DEFAULT 0.0
        )
    """)

  try:
    cursor.execute("ALTER TABLE customers ADD COLUMN loyalty_points REAL DEFAULT 0.0")
  except Exception:
    pass

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT,
            customer_phone TEXT,
            delivery_address TEXT DEFAULT '',
            items_summary TEXT,
            subtotal REAL,
            discount REAL DEFAULT 0.0,
            gst_amount REAL,
            total_amount REAL,
            payment_mode TEXT,
            cash_tendered REAL,
            change_returned REAL,
            khata_status TEXT DEFAULT 'Unpaid',
            order_status TEXT DEFAULT 'Unpacked',
            timestamp TEXT
        )
    """)

  for col, def_type, def_val in [
      ("delivery_address", "TEXT", "''"),
      ("discount", "REAL", "0.0"),
      ("gst_amount", "REAL", "0.0"),
      ("cash_tendered", "REAL", "0.0"),
      ("change_returned", "REAL", "0.0"),
      ("khata_status", "TEXT", "'Unpaid'"),
      ("order_status", "TEXT", "'Unpacked'"),
  ]:
    try:
      cursor.execute(f"ALTER TABLE bills ADD COLUMN {col} {def_type} DEFAULT {def_val}")
    except Exception:
      pass

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS bill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER,
            item_name TEXT,
            price REAL,
            qty INTEGER,
            gst_rate REAL,
            total REAL,
            FOREIGN KEY(bill_id) REFERENCES bills(id)
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_name TEXT,
            invoice_no TEXT,
            items_summary TEXT,
            total_amount REAL,
            timestamp TEXT
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            amount REAL,
            category TEXT,
            timestamp TEXT
        )
    """)

  # --- BILL SETTINGS TABLE (HEADER & FOOTER CUSTOMIZATION) ---
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS store_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT,
            address TEXT,
            gstin TEXT,
            phone TEXT,
            footer_note TEXT,
            return_policy TEXT
        )
    """)
  
  cursor.execute("SELECT COUNT(*) FROM store_settings")
  if cursor.fetchone()[0] == 0:
    cursor.execute("""
        INSERT INTO store_settings (store_name, address, gstin, phone, footer_note, return_policy)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        "SWARAJ GROCERY", 
        "Main Road, Katol, Nagpur, Maharashtra", 
        "27AAAAA0000A1Z5", 
        "9876543210", 
        "Thank you for shopping at Swaraj Grocery!", 
        "Goods once sold will be replaced within 3 days."
    ))

  conn.commit()

  # Always ensure default sample products exist
  default_products = [
      ("890101", "Aashirvaad Atta 5kg", 240.0, 50, 5.0, "BATCH-A1", "2027-06-30", "Rack-1 Shelf A", "Grains & Flour"),
      ("890102", "Tata Salt 1kg", 28.0, 100, 0.0, "BATCH-S2", "2028-01-15", "Rack-2 Shelf B", "Essentials"),
      ("890103", "Fortune Sunflower Oil 1L", 145.0, 40, 5.0, "BATCH-O3", "2027-03-20", "Rack-3 Shelf C", "Oils & Ghee"),
      ("890104", "Sugar 1kg", 44.0, 60, 0.0, "BATCH-SU4", "2028-10-10", "Rack-1 Shelf B", "Essentials"),
      ("890105", "Toor Dal 1kg", 130.0, 30, 0.0, "BATCH-D5", "2027-08-15", "Rack-4 Shelf A", "Pulses & Dal"),
  ]
  cursor.executemany(
      """
          INSERT OR IGNORE INTO products (barcode, name, price, stock, gst_rate, batch_no, expiry_date, rack_location, category)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      default_products,
  )
  conn.commit()
  conn.close()

# Auto-execute database initialization on module startup
init_db()