from datetime import datetime
from zoneinfo import ZoneInfo
import json
import sqlite3
from flask import Blueprint, Response, flash, redirect, render_template, request, send_from_directory, url_for

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

customer_bp = Blueprint("customer", __name__)


def _ensure_storefront_columns(cursor, conn):
  for col_sql in [
      "ALTER TABLE products ADD COLUMN pack_size TEXT DEFAULT ''",
      "ALTER TABLE products ADD COLUMN mrp REAL DEFAULT 0.0",
      "ALTER TABLE products ADD COLUMN is_online INTEGER DEFAULT 1",
      "ALTER TABLE products ADD COLUMN is_active INTEGER DEFAULT 1",
      "ALTER TABLE products ADD COLUMN is_best_deal INTEGER DEFAULT 0",
      "ALTER TABLE products ADD COLUMN base_product_id INTEGER DEFAULT NULL",
      "ALTER TABLE products ADD COLUMN weight_kg REAL DEFAULT 1.0",
      "ALTER TABLE bills ADD COLUMN delivery_address TEXT DEFAULT ''",
      "ALTER TABLE bills ADD COLUMN order_status TEXT DEFAULT 'Unpacked'",
      "ALTER TABLE bills ADD COLUMN order_source TEXT DEFAULT 'POS'",
  ]:
    try:
      cursor.execute(col_sql)
      conn.commit()
    except sqlite3.OperationalError:
      pass

  try:
    cursor.execute(
        "UPDATE products SET mrp = price WHERE mrp IS NULL OR mrp <= 0"
    )
    cursor.execute(
        "UPDATE products SET is_best_deal = CASE WHEN COALESCE(mrp, 0) > COALESCE(price, 0) THEN 1 ELSE 0 END WHERE is_best_deal IS NULL"
    )
    cursor.execute(
        "UPDATE products SET weight_kg = 1.0 WHERE weight_kg IS NULL OR weight_kg <= 0"
    )
    conn.commit()
  except sqlite3.OperationalError:
    pass


@customer_bp.route("/.well-known/assetlinks.json")
def assetlinks_json():
  payload = """[
    {
      "relation": ["delegate_permission/common.handle_all_urls"],
      "target": {
        "namespace": "android_app",
        "package_name": "com.onrender.swaraj_grocery.twa",
        "sha256_cert_fingerprints": [
          "0F:3F:39:8A:A1:B5:1D:6A:FD:2F:03:7F:C5:24:C4:EC:CF:F1:B5:48:F7:84:F6:47:5E:A9:1C:14:C6:08:C9:5D"
        ]
      }
    }
  ]"""
  return Response(payload, mimetype="application/json")


@customer_bp.route("/sw.js")
def service_worker():
  response = send_from_directory("static", "sw.js", mimetype="application/javascript")
  response.headers["Service-Worker-Allowed"] = "/"
  return response


@customer_bp.route("/privacy")
def privacy_policy():
  html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Privacy Policy - Swaraj Grocery</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 font-sans p-4 md:p-8 text-slate-900">
  <div class="max-w-2xl mx-auto bg-white p-6 md:p-8 rounded-3xl shadow-md border border-slate-200 space-y-4 text-xs leading-relaxed">
    <div class="flex justify-between items-center border-b border-slate-200 pb-4">
      <div>
        <span class="text-[10px] font-extrabold uppercase tracking-wider text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-full">Swaraj Grocery • Katol</span>
        <h1 class="text-xl font-black text-slate-900 mt-1.5">Privacy Policy</h1>
      </div>
      <a href="/" class="bg-slate-100 hover:bg-slate-200 text-slate-800 px-3.5 py-2 rounded-xl font-bold transition">← Back to Store</a>
    </div>
    <p class="text-slate-600"><strong>Swaraj Grocery</strong> respects your privacy. When you place an online grocery order, we collect only the details needed to prepare and deliver your order:</p>
    <ul class="list-disc pl-5 space-y-1.5 text-slate-700">
      <li><strong>Contact & Delivery Details:</strong> Your name, phone/WhatsApp number, area, house number, and locality/town in Katol are used strictly for order confirmation, doorstep delivery, and invoice generation.</li>
      <li><strong>Order & Payment Records:</strong> Selected grocery items, quantities, tax breakdown, and payment mode (Online UPI or Cash on Delivery) are recorded for billing and order fulfillment.</li>
      <li><strong>No Third-Party Ad Selling:</strong> We do not sell or rent your personal contact details to third-party advertisers.</li>
    </ul>
    <p class="text-slate-500 pt-2 border-t border-slate-100">For questions regarding an order or your data, contact Swaraj Grocery in Katol.</p>
  </div>
</body>
</html>"""
  return html


@customer_bp.route("/")
def customer_shop():
  conn = sqlite3.connect("store_pos.db")
  conn.row_factory = sqlite3.Row
  cursor = conn.cursor()
  _ensure_storefront_columns(cursor, conn)

  cursor.execute(
      "SELECT id, barcode, name, pack_size, mrp, price, stock, gst_rate,"
      " batch_no, expiry_date, rack_location, category, image_url,"
      " COALESCE(is_best_deal, 0) AS is_best_deal"
      " FROM products"
      " WHERE stock > 0"
      " AND COALESCE(is_active, 1) = 1"
      " AND COALESCE(is_online, 1) = 1"
      " ORDER BY id ASC"
  )
  products = cursor.fetchall()
  conn.close()

  grouped_map = {}
  grouped_products = []

  for p in products:
    base_name = (p["name"] or "").strip()
    pack_size = (p["pack_size"] or "").strip()
    mrp_val = float(p["mrp"] or p["price"] or 0.0)
    sell_val = float(p["price"] or 0.0)
    savings_val = max(0.0, round(mrp_val - sell_val, 2))
    is_deal = bool(int(p["is_best_deal"] or 0) == 1 or savings_val > 0)
    display_name = f"{base_name} ({pack_size})" if pack_size else base_name

    variant_info = {
        "id": p["id"],
        "name": base_name,
        "display_name": display_name,
        "pack_size": pack_size or "Standard",
        "has_pack": bool(pack_size),
        "mrp": mrp_val,
        "price": sell_val,
        "savings": savings_val,
        "is_best_deal": is_deal,
        "stock": int(p["stock"] or 0),
        "gst": float(p["gst_rate"] if p["gst_rate"] is not None else 5.0),
        "category": p["category"] or "Essentials",
        "image_url": p["image_url"] or "",
    }

    group_key = base_name.lower()
    if group_key not in grouped_map:
      group_entry = {
          "name": base_name,
          "category": variant_info["category"],
          "image_url": variant_info["image_url"],
          "is_best_deal": is_deal,
          "variants": [variant_info],
      }
      grouped_map[group_key] = group_entry
      grouped_products.append(group_entry)
    else:
      grouped_map[group_key]["variants"].append(variant_info)
      if is_deal:
        grouped_map[group_key]["is_best_deal"] = True
      if not grouped_map[group_key]["image_url"] and variant_info["image_url"]:
        grouped_map[group_key]["image_url"] = variant_info["image_url"]

  return render_template(
      "customer_shop.html",
      products=products,
      grouped_products=grouped_products,
  )


@customer_bp.route("/online_checkout", methods=["POST"])
def online_checkout():
  try:
    customer_name = (request.form.get("customer_name") or "Online Customer").strip()
    customer_phone = (request.form.get("customer_phone") or "").strip()

    area = (request.form.get("area") or "").strip()
    house_no = (request.form.get("house_no") or "").strip()
    locality_town = (request.form.get("locality_town") or "Katol").strip()

    address_parts = [p for p in [house_no, area, locality_town] if p]
    delivery_address = ", ".join(address_parts)

    payment_mode = request.form.get("payment_mode", "Online UPI")
    cart_data_json = request.form.get("cart_data", "{}")

    try:
      cart_items = json.loads(cart_data_json)
    except Exception:
      cart_items = {}

    if not cart_items:
      flash("Your online cart is empty!")
      return redirect(url_for("customer.customer_shop"))

    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    _ensure_storefront_columns(cursor, conn)
    ensure_inventory_track_table(cursor, conn)

    subtotal = 0.0
    gst_total = 0.0
    summary_parts = []
    processed_cart = []
    track_events = []
    synced_bases = set()

    for p_id_str, item in cart_items.items():
      try:
        p_id = int(p_id_str)
      except ValueError:
        continue
      qty = max(1, int(item.get("qty", 1)))

      cursor.execute(
          "SELECT id, barcode, name, pack_size, category, price, stock, gst_rate, base_product_id, COALESCE(weight_kg, 1.0)"
          " FROM products WHERE id = ?",
          (p_id,),
      )
      prod = cursor.fetchone()
      if prod:
        _, p_barcode, raw_name, pack_size, p_category, price, stock, gst_rate, base_prod_id, weight_kg = prod
        pack_str = (pack_size or "").strip()
        name = f"{raw_name} ({pack_str})" if pack_str and f"({pack_str})" not in raw_name else raw_name
        price = float(price or 0.0)
        gst_rate = float(gst_rate if gst_rate is not None else 5.0)
        weight_kg = float(weight_kg or 1.0)
        p_barcode = p_barcode or ""
        p_category = p_category or "Essentials"
        curr_stock = float(stock or 0.0)
      else:
        name = item.get("name", "Product")
        price = float(item.get("price", 0.0) or 0.0)
        gst_rate = float(item.get("gst", 5.0) or 5.0)
        base_prod_id = None
        weight_kg = 1.0
        p_barcode = ""
        p_category = "Essentials"
        curr_stock = 0.0

      line_total = round(price * qty, 2)
      item_gst = round((line_total * gst_rate) / (100.0 + gst_rate), 2) if gst_rate > 0 else 0.0

      subtotal += line_total
      gst_total += item_gst
      summary_parts.append(f"{name} (x{qty})")
      processed_cart.append({
          "name": name,
          "price": price,
          "qty": qty,
          "gst": gst_rate,
          "total": line_total,
      })

      if prod:
        if base_prod_id:
          kg_deducted = round(qty * weight_kg, 3)
          cursor.execute(
              "UPDATE products SET stock = MAX(0, ROUND(stock - ?, 3)) WHERE id = ?",
              (kg_deducted, base_prod_id),
          )
          cursor.execute("SELECT stock FROM products WHERE id = ?", (base_prod_id,))
          b_after_row = cursor.fetchone()
          b_stock_after = float(b_after_row[0] or 0.0) if b_after_row else max(0.0, curr_stock - kg_deducted)
          track_events.append({
              "product_id": p_id,
              "barcode": p_barcode,
              "product_name": name,
              "category": p_category,
              "qty_change": -abs(kg_deducted),
              "stock_after": b_stock_after,
          })
          synced_bases.add(base_prod_id)
        else:
          stock_after = max(0.0, round(curr_stock - float(qty), 3))
          cursor.execute(
              "UPDATE products SET stock = MAX(0, ROUND(stock - ?, 3)) WHERE id = ?",
              (qty, p_id),
          )
          track_events.append({
              "product_id": p_id,
              "barcode": p_barcode,
              "product_name": name,
              "category": p_category,
              "qty_change": -abs(float(qty)),
              "stock_after": stock_after,
          })
          synced_bases.add(p_id)
      else:
        track_events.append({
            "product_id": p_id,
            "barcode": p_barcode,
            "product_name": name,
            "category": p_category,
            "qty_change": -abs(float(qty)),
            "stock_after": None,
        })

    for b_id in synced_bases:
      cursor.execute("SELECT stock FROM products WHERE id = ?", (b_id,))
      rem_row = cursor.fetchone()
      rem_kg = float(rem_row[0] or 0.0) if rem_row else 0.0
      cursor.execute(
          "SELECT id, COALESCE(weight_kg, 1.0) FROM products WHERE base_product_id = ?",
          (b_id,),
      )
      for v_id, v_w in cursor.fetchall():
        v_mult = float(v_w or 1.0)
        if v_mult > 0:
          cursor.execute(
              "UPDATE products SET stock = ? WHERE id = ?",
              (int(rem_kg // v_mult), v_id),
          )

    subtotal = round(subtotal, 2)
    gst_total = round(gst_total, 2)
    grand_total = subtotal
    items_summary = ", ".join(summary_parts) if summary_parts else "General Order"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
            INSERT INTO bills (
                customer_name, customer_phone, delivery_address, items_summary,
                subtotal, discount, gst_amount, total_amount,
                payment_mode, cash_tendered, change_returned, khata_status, order_status, order_source, timestamp
            )
            VALUES (?, ?, ?, ?, ?, 0.0, ?, ?, ?, 0.0, 0.0, 'Paid', 'Unpacked', 'Online', ?)
        """,
        (
            customer_name,
            customer_phone,
            delivery_address,
            items_summary,
            subtotal,
            gst_total,
            grand_total,
            payment_mode,
            timestamp,
        ),
    )
    bill_id = cursor.lastrowid

    for item in processed_cart:
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

    for ev in track_events:
      record_inventory_track(
          cursor,
          conn,
          product_id=ev["product_id"],
          barcode=ev["barcode"],
          product_name=ev["product_name"],
          category=ev["category"],
          movement_type="SOLD",
          qty_change=ev["qty_change"],
          stock_after=ev["stock_after"],
          source="Online Store",
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

    return redirect(
        url_for("customer.customer_receipt", bill_id=bill_id, placed=1)
    )

  except Exception as e:
    return (
        f"<h3 style='color:red;'>Checkout Error: {str(e)}</h3><p><a"
        " href='/'>Return to Store</a></p>"
    ), 500


@customer_bp.route("/customer_receipt/<int:bill_id>")
def customer_receipt(bill_id):
  conn = sqlite3.connect("store_pos.db")
  cursor = conn.cursor()

  cursor.execute("""
      SELECT
          id, customer_name, customer_phone, items_summary,
          subtotal, discount, gst_amount, total_amount,
          payment_mode, cash_tendered, change_returned, khata_status,
          timestamp, delivery_address, order_status
      FROM bills
      WHERE id = ?
  """, (bill_id,))
  raw_bill = cursor.fetchone()

  if not raw_bill:
    conn.close()
    flash("❌ Invoice not found or order not placed yet!")
    return redirect(url_for("customer.customer_shop"))

  bill = list(raw_bill)
  for idx in [4, 5, 6, 7, 9, 10]:
    if idx < len(bill) and bill[idx] is not None:
      try:
        bill[idx] = float(bill[idx])
      except ValueError:
        bill[idx] = 0.0

  delivery_address = (bill[13] or "").strip() if len(bill) > 13 and bill[13] else ""

  cursor.execute(
      "SELECT item_name, price, qty, gst_rate, total FROM bill_items WHERE bill_id = ?",
      (bill_id,),
  )
  raw_items = cursor.fetchall()

  items = []
  for item in raw_items:
    items.append((
        item[0],
        float(item[1]) if item[1] is not None else 0.0,
        int(item[2]) if item[2] is not None else 1,
        float(item[3]) if len(item) > 3 and item[3] is not None else 5.0,
        float(item[4]) if len(item) > 4 and item[4] is not None else 0.0
    ))

  settings_dict = {
      "store_name": "SWARAJ GROCERY",
      "store_address": "Main Road, Katol, Nagpur, Maharashtra",
      "gstin": "27AAAAA0000A1Z5",
      "phone": "9876543210",
      "receipt_header": "Thank you for shopping at Swaraj Grocery!",
      "receipt_footer": "Goods once sold will be replaced within 3 days."
  }

  try:
    cursor.execute("CREATE TABLE IF NOT EXISTS store_settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("SELECT key, value FROM store_settings")
    rows = cursor.fetchall()
    for r in rows:
      if r[0] and r[1]:
        settings_dict[r[0]] = r[1]
  except Exception:
    pass

  conn.close()
  order_placed = request.args.get("placed") == "1"
  return render_template(
      "receipt.html",
      bill=bill,
      items=items,
      settings=settings_dict,
      delivery_address=delivery_address,
      order_placed=order_placed,
  )