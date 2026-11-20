from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, session
import sqlite3
from datetime import datetime
import json
import csv
import io
from database import db
from firebase_admin import firestore

admin_bp = Blueprint("admin", __name__)

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin.admin_login"))
        return f(*args, **kwargs)
    return decorated_function

def _ensure_order_columns(cursor, conn):
    for col_sql in [
        "ALTER TABLE bills ADD COLUMN order_status TEXT DEFAULT 'Unpacked'",
        "ALTER TABLE bills ADD COLUMN delivery_address TEXT DEFAULT ''",
        "ALTER TABLE bills ADD COLUMN order_source TEXT DEFAULT 'POS'"
    ]:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass

def _ensure_item_master_columns(cursor, conn):
    migrations = [
        "ALTER TABLE products ADD COLUMN sku_code TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN brand TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN pack_size TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN supplier_name TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN unit TEXT DEFAULT 'pcs'",
        "ALTER TABLE products ADD COLUMN mrp REAL DEFAULT 0.0",
        "ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0.0",
        "ALTER TABLE products ADD COLUMN hsn_code TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN reorder_level INTEGER DEFAULT 15",
        "ALTER TABLE products ADD COLUMN is_online INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN image_url TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN base_product_id INTEGER DEFAULT NULL",
        "ALTER TABLE products ADD COLUMN weight_kg REAL DEFAULT 1.0"
    ]
    for col_sql in migrations:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    try:
        cursor.execute("UPDATE products SET mrp = price WHERE mrp IS NULL OR mrp <= 0")
        cursor.execute("UPDATE products SET cost_price = 0.0 WHERE cost_price IS NULL OR cost_price < 0")
        cursor.execute("UPDATE products SET unit = 'pcs' WHERE unit IS NULL OR TRIM(unit) = ''")
        cursor.execute("UPDATE products SET reorder_level = 15 WHERE reorder_level IS NULL")
        cursor.execute("UPDATE products SET is_online = 1 WHERE is_online IS NULL")
        cursor.execute("UPDATE products SET is_active = 1 WHERE is_active IS NULL")
        cursor.execute("UPDATE products SET weight_kg = 1.0 WHERE weight_kg IS NULL OR weight_kg <= 0")
        cursor.execute("UPDATE products SET sku_code = 'SG-' || PRINTF('%04d', id) WHERE sku_code IS NULL OR TRIM(sku_code) = ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass

def _sync_linked_weight_variants(cursor, base_id):
    if not base_id:
        return
    cursor.execute(
        "SELECT price, mrp, cost_price, stock, gst_rate, hsn_code, supplier_name, image_url FROM products WHERE id = ?",
        (base_id,)
    )
    base = cursor.fetchone()
    if not base:
        return
    b_price, b_mrp, b_cost, b_stock, b_gst, b_hsn, b_sup, b_img = base
    b_price = float(b_price or 0.0)
    b_mrp = float(b_mrp or b_price)
    b_cost = float(b_cost or 0.0)
    b_stock = float(b_stock or 0.0)

    cursor.execute("SELECT id, COALESCE(weight_kg, 1.0) FROM products WHERE base_product_id = ?", (base_id,))
    variants = cursor.fetchall()
    for v_id, w_kg in variants:
        mult = float(w_kg or 1.0)
        if mult <= 0:
            mult = 1.0
        v_price = round(b_price * mult, 2)
        v_mrp = round(b_mrp * mult, 2)
        v_cost = round(b_cost * mult, 2)
        v_stock = int(b_stock // mult)
        cursor.execute("""
            UPDATE products
            SET price = ?, mrp = ?, cost_price = ?, stock = ?, gst_rate = ?,
                hsn_code = ?, supplier_name = ?, image_url = COALESCE(NULLIF(?, ''), image_url)
            WHERE id = ?
        """, (v_price, v_mrp, v_cost, v_stock, b_gst, b_hsn, b_sup, b_img or "", v_id))

@admin_bp.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        passcode = request.form.get("passcode")
        if passcode == "teddewar":
            session["is_admin"] = True
            return redirect(url_for("pos.pos_terminal"))
        else:
            flash("❌ Incorrect Admin Passcode!")
    return render_template("admin_login.html")

@admin_bp.route("/admin_logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Logged out of Admin ERP successfully.")
    return redirect(url_for("customer.customer_shop"))

@admin_bp.route("/api/unread_orders_count")
@admin_required
def unread_orders_count():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    _ensure_order_columns(cursor, conn)
    cursor.execute("""
        SELECT COUNT(*) FROM bills
        WHERE (order_status = 'Unpacked' OR order_status IS NULL)
          AND order_source = 'Online'
          AND NOT (customer_name = 'Walking Customer' AND (delivery_address IS NULL OR TRIM(delivery_address) = ''))
    """)
    count = cursor.fetchone()[0] or 0
    conn.close()
    return {"count": count}

@admin_bp.route("/online_orders")
@admin_required
def online_orders():
    conn = sqlite3.connect("store_pos.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    _ensure_order_columns(cursor, conn)

    cursor.execute("""
        SELECT * FROM bills
        WHERE order_source = 'Online'
          AND NOT (customer_name = 'Walking Customer' AND (delivery_address IS NULL OR TRIM(delivery_address) = ''))
        ORDER BY id DESC
    """)
    raw_orders = cursor.fetchall()
    conn.close()

    unpacked_orders = []
    packed_orders = []

    for r in raw_orders:
        try:
            tot = float(r["total_amount"]) if r["total_amount"] is not None else 0.0
        except (ValueError, TypeError):
            tot = 0.0

        addr = ""
        if "delivery_address" in r.keys() and r["delivery_address"]:
            addr = str(r["delivery_address"]).strip()
            
        order = {
            "id": r["id"],
            "customer_name": r["customer_name"] or "Online Customer",
            "customer_phone": r["customer_phone"] or "N/A",
            "delivery_address": addr or "Not Specified",
            "items_summary": r["items_summary"] or "",
            "total_amount": tot,
            "payment_mode": r["payment_mode"] or "Cash",
            "timestamp": r["timestamp"] or "",
            "order_status": r["order_status"] or "Unpacked"
        }
        
        if order["order_status"] == "Packed":
            packed_orders.append(order)
        else:
            unpacked_orders.append(order)

    return render_template("online_orders.html", unpacked_orders=unpacked_orders, packed_orders=packed_orders, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/pack_order/<int:order_id>")
@admin_required
def pack_order(order_id):
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE bills SET order_status = 'Packed' WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash(f"Order #{order_id} marked as Packed successfully!")
    return redirect(url_for("admin.online_orders"))

@admin_bp.route("/item_master", methods=["GET", "POST"])
@admin_required
def item_master():
    conn = sqlite3.connect("store_pos.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    _ensure_item_master_columns(cursor, conn)

    if request.method == "POST":
        action = request.form.get("action", "create")
        try:
            if action == "create":
                barcode = (request.form.get("barcode") or "").strip()
                sku_code = (request.form.get("sku_code") or "").strip()
                name = (request.form.get("name") or "").strip()
                brand = (request.form.get("brand") or "").strip()
                pack_size = (request.form.get("pack_size") or "").strip()
                supplier_name = (request.form.get("supplier_name") or "").strip()
                category = (request.form.get("category") or "Essentials").strip()
                unit = (request.form.get("unit") or "pcs").strip()
                raw_base_id = (request.form.get("base_product_id") or "").strip()
                base_product_id = int(raw_base_id) if raw_base_id.isdigit() else None
                weight_kg = float(request.form.get("weight_kg") or 1.0)
                if weight_kg <= 0:
                    weight_kg = 1.0

                mrp = float(request.form.get("mrp") or 0.0)
                price = float(request.form.get("price") or 0.0)
                cost_price = float(request.form.get("cost_price") or 0.0)
                gst_rate = float(request.form.get("gst_rate") or 5.0)
                hsn_code = (request.form.get("hsn_code") or "").strip()
                stock = int(float(request.form.get("stock") or 0))
                reorder_level = int(request.form.get("reorder_level") or 15)
                batch_no = (request.form.get("batch_no") or "GENERAL").strip()
                expiry_date = (request.form.get("expiry_date") or "2027-12-31").strip()
                rack_location = (request.form.get("rack_location") or "Rack-1 Shelf A").strip()
                image_url = (request.form.get("image_url") or "").strip()
                is_online = 1 if request.form.get("is_online") == "1" else 0
                is_active = 1 if request.form.get("is_active") == "1" else 0

                if base_product_id:
                    cursor.execute(
                        "SELECT price, mrp, cost_price, stock, gst_rate, hsn_code, supplier_name, image_url FROM products WHERE id = ?",
                        (base_product_id,)
                    )
                    b_row = cursor.fetchone()
                    if b_row:
                        b_price = float(b_row["price"] or 0.0)
                        b_mrp = float(b_row["mrp"] or b_price)
                        b_cost = float(b_row["cost_price"] or 0.0)
                        b_stock = float(b_row["stock"] or 0.0)
                        price = round(b_price * weight_kg, 2)
                        mrp = round(b_mrp * weight_kg, 2)
                        cost_price = round(b_cost * weight_kg, 2)
                        stock = int(b_stock // weight_kg)
                        if not gst_rate and b_row["gst_rate"] is not None:
                            gst_rate = float(b_row["gst_rate"])
                        if not hsn_code and b_row["hsn_code"]:
                            hsn_code = b_row["hsn_code"]
                        if not supplier_name and b_row["supplier_name"]:
                            supplier_name = b_row["supplier_name"]
                        if not image_url and b_row["image_url"]:
                            image_url = b_row["image_url"]

                if mrp <= 0:
                    mrp = price

                cursor.execute("""
                    INSERT INTO products (
                        barcode, sku_code, name, brand, pack_size, supplier_name,
                        category, unit, mrp, price, cost_price, gst_rate, hsn_code,
                        stock, reorder_level, batch_no, expiry_date, rack_location,
                        image_url, is_online, is_active, base_product_id, weight_kg
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    barcode, sku_code, name, brand, pack_size, supplier_name,
                    category, unit, mrp, price, cost_price, gst_rate, hsn_code,
                    stock, reorder_level, batch_no, expiry_date, rack_location,
                    image_url, is_online, is_active, base_product_id, weight_kg
                ))
                new_id = cursor.lastrowid
                if not sku_code:
                    cursor.execute("UPDATE products SET sku_code = ? WHERE id = ?", (f"SG-{new_id:04d}", new_id))
                conn.commit()
                flash(f"✅ Item '{name}' ({pack_size or unit}) added to Item Master!")

            elif action == "update":
                prod_id = int(request.form.get("prod_id"))
                barcode = (request.form.get("barcode") or "").strip()
                sku_code = (request.form.get("sku_code") or "").strip() or f"SG-{prod_id:04d}"
                name = (request.form.get("name") or "").strip()
                brand = (request.form.get("brand") or "").strip()
                pack_size = (request.form.get("pack_size") or "").strip()
                supplier_name = (request.form.get("supplier_name") or "").strip()
                category = (request.form.get("category") or "Essentials").strip()
                unit = (request.form.get("unit") or "pcs").strip()
                raw_base_id = (request.form.get("base_product_id") or "").strip()
                base_product_id = int(raw_base_id) if raw_base_id.isdigit() else None
                weight_kg = float(request.form.get("weight_kg") or 1.0)
                if weight_kg <= 0:
                    weight_kg = 1.0

                mrp = float(request.form.get("mrp") or 0.0)
                price = float(request.form.get("price") or 0.0)
                cost_price = float(request.form.get("cost_price") or 0.0)
                gst_rate = float(request.form.get("gst_rate") or 5.0)
                hsn_code = (request.form.get("hsn_code") or "").strip()
                stock = int(float(request.form.get("stock") or 0))
                reorder_level = int(request.form.get("reorder_level") or 15)
                batch_no = (request.form.get("batch_no") or "GENERAL").strip()
                expiry_date = (request.form.get("expiry_date") or "2027-12-31").strip()
                rack_location = (request.form.get("rack_location") or "Rack-1 Shelf A").strip()
                image_url = (request.form.get("image_url") or "").strip()
                is_online = 1 if request.form.get("is_online") == "1" else 0
                is_active = 1 if request.form.get("is_active") == "1" else 0

                if base_product_id:
                    cursor.execute(
                        "SELECT price, mrp, cost_price, stock FROM products WHERE id = ?",
                        (base_product_id,)
                    )
                    b_row = cursor.fetchone()
                    if b_row:
                        b_price = float(b_row["price"] or 0.0)
                        b_mrp = float(b_row["mrp"] or b_price)
                        b_cost = float(b_row["cost_price"] or 0.0)
                        b_stock = float(b_row["stock"] or 0.0)
                        price = round(b_price * weight_kg, 2)
                        mrp = round(b_mrp * weight_kg, 2)
                        cost_price = round(b_cost * weight_kg, 2)
                        stock = int(b_stock // weight_kg)

                if mrp <= 0:
                    mrp = price

                cursor.execute("""
                    UPDATE products
                    SET barcode = ?, sku_code = ?, name = ?, brand = ?, pack_size = ?, supplier_name = ?,
                        category = ?, unit = ?, mrp = ?, price = ?, cost_price = ?, gst_rate = ?, hsn_code = ?,
                        stock = ?, reorder_level = ?, batch_no = ?, expiry_date = ?, rack_location = ?,
                        image_url = ?, is_online = ?, is_active = ?, base_product_id = ?, weight_kg = ?
                    WHERE id = ?
                """, (
                    barcode, sku_code, name, brand, pack_size, supplier_name,
                    category, unit, mrp, price, cost_price, gst_rate, hsn_code,
                    stock, reorder_level, batch_no, expiry_date, rack_location,
                    image_url, is_online, is_active, base_product_id, weight_kg, prod_id
                ))

                # Sync any child weight variants linked to this item
                _sync_linked_weight_variants(cursor, prod_id)
                conn.commit()
                flash(f"✅ Item Master updated for '{name}'!")

            elif action == "delete":
                prod_id = int(request.form.get("prod_id"))
                cursor.execute("UPDATE products SET base_product_id = NULL WHERE base_product_id = ?", (prod_id,))
                cursor.execute("DELETE FROM products WHERE id = ?", (prod_id,))
                conn.commit()
                flash("🗑️ Item deleted from Item Master.")

        except Exception as e:
            flash(f"❌ Item Master Error: {str(e)}")

        conn.close()
        return redirect(url_for("admin.item_master"))

    sold_map = {}
    try:
        cursor.execute("SELECT LOWER(TRIM(item_name)) AS key_name, SUM(qty) AS sold_qty, SUM(total) AS sold_rev FROM bill_items GROUP BY LOWER(TRIM(item_name))")
        for sr in cursor.fetchall():
            if sr["key_name"]:
                sold_map[sr["key_name"]] = {
                    "qty": int(sr["sold_qty"] or 0),
                    "rev": float(sr["sold_rev"] or 0.0)
                }
    except sqlite3.OperationalError:
        pass

    cursor.execute("SELECT * FROM products ORDER BY id DESC")
    raw_products = cursor.fetchall()
    conn.close()

    items = []
    total_realized_profit = 0.0
    for p in raw_products:
        d = dict(p)
        price = float(d.get("price") or 0.0)
        mrp = float(d.get("mrp") or price)
        cost = float(d.get("cost_price") or 0.0)
        gst_rate = float(d.get("gst_rate") or 0.0)

        unit_profit = round(price - cost, 2) if cost > 0 else 0.0
        included_gst = round((price * gst_rate) / (100.0 + gst_rate), 2) if gst_rate > 0 else 0.0
        net_unit_profit = round((price - included_gst) - cost, 2) if cost > 0 else 0.0
        margin_pct = round(((price - cost) / price) * 100.0, 1) if price > 0 and cost > 0 else 0.0

        sold_info = sold_map.get((d.get("name") or "").strip().lower(), {"qty": 0, "rev": 0.0})
        units_sold = sold_info["qty"]
        earned_profit = round(units_sold * unit_profit, 2) if cost > 0 else 0.0
        total_realized_profit += earned_profit

        d["price"] = price
        d["mrp"] = mrp
        d["cost_price"] = cost
        d["unit_profit"] = unit_profit
        d["net_unit_profit"] = net_unit_profit
        d["margin_pct"] = margin_pct
        d["units_sold"] = units_sold
        d["earned_profit"] = earned_profit
        d["sku_code"] = d.get("sku_code") or f"SG-{d['id']:04d}"
        d["brand"] = d.get("brand") or ""
        d["pack_size"] = d.get("pack_size") or ""
        d["supplier_name"] = d.get("supplier_name") or ""
        d["unit"] = d.get("unit") or "pcs"
        d["hsn_code"] = d.get("hsn_code") or ""
        d["reorder_level"] = int(d.get("reorder_level") if d.get("reorder_level") is not None else 15)
        d["is_online"] = int(d.get("is_online") if d.get("is_online") is not None else 1)
        d["is_active"] = int(d.get("is_active") if d.get("is_active") is not None else 1)
        d["image_url"] = d.get("image_url") or ""
        d["base_product_id"] = d.get("base_product_id")
        d["weight_kg"] = float(d.get("weight_kg") or 1.0)
        items.append(d)

    return render_template(
        "item_master.html",
        items=items,
        total_realized_profit=round(total_realized_profit, 2),
        admin_navbar=ADMIN_NAVBAR_SNIPPET
    )

@admin_bp.route("/item_master/toggle/<int:prod_id>/<string:field>")
@admin_required
def toggle_item_master_flag(prod_id, field):
    if field not in ("is_online", "is_active"):
        return redirect(url_for("admin.item_master"))
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    _ensure_item_master_columns(cursor, conn)
    cursor.execute(f"UPDATE products SET {field} = CASE WHEN COALESCE({field}, 1) = 1 THEN 0 ELSE 1 END WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()
    flash("Updated item visibility/status!")
    return redirect(url_for("admin.item_master"))

@admin_bp.route("/item_master/export_csv")
@admin_required
def export_item_master_csv():
    conn = sqlite3.connect("store_pos.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    _ensure_item_master_columns(cursor, conn)
    cursor.execute("SELECT * FROM products ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        "id", "sku_code", "barcode", "name", "brand", "pack_size", "supplier_name",
        "category", "unit", "mrp", "price", "cost_price", "gst_rate", "hsn_code",
        "stock", "reorder_level", "batch_no", "expiry_date", "rack_location",
        "is_online", "is_active", "image_url", "base_product_id", "weight_kg"
    ]
    writer.writerow(headers)
    for r in rows:
        d = dict(r)
        writer.writerow([d.get(h, "") for h in headers])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=Swaraj_Grocery_Item_Master.csv"}
    )

@admin_bp.route("/item_master/import_csv", methods=["POST"])
@admin_required
def import_item_master_csv():
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("❌ Please choose a CSV file to upload.")
        return redirect(url_for("admin.item_master"))

    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        reader = csv.DictReader(stream)
        conn = sqlite3.connect("store_pos.db")
        cursor = conn.cursor()
        _ensure_item_master_columns(cursor, conn)

        updated_count = 0
        inserted_count = 0

        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            barcode = (row.get("barcode") or "").strip()
            sku_code = (row.get("sku_code") or "").strip()
            brand = (row.get("brand") or "").strip()
            pack_size = (row.get("pack_size") or "").strip()
            supplier_name = (row.get("supplier_name") or "").strip()
            category = (row.get("category") or "Essentials").strip()
            unit = (row.get("unit") or "pcs").strip()
            price = float(row.get("price") or 0.0)
            mrp = float(row.get("mrp") or price)
            cost_price = float(row.get("cost_price") or 0.0)
            gst_rate = float(row.get("gst_rate") or 5.0)
            hsn_code = (row.get("hsn_code") or "").strip()
            stock = int(float(row.get("stock") or 0))
            reorder_level = int(float(row.get("reorder_level") or 15))
            batch_no = (row.get("batch_no") or "GENERAL").strip()
            expiry_date = (row.get("expiry_date") or "2027-12-31").strip()
            rack_location = (row.get("rack_location") or "Rack-1 Shelf A").strip()
            is_online = int(float(row.get("is_online") if row.get("is_online") not in (None, "") else 1))
            is_active = int(float(row.get("is_active") if row.get("is_active") not in (None, "") else 1))
            image_url = (row.get("image_url") or "").strip()
            raw_base = (row.get("base_product_id") or "").strip()
            base_product_id = int(float(raw_base)) if raw_base else None
            weight_kg = float(row.get("weight_kg") or 1.0)

            existing = None
            if barcode:
                cursor.execute("SELECT id FROM products WHERE barcode = ?", (barcode,))
                existing = cursor.fetchone()
            if not existing and sku_code:
                cursor.execute("SELECT id FROM products WHERE sku_code = ?", (sku_code,))
                existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    UPDATE products
                    SET name = ?, brand = ?, pack_size = ?, supplier_name = ?,
                        category = ?, unit = ?, mrp = ?, price = ?, cost_price = ?,
                        gst_rate = ?, hsn_code = ?, stock = ?, reorder_level = ?,
                        batch_no = ?, expiry_date = ?, rack_location = ?,
                        is_online = ?, is_active = ?, image_url = COALESCE(NULLIF(?, ''), image_url),
                        base_product_id = ?, weight_kg = ?
                    WHERE id = ?
                """, (
                    name, brand, pack_size, supplier_name,
                    category, unit, mrp, price, cost_price,
                    gst_rate, hsn_code, stock, reorder_level,
                    batch_no, expiry_date, rack_location,
                    is_online, is_active, image_url, base_product_id, weight_kg, existing[0]
                ))
                updated_count += 1
            else:
                cursor.execute("""
                    INSERT INTO products (
                        barcode, sku_code, name, brand, pack_size, supplier_name,
                        category, unit, mrp, price, cost_price, gst_rate, hsn_code,
                        stock, reorder_level, batch_no, expiry_date, rack_location,
                        is_online, is_active, image_url, base_product_id, weight_kg
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    barcode, sku_code, name, brand, pack_size, supplier_name,
                    category, unit, mrp, price, cost_price, gst_rate, hsn_code,
                    stock, reorder_level, batch_no, expiry_date, rack_location,
                    is_online, is_active, image_url, base_product_id, weight_kg
                ))
                new_id = cursor.lastrowid
                if not sku_code:
                    cursor.execute("UPDATE products SET sku_code = ? WHERE id = ?", (f"SG-{new_id:04d}", new_id))
                inserted_count += 1

        conn.commit()
        conn.close()
        flash(f"✅ CSV Import complete: {inserted_count} added, {updated_count} updated.")
    except Exception as e:
        flash(f"❌ CSV Import Error: {str(e)}")

    return redirect(url_for("admin.item_master"))

@admin_bp.route("/inventory", methods=["GET", "POST"])
@admin_required
def inventory():
    try:
        conn = sqlite3.connect("store_pos.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        action = request.form.get("action")
        
        if request.method == "POST":
            if action == "update":
                prod_id = request.form.get("prod_id")
                name = request.form.get("name")
                price = float(request.form.get("price") or 0.0)
                stock = int(request.form.get("stock") or 0)
                category = request.form.get("category", "Essentials")
                
                cursor.execute("""
                    UPDATE products SET name = ?, price = ?, stock = ?, category = ?
                    WHERE id = ?
                """, (name, price, stock, category, prod_id))
                _sync_linked_weight_variants(cursor, prod_id)
                conn.commit()
                conn.close()
                flash(f"Product '{name}' updated successfully!")
                return redirect(url_for("admin.inventory"))
                
            elif action == "delete":
                prod_id = request.form.get("prod_id")
                cursor.execute("DELETE FROM products WHERE id = ?", (prod_id,))
                conn.commit()
                conn.close()
                flash("Product deleted successfully!")
                return redirect(url_for("admin.inventory"))
                
            else:
                barcode = request.form.get("barcode")
                name = request.form.get("name")
                price = float(request.form.get("price") or 0.0)
                stock = int(request.form.get("stock") or 0)
                gst = float(request.form.get("gst_rate", 5.0) or 5.0)
                batch_no = request.form.get("batch_no", "GENERAL")
                expiry_date = request.form.get("expiry_date", "2027-12-31")
                rack_location = request.form.get("rack_location", "Rack-1 Shelf A")
                category = request.form.get("category", "Essentials")
                
                cursor.execute("SELECT id FROM products WHERE barcode = ?", (barcode,))
                existing = cursor.fetchone()
                
                if existing:
                    existing_id = existing["id"]
                    cursor.execute("""
                        UPDATE products 
                        SET stock = stock + ?, name = ?, price = ?, category = ?, batch_no = ?, expiry_date = ?, rack_location = ?
                        WHERE id = ?
                    """, (stock, name, price, category, batch_no, expiry_date, rack_location, existing_id))
                    _sync_linked_weight_variants(cursor, existing_id)
                    conn.commit()
                    flash(f"Product '{name}' restocked! Added +{stock} to previous stock.")
                else:
                    cursor.execute("""
                        INSERT INTO products (barcode, name, price, stock, gst_rate, batch_no, expiry_date, rack_location, category)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (barcode, name, price, stock, gst, batch_no, expiry_date, rack_location, category))
                    conn.commit()
                    flash("New product added to inventory successfully!")
                
                conn.close()
                return redirect(url_for("admin.inventory"))
                
        cursor.execute("SELECT * FROM products")
        products = cursor.fetchall()
        conn.close()
        return render_template("inventory.html", products=products, admin_navbar=ADMIN_NAVBAR_SNIPPET)
        
    except Exception as e:
        return f"<h3 style='color:red; padding: 20px;'>Inventory Error: {str(e)}</h3><p style='padding: 0 20px;'><a href='/inventory'>&larr; Return to Inventory</a></p>", 500

@admin_bp.route("/product_images", methods=["GET", "POST"])
@admin_required
def product_images():
    try:
        conn = sqlite3.connect("store_pos.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if request.method == "POST":
            prod_id = request.form.get("prod_id")
            image_url = request.form.get("image_url", "").strip()
            
            cursor.execute("UPDATE products SET image_url = ? WHERE id = ?", (image_url, prod_id))
            _sync_linked_weight_variants(cursor, prod_id)
            conn.commit()
            flash("Product image updated successfully for online store!")
            conn.close()
            return redirect(url_for("admin.product_images"))
            
        cursor.execute("SELECT id, barcode, name, category, image_url FROM products")
        products = cursor.fetchall()
        conn.close()
        
        return render_template("product_images.html", products=products, admin_navbar=ADMIN_NAVBAR_SNIPPET)
        
    except Exception as e:
        return f"<h3 style='color:red;'>Error: {str(e)}</h3><p><a href='/inventory'>Return</a></p>", 500

@admin_bp.route("/khata_limits", methods=["GET", "POST"])
@admin_required
def khata_limits():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    if request.method == "POST":
        name = request.form.get("name")
        phone = request.form.get("phone")
        limit = float(request.form.get("credit_limit", 2000.0))
        cursor.execute("""
            INSERT INTO customers (name, phone, credit_limit) VALUES (?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET name=?, credit_limit=?
        """, (name, phone, limit, name, limit))
        conn.commit()
        flash(f"Credit limit set for {name} (₹{limit})!")
    cursor.execute("SELECT * FROM customers")
    customers = cursor.fetchall()
    conn.close()
    return render_template("limits.html", customers=customers, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/khata")
@admin_required
def khata_ledger():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, customer_name, customer_phone, total_amount, timestamp FROM bills WHERE payment_mode = 'Credit' AND khata_status = 'Unpaid' ORDER BY id DESC")
    unpaid_bills = cursor.fetchall()
    cursor.execute("SELECT SUM(total_amount) FROM bills WHERE payment_mode = 'Credit' AND khata_status = 'Unpaid'")
    total_khata_due = cursor.fetchone()[0] or 0.0
    conn.close()
    return render_template("khata.html", unpaid_bills=unpaid_bills, total_khata_due=total_khata_due, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/settle_khata/<int:bill_id>")
@admin_required
def settle_khata(bill_id):
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE bills SET khata_status = 'Paid' WHERE id = ?", (bill_id,))
    conn.commit()
    conn.close()
    flash(f"Khata Bill #{bill_id} marked as Paid/Settled!")
    return redirect(url_for("admin.khata_ledger"))

@admin_bp.route("/low_stock")
@admin_required
def low_stock_alert():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    _ensure_item_master_columns(cursor, conn)
    cursor.execute("SELECT id, barcode, name, price, stock, batch_no FROM products WHERE COALESCE(is_active, 1) = 1 AND stock <= COALESCE(reorder_level, 15) ORDER BY stock ASC")
    products = cursor.fetchall()
    conn.close()
    return render_template("low_stock.html", products=products, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/purchase", methods=["GET", "POST"])
@admin_required
def purchase_entry():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    _ensure_item_master_columns(cursor, conn)
    if request.method == "POST":
        supplier_name = request.form.get("supplier_name")
        invoice_no = request.form.get("invoice_no")
        product_id = request.form.get("product_id")
        qty_added = int(request.form.get("qty") or 0)
        total_amount = float(request.form.get("total_amount") or 0.0)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        prod = cursor.fetchone()
        if prod:
            p_name = prod[0]
            items_summary = f"{p_name} (x{qty_added})"
            cursor.execute("""
                INSERT INTO purchases (supplier_name, invoice_no, items_summary, total_amount, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (supplier_name, invoice_no, items_summary, total_amount, timestamp))
            cursor.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (qty_added, product_id))
            if qty_added > 0 and total_amount > 0:
                unit_cost = round(total_amount / qty_added, 2)
                cursor.execute("UPDATE products SET cost_price = ?, supplier_name = COALESCE(NULLIF(?, ''), supplier_name) WHERE id = ?", (unit_cost, supplier_name or "", product_id))
            _sync_linked_weight_variants(cursor, product_id)
            conn.commit()
            flash("Purchase recorded, unit cost & inventory stock updated successfully!")
    cursor.execute("SELECT id, name, stock FROM products")
    products = cursor.fetchall()
    cursor.execute("SELECT * FROM purchases ORDER BY id DESC")
    purchases = cursor.fetchall()
    conn.close()
    return render_template("purchase.html", products=products, purchases=purchases, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/daybook")
@admin_required
def daybook():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, customer_name, customer_phone, total_amount, payment_mode, khata_status, timestamp FROM bills ORDER BY id DESC")
    bills = cursor.fetchall()
    cursor.execute("SELECT payment_mode, SUM(total_amount) FROM bills GROUP BY payment_mode")
    totals_by_mode = dict(cursor.fetchall())
    cursor.execute("SELECT SUM(total_amount) FROM bills")
    total_revenue = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(amount) FROM expenses")
    total_expenses = cursor.fetchone()[0] or 0.0
    conn.close()
    return render_template("daybook.html", bills=bills, totals_by_mode=totals_by_mode, total_revenue=total_revenue, total_expenses=total_expenses, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/gst_report")
@admin_required
def gst_report():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, items_summary, subtotal, gst_amount, total_amount, timestamp FROM bills ORDER BY id DESC")
    bills = cursor.fetchall()
    cursor.execute("SELECT SUM(subtotal), SUM(gst_amount), SUM(total_amount) FROM bills")
    totals = cursor.fetchone()
    conn.close()
    return render_template("gst_report.html", bills=bills, taxable_total=totals[0] or 0.0, gst_total=totals[1] or 0.0, grand_total=totals[2] or 0.0, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/expenses", methods=["GET", "POST"])
@admin_required
def expenses():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    if request.method == "POST":
        title = request.form.get("title")
        amount = float(request.form.get("amount") or 0.0)
        category = request.form.get("category", "General")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO expenses (title, amount, category, timestamp)
            VALUES (?, ?, ?, ?)
        """, (title, amount, category, timestamp))
        conn.commit()
        flash("Expense recorded successfully!")
    cursor.execute("SELECT * FROM expenses ORDER BY id DESC")
    expense_list = cursor.fetchall()
    cursor.execute("SELECT SUM(amount) FROM expenses")
    total_expenses = cursor.fetchone()[0] or 0.0
    conn.close()
    return render_template("expenses.html", expenses=expense_list, total_expenses=total_expenses, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/profit_loss")
@admin_required
def profit_loss():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    _ensure_item_master_columns(cursor, conn)

    cursor.execute("SELECT SUM(total_amount) FROM bills")
    total_revenue = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(total_amount) FROM purchases")
    total_purchases = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(amount) FROM expenses")
    total_expenses = cursor.fetchone()[0] or 0.0

    realized_gross_profit = 0.0
    try:
        cursor.execute("""
            SELECT COALESCE(SUM((bi.price - p.cost_price) * bi.qty), 0.0)
            FROM bill_items bi
            JOIN products p ON LOWER(TRIM(bi.item_name)) = LOWER(TRIM(p.name))
            WHERE COALESCE(p.cost_price, 0) > 0
        """)
        realized_gross_profit = float(cursor.fetchone()[0] or 0.0)
    except sqlite3.OperationalError:
        pass

    conn.close()
    net_profit = total_revenue - (total_purchases + total_expenses)
    realized_net_profit = round(realized_gross_profit - total_expenses, 2)
    return render_template(
        "profit_loss.html",
        total_revenue=total_revenue,
        total_purchases=total_purchases,
        total_expenses=total_expenses,
        net_profit=net_profit,
        realized_gross_profit=round(realized_gross_profit, 2),
        realized_net_profit=realized_net_profit,
        admin_navbar=ADMIN_NAVBAR_SNIPPET
    )

@admin_bp.route("/batches")
@admin_required
def batch_tracker():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, barcode, name, price, stock, batch_no, expiry_date, rack_location FROM products ORDER BY expiry_date ASC")
    products = cursor.fetchall()
    conn.close()
    return render_template("batches.html", products=products, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def store_settings():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS store_settings (key TEXT PRIMARY KEY, value TEXT)")
    if request.method == "POST":
        store_name = request.form.get("store_name", "SWARAJ GROCERY")
        store_address = request.form.get("store_address", "Main Road, Katol, Nagpur, Maharashtra")
        gstin = request.form.get("gstin", "27AAAAA0000A1Z5")
        phone = request.form.get("phone", "9876543210")
        receipt_header = request.form.get("receipt_header", "Thank you for shopping at Swaraj Grocery!")
        receipt_footer = request.form.get("receipt_footer", "Goods once sold will be replaced within 3 days.\nNo exchange without bill.\nVisit Again! 🙏")
        
        for key, val in [
            ("store_name", store_name),
            ("store_address", store_address),
            ("gstin", gstin),
            ("phone", phone),
            ("receipt_header", receipt_header),
            ("receipt_footer", receipt_footer)
        ]:
            cursor.execute("""
                INSERT INTO store_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = ?
            """, (key, val, val))
        conn.commit()
        flash("Bill Header & Footer Settings updated successfully!")
        
    cursor.execute("SELECT key, value FROM store_settings")
    settings_dict = dict(cursor.fetchall())
    conn.close()
    return render_template("settings.html", settings=settings_dict, admin_navbar=ADMIN_NAVBAR_SNIPPET)

@admin_bp.route("/gstr_export")
@admin_required
def gstr_export():
    conn = sqlite3.connect("store_pos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, customer_name, subtotal, gst_amount, total_amount, timestamp FROM bills")
    bills = cursor.fetchall()
    conn.close()
    
    gstr_data = {
        "gstin": "27AAAAA0000A1Z5",
        "store_name": "Swaraj Grocery, Katol",
        "return_period": datetime.now().strftime("%m%Y"),
        "b2cs": []
    }
    for b in bills:
        gstr_data["b2cs"].append({
            "inum": f"INV-{b[0]}",
            "idt": b[5],
            "val": b[4],
            "pos": "27-Maharashtra",
            "txval": b[2],
            "iamt": 0.0,
            "camt": round(b[3]/2, 2),
            "samt": round(b[3]/2, 2)
        })
    return Response(json.dumps(gstr_data, indent=4), mimetype="application/json", headers={"Content-Disposition": "attachment;filename=GSTR1_Export.json"})

# --- ADMIN NAVBAR SNIPPET ---
ADMIN_NAVBAR_SNIPPET = """
<div class="bg-slate-900 text-white p-4 shadow-xl mb-6 rounded-2xl border border-slate-800">
    <div class="flex flex-col md:flex-row justify-between items-center pb-3 mb-3 border-b border-slate-800 gap-2">
        <div class="flex items-center space-x-3">
            <span class="text-xl font-extrabold tracking-wide text-emerald-400">🛡️ SWARAJ GROCERY ADMIN ERP</span>
            <span class="text-xs bg-emerald-600 text-white px-2.5 py-1 rounded-full font-bold shadow-sm">Katol Store</span>
        </div>
        <div class="flex items-center space-x-4 text-xs font-mono">
            <span class="text-green-400 font-bold">● Secure Admin Mode</span>
            <a href="/admin_logout" class="bg-rose-600 hover:bg-rose-700 text-white px-3 py-1 rounded-lg font-bold transition">🔒 Logout</a>
        </div>
    </div>
    <div class="flex flex-wrap gap-1.5 justify-center md:justify-start text-xs font-medium">
        <a href="/pos" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-green-600 text-slate-200 hover:text-white transition shadow-sm">🛍️ POS Counter</a>
        <a href="/online_orders" class="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white font-bold transition shadow-sm flex items-center gap-1">
            🔔 Orders <span id="order-badge" class="bg-rose-600 text-white text-[10px] px-1.5 py-0.5 rounded-full font-extrabold hidden">0</span>
        </a>
        <a href="/item_master" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-emerald-600 text-slate-200 hover:text-white transition shadow-sm">🗂️ Item Master</a>
        <a href="/inventory" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-blue-600 text-slate-200 hover:text-white transition shadow-sm">📦 Inventory</a>
        <a href="/product_images" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-pink-600 text-slate-200 hover:text-white transition shadow-sm">🖼️ Online Images</a>
        <a href="/low_stock" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-red-600 text-slate-200 hover:text-white transition shadow-sm">⚠️ Low Stock</a>
        <a href="/batches" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-orange-600 text-slate-200 hover:text-white transition shadow-sm">⏳ Batches</a>
        <a href="/purchase" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-indigo-600 text-slate-200 hover:text-white transition shadow-sm">📥 Purchases</a>
        <a href="/khata" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-amber-600 text-slate-200 hover:text-white transition shadow-sm">📒 Khata Dues</a>
        <a href="/khata_limits" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-yellow-600 text-slate-200 hover:text-white transition shadow-sm">🛡️ Credit Limits</a>
        <a href="/daybook" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-purple-600 text-slate-200 hover:text-white transition shadow-sm">📊 Day Book</a>
        <a href="/profit_loss" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-yellow-700 text-slate-200 hover:text-white transition shadow-sm">📈 P&L</a>
        <a href="/gst_report" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-teal-600 text-slate-200 hover:text-white transition shadow-sm">📑 GST Report</a>
        <a href="/gstr_export" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-cyan-600 text-slate-200 hover:text-white transition shadow-sm">📥 GSTR JSON</a>
        <a href="/expenses" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-rose-600 text-slate-200 hover:text-white transition shadow-sm">💸 Expenses</a>
        <a href="/settings" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 hover:text-white transition shadow-sm">⚙️ Bill Settings</a>
    </div>
</div>

<script>
    function playBeep() {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(880, ctx.currentTime);
            gain.gain.setValueAtTime(0.2, ctx.currentTime);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.2);
        } catch(e) {
            console.log('Audio Context unsupported or blocked');
        }
    }

    let lastOrderCount = localStorage.getItem('swaraj_last_order_count') !== null ? parseInt(localStorage.getItem('swaraj_last_order_count')) : null;

    function pollOrders() {
        fetch('/api/unread_orders_count')
            .then(res => res.json())
            .then(data => {
                const badge = document.getElementById('order-badge');
                if (lastOrderCount === null) {
                    lastOrderCount = data.count;
                    localStorage.setItem('swaraj_last_order_count', data.count);
                } else if (data.count > lastOrderCount) {
                    playBeep();
                    lastOrderCount = data.count;
                    localStorage.setItem('swaraj_last_order_count', data.count);
                }
                
                if (data.count > 0) {
                    badge.innerText = data.count;
                    badge.classList.remove('hidden');
                } else {
                    badge.classList.add('hidden');
                }
            }).catch(ext => console.log('Polling error:', ext));
    }

    setInterval(pollOrders, 10000);
    pollOrders();
</script>
"""