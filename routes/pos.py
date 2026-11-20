from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

try:
  from inventory_tracker import ensure_inventory_track_table, record_inventory_track
except ImportError:
  IST = ZoneInfo("Asia/Kolkata")

  def _ist_now():
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
    track_date, ist_ts = _ist_now()
    movement = (movement_type or "").strip().upper()
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

pos_bp = Blueprint("pos", __name__)
pos_cart = []


# --- ADMIN AUTHENTICATION DECORATOR ---
def admin_required(f):
  from functools import wraps

  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not session.get("is_admin"):
      return redirect(url_for("admin.admin_login"))
    return f(*args, **kwargs)

  return decorated_function


@pos_bp.route("/pos")
@admin_required
def pos_terminal():
  conn = sqlite3.connect("store_pos.db")
  cursor = conn.cursor()
  cursor.execute("SELECT * FROM products")
  products = cursor.fetchall()
  conn.close()

  # Tax-inclusive subtotal (sum of catalog prices)
  subtotal = sum(item["price"] * item["qty"] for item in pos_cart)

  # Backward calculation for tax-inclusive embedded GST
  gst_total = 0.0
  for item in pos_cart:
    item_gross = item["price"] * item["qty"]
    gst_rate = float(item["gst"])
    taxable_base = item_gross / (1.0 + (gst_rate / 100.0))
    gst_total += (item_gross - taxable_base)

  cgst_total = gst_total / 2
  sgst_total = gst_total / 2
  grand_total = subtotal

  return render_template(
      "pos.html",
      products=products,
      cart=pos_cart,
      subtotal=subtotal,
      cgst_total=cgst_total,
      sgst_total=sgst_total,
      gst_total=gst_total,
      grand_total=grand_total,
  )


@pos_bp.route("/add_to_cart", methods=["POST"])
@admin_required
def add_to_cart():
  product_id = request.form.get("product_id")
  barcode_scan = request.form.get("barcode_scan")

  conn = sqlite3.connect("store_pos.db")
  cursor = conn.cursor()

  if barcode_scan:
    cursor.execute(
        "SELECT id, name, price, stock, gst_rate FROM products WHERE barcode ="
        " ?",
        (barcode_scan.strip(),),
    )
    prod = cursor.fetchone()
  elif product_id:
    cursor.execute(
        "SELECT id, name, price, stock, gst_rate FROM products WHERE id = ?",
        (product_id,),
    )
    prod = cursor.fetchone()
  else:
    prod = None

  conn.close()

  if prod:
    p_id, name, price, stock, gst = prod
    found = False
    for item in pos_cart:
      if item["id"] == p_id:
        if item["qty"] < stock:
          item["qty"] += 1
        else:
          flash("Stock limit reached!")
        found = True
        break
    if not found:
      if stock > 0:
        pos_cart.append(
            {"id": p_id, "name": name, "price": price, "qty": 1, "gst": gst}
        )
      else:
        flash("Product out of stock!")
  elif barcode_scan:
    flash(f"Product with barcode '{barcode_scan}' not found!")

  return redirect(url_for("pos.pos_terminal"))


@pos_bp.route("/update_cart/<int:index>/<action>")
@admin_required
def update_cart(index, action):
  if 0 <= index < len(pos_cart):
    if action == "increase":
      pos_cart[index]["qty"] += 1
    elif action == "decrease":
      pos_cart[index]["qty"] -= 1
      if pos_cart[index]["qty"] <= 0:
        pos_cart.pop(index)
    elif action == "remove":
      pos_cart.pop(index)
  return redirect(url_for("pos.pos_terminal"))


@pos_bp.route("/checkout", methods=["POST"])
@admin_required
def checkout():
  if not pos_cart:
    flash("Cart is empty!")
    return redirect(url_for("pos.pos_terminal"))

  customer_name = request.form.get("customer_name", "Walking Customer")
  customer_phone = request.form.get("customer_phone", "")
  payment_mode = request.form.get("payment_mode", "Cash")

  disc_raw = request.form.get("discount", "").strip()
  disc_pct_raw = request.form.get("discount_percent", "").strip()
  cash_raw = request.form.get("cash_tendered", "").strip()

  discount_fixed = float(disc_raw) if disc_raw else 0.0
  discount_percent = float(disc_pct_raw) if disc_pct_raw else 0.0
  cash_tendered = float(cash_raw) if cash_raw else 0.0

  # Gross subtotal (catalog prices include GST)
  gross_subtotal = sum(item["price"] * item["qty"] for item in pos_cart)

  if discount_percent > 0:
    calculated_discount = (gross_subtotal * discount_percent) / 100.0
  else:
    calculated_discount = discount_fixed

  # Final payable grand total after discount
  grand_total = max(0.0, gross_subtotal - calculated_discount)

  # Tax-inclusive backward calculation per item
  total_taxable_amount = 0.0
  total_gst_amount = 0.0
  item_breakdowns = []

  for item in pos_cart:
    item_gross = item["price"] * item["qty"]
    # Proportionally distribute discount if any
    item_net_gross = item_gross * (grand_total / gross_subtotal) if gross_subtotal > 0 else 0.0

    gst_rate = float(item["gst"])
    taxable_base = item_net_gross / (1.0 + (gst_rate / 100.0))
    gst_amt = item_net_gross - taxable_base

    total_taxable_amount += taxable_base
    total_gst_amount += gst_amt

    item_breakdowns.append({
        "id": item["id"],
        "name": item["name"],
        "price": item["price"],
        "qty": item["qty"],
        "gst": gst_rate,
        "total": item_gross
    })

  conn = sqlite3.connect("store_pos.db")
  cursor = conn.cursor()
  ensure_inventory_track_table(cursor, conn)

  if payment_mode == "Credit" and customer_phone:
    cursor.execute(
        "SELECT credit_limit FROM customers WHERE phone = ?", (customer_phone,)
    )
    c_limit_row = cursor.fetchone()
    credit_limit = c_limit_row[0] if c_limit_row else 2000.0

    cursor.execute(
        "SELECT SUM(total_amount) FROM bills WHERE customer_phone = ? AND"
        " payment_mode = 'Credit' AND khata_status = 'Unpaid'",
        (customer_phone,),
    )
    current_due = cursor.fetchone()[0] or 0.0

    if (current_due + grand_total) > credit_limit:
      conn.close()
      flash(
          f"⚠️ Credit Limit Exceeded! Limit is ₹{credit_limit}, Current Dues:"
          f" ₹{current_due}, This Bill: ₹{grand_total}."
      )
      return redirect(url_for("pos.pos_terminal"))

  change_returned = (
      cash_tendered - grand_total
      if payment_mode == "Cash" and cash_tendered > 0
      else 0.0
  )
  khata_status = "Unpaid" if payment_mode == "Credit" else "Paid"

  items_summary = ", ".join(f"{i['name']} (x{i['qty']})" for i in pos_cart)
  timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

  cursor.execute(
      """
        INSERT INTO bills (customer_name, customer_phone, items_summary, subtotal, discount, gst_amount, total_amount, payment_mode, cash_tendered, change_returned, khata_status, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
      (
          customer_name,
          customer_phone,
          items_summary,
          gross_subtotal,      # Amt before tax & disc
          calculated_discount, # Amount You Saved
          total_gst_amount,    # GST Amount extracted
          grand_total,         # Payable Amt
          payment_mode,
          cash_tendered,
          change_returned,
          khata_status,
          timestamp,
      ),
  )
  bill_id = cursor.lastrowid

  for item in item_breakdowns:
    cursor.execute(
        """
            INSERT INTO bill_items (bill_id, item_name, price, qty, gst_rate, total)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            bill_id,
            item["name"],
            item["price"],
            item["qty"],
            item["gst"],
            item["total"],
        ),
    )

    p_barcode = ""
    p_category = "Essentials"
    prev_stock = 0.0
    try:
      cursor.execute(
          "SELECT barcode, category, stock FROM products WHERE id = ?",
          (item["id"],),
      )
      p_row = cursor.fetchone()
      if p_row:
        p_barcode = p_row[0] or ""
        p_category = p_row[1] or "Essentials"
        prev_stock = float(p_row[2] or 0.0)
    except sqlite3.OperationalError:
      pass

    new_stock = prev_stock - float(item["qty"])
    cursor.execute(
        "UPDATE products SET stock = stock - ? WHERE id = ?",
        (item["qty"], item["id"]),
    )
    record_inventory_track(
        cursor,
        conn,
        product_id=item["id"],
        barcode=p_barcode,
        product_name=item["name"],
        category=p_category,
        movement_type="SOLD",
        qty_change=-abs(float(item["qty"])),
        stock_after=new_stock,
        source="POS Billing",
        reference_id=f"BILL-{bill_id}",
    )

  if customer_phone:
    earned_points = round(grand_total / 100.0, 2)
    cursor.execute(
        """
        INSERT INTO customers (name, phone, loyalty_points) VALUES (?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET loyalty_points = loyalty_points + ?
    """,
        (customer_name, customer_phone, earned_points, earned_points),
    )

  conn.commit()
  conn.close()

  pos_cart.clear()
  return redirect(url_for("pos.view_receipt", bill_id=bill_id))


@pos_bp.route("/receipt/")
@pos_bp.route("/receipt/<int:bill_id>")
@admin_required
def view_receipt(bill_id=None):
  try:
    conn = sqlite3.connect("store_pos.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if bill_id:
      cursor.execute("SELECT * FROM bills WHERE id = ?", (bill_id,))
      raw_bill = cursor.fetchone()
      cursor.execute(
          "SELECT item_name, price, qty, gst_rate, total FROM bill_items WHERE bill_id = ?",
          (bill_id,),
      )
      raw_items = cursor.fetchall()
    else:
      cursor.execute("SELECT * FROM bills ORDER BY id DESC LIMIT 1")
      raw_bill = cursor.fetchone()
      if raw_bill:
        cursor.execute(
            "SELECT item_name, price, qty, gst_rate, total FROM bill_items WHERE bill_id = ?",
            (raw_bill["id"],),
        )
        raw_items = cursor.fetchall()
      else:
        raw_items = []

    # Fetch store header & footer settings safely as a dictionary
    cursor.execute("SELECT * FROM store_settings WHERE id = 1")
    s_row = cursor.fetchone()

    settings = {
        "store_name": "SWARAJ GROCERY",
        "store_address": "Main Road, Katol, Nagpur, Maharashtra",
        "gstin": "27AAAAA0000A1Z5",
        "phone": "9876543210",
        "receipt_header": "Thank you for shopping at Swaraj Grocery!",
        "receipt_footer": "Goods once sold will be replaced within 3 days."
    }
    if s_row:
      try:
        settings["store_name"] = s_row["store_name"] or settings["store_name"]
      except Exception:
        pass
      try:
        settings["store_address"] = s_row["address"] or settings["store_address"]
      except Exception:
        pass
      try:
        settings["gstin"] = s_row["gstin"] or settings["gstin"]
      except Exception:
        pass
      try:
        settings["phone"] = s_row["phone"] or settings["phone"]
      except Exception:
        pass
      try:
        settings["receipt_header"] = s_row["footer_note"] or settings["receipt_header"]
      except Exception:
        pass
      try:
        settings["receipt_footer"] = s_row["return_policy"] or settings["receipt_footer"]
      except Exception:
        pass

    conn.close()

    if not raw_bill:
      return "<h3>No bills found yet. <a href='/pos'>Return to POS</a></h3>", 404

    def safe_float(val):
      if val is None:
        return 0.0
      if isinstance(val, (int, float)):
        return float(val)
      cleaned = str(val).replace("₹", "").replace(",", "").strip()
      try:
        return float(cleaned)
      except ValueError:
        return 0.0

    bill = [
        raw_bill["id"],
        raw_bill["customer_name"] or "Walking Customer",
        raw_bill["customer_phone"] or "",
        raw_bill["items_summary"] or "",
        safe_float(raw_bill["subtotal"]),
        safe_float(raw_bill["discount"]),
        safe_float(raw_bill["gst_amount"]),
        safe_float(raw_bill["total_amount"]),
        raw_bill["payment_mode"] or "Cash",
        safe_float(raw_bill["cash_tendered"]),
        safe_float(raw_bill["change_returned"]),
        raw_bill["khata_status"] or "Paid",
        raw_bill["timestamp"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]

    items = []
    for r_item in raw_items:
      items.append([
          r_item["item_name"],
          safe_float(r_item["price"]),
          int(r_item["qty"] or 1),
          safe_float(r_item["gst_rate"]),
          safe_float(r_item["total"])
      ])

    return render_template("receipt.html", bill=bill, items=items, settings=settings)
  except Exception as e:
    return (
        f"<h3 style='color: red;'>Receipt Error: {str(e)}</h3><p><a"
        " href='/pos'>Return to POS Terminal</a></p>"
    ), 500


@pos_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def bill_settings():
  conn = sqlite3.connect("store_pos.db")
  cursor = conn.cursor()

  if request.method == "POST":
    store_name = request.form.get("store_name")
    address = request.form.get("address")
    gstin = request.form.get("gstin")
    phone = request.form.get("phone")
    footer_note = request.form.get("footer_note")
    return_policy = request.form.get("return_policy")

    cursor.execute("""
            UPDATE store_settings 
            SET store_name = ?, address = ?, gstin = ?, phone = ?, footer_note = ?, return_policy = ?
            WHERE id = 1
        """, (store_name, address, gstin, phone, footer_note, return_policy))
    conn.commit()
    flash("✅ Bill header & footer settings updated successfully!")

  cursor.execute("SELECT * FROM store_settings WHERE id = 1")
  settings = cursor.fetchone()
  conn.close()

  return render_template("settings.html", settings=settings)