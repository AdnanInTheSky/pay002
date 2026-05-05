import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template, redirect
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIG
# =========================
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MERCHANT_ID = os.getenv("MERCHANT_ID")
PASSWORD = os.getenv("PAYSTATION_PASSWORD")
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")

PAY_URL = "https://sandbox.paystation.com.bd/initiate-payment"
STATUS_URL = "https://sandbox.paystation.com.bd/transaction-status"

app = Flask(__name__, template_folder="../templates", static_folder="../static")

# =========================
# DB
# =========================
client = MongoClient(MONGO_URI)
db = client["paystation_demo"]
orders = db["orders"]

# =========================
# PRODUCTS (server-side truth)
# =========================
PRODUCTS = {
    "p1": {"name": "Wireless Earbuds",  "price": 1200, "emoji": "🎧"},
    "p2": {"name": "Phone Case",        "price": 350,  "emoji": "📱"},
    "p3": {"name": "USB-C Cable",       "price": 180,  "emoji": "🔌"},
    "p4": {"name": "Power Bank 10000mAh","price": 950, "emoji": "🔋"},
    "p5": {"name": "Screen Protector",  "price": 120,  "emoji": "🛡️"},
    "p6": {"name": "Smart Watch Strap", "price": 280,  "emoji": "⌚"},
}

# =========================
# PRICE ENGINE (server-side truth — never trust client)
# =========================
def calc(items):
    total = 0
    line_items = []
    for i in items:
        pid = i.get("id")
        qty = int(i.get("qty", 1))

        if pid not in PRODUCTS or qty < 1 or qty > 10:
            raise ValueError(f"Invalid product or quantity: {pid}")

        product = PRODUCTS[pid]
        subtotal = product["price"] * qty
        total += subtotal
        line_items.append({
            "id": pid,
            "name": product["name"],
            "price": product["price"],
            "qty": qty,
            "subtotal": subtotal
        })

    if total <= 0:
        raise ValueError("Cart is empty")

    return total, line_items


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return render_template("index.html", products=PRODUCTS)


@app.route("/success")
def success():
    invoice = request.args.get("invoice_number", "")
    status = request.args.get("status", "")
    return render_template("result.html", success=True, invoice=invoice, status=status)


@app.route("/failed")
def failed():
    invoice = request.args.get("invoice_number", "")
    return render_template("result.html", success=False, invoice=invoice, status="Failed")


# =========================
# API: CREATE ORDER
# =========================
@app.route("/api/create-order", methods=["POST"])
def create_order():
    try:
        data = request.get_json(force=True)

        name    = str(data.get("name", "")).strip()
        email   = str(data.get("email", "")).strip()
        phone   = str(data.get("phone", "")).strip()
        address = str(data.get("address", "")).strip()
        items   = data.get("items", [])

        # Basic validation
        if not all([name, email, phone, address]):
            return jsonify({"error": "All customer fields are required"}), 400

        if not items:
            return jsonify({"error": "Cart is empty"}), 400

        # Server-side price calculation
        amount, line_items = calc(items)

        invoice = str(uuid.uuid4())

        order_doc = {
            "invoice": invoice,
            "items": line_items,
            "amount": amount,
            "status": "initiated",
            "verified": False,
            "customer": {
                "name": name,
                "email": email,
                "phone": phone,
                "address": address
            }
        }
        orders.insert_one(order_doc)

        # Call PayStation
        payload = {
            "merchantId": MERCHANT_ID,
            "password": PASSWORD,
            "invoice_number": invoice,
            "currency": "BDT",
            "payment_amount": amount,
            "cust_name": name,
            "cust_phone": phone,
            "cust_email": email,
            "cust_address": address,
            "callback_url": f"{BASE_URL}/api/payment-callback",
            "checkout_items": str([i["name"] for i in line_items])
        }

        ps_res = requests.post(PAY_URL, data=payload, timeout=10).json()

        if ps_res.get("status") == "success" and ps_res.get("payment_url"):
            return jsonify({
                "payment_url": ps_res["payment_url"],
                "invoice": invoice
            })
        else:
            orders.update_one({"invoice": invoice}, {"$set": {"status": "failed"}})
            return jsonify({"error": ps_res.get("message", "Payment initiation failed")}), 400

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "Server error"}), 500


# =========================
# API: CALLBACK (verify with PayStation, never trust params)
# =========================
@app.route("/api/payment-callback")
def callback():
    invoice = request.args.get("invoice_number", "").strip()
    reported_status = request.args.get("status", "").lower()

    if not invoice:
        return "bad request", 400

    # Mark as verifying
    orders.update_one({"invoice": invoice}, {"$set": {"status": "verifying"}})

    # Verify independently with PayStation
    verified_status = verify_with_paystation(invoice)

    if verified_status == "success":
        orders.update_one({"invoice": invoice}, {"$set": {"status": "success", "verified": True}})
        return redirect(f"/success?invoice_number={invoice}&status=Success")
    else:
        orders.update_one({"invoice": invoice}, {"$set": {"status": verified_status, "verified": True}})
        return redirect(f"/failed?invoice_number={invoice}")


def verify_with_paystation(invoice):
    """Always verify payment status server-side with PayStation API."""
    try:
        headers = {"merchantId": MERCHANT_ID}
        body = {"invoice_number": invoice}
        r = requests.post(STATUS_URL, headers=headers, data=body, timeout=10).json()

        data = r.get("data", {})
        return data.get("trx_status", "failed")
    except Exception:
        return "failed"


# =========================
# API: ORDER STATUS (for frontend polling)
# =========================
@app.route("/api/order-status/<invoice>")
def order_status(invoice):
    order = orders.find_one({"invoice": invoice}, {"_id": 0})
    if not order:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "status": order.get("status"),
        "verified": order.get("verified"),
        "amount": order.get("amount"),
        "customer": order.get("customer", {}).get("name")
    })


# =========================
# VERCEL / LOCAL
# =========================
if __name__ == "__main__":
    app.run(debug=True)