import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# =========================
# ENV
# =========================
MONGO_URI = os.getenv("MONGO_URI")
MERCHANT_ID = os.getenv("MERCHANT_ID")
PAYSTATION_PASSWORD = os.getenv("PAYSTATION_PASSWORD")
BASE_URL = os.getenv("BASE_URL")

PAY_INIT_URL = "https://sandbox.paystation.com.bd/initiate-payment"
PAY_STATUS_URL = "https://sandbox.paystation.com.bd/transaction-status"

# =========================
# APP
# =========================
app = Flask(__name__, template_folder="../templates")

# =========================
# DB (SAFE CONNECTION)
# =========================
client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000
)

db = client["paystation_demo"]
orders = db["orders"]

# =========================
# PRODUCTS (FIXED PRICES)
# =========================
PRODUCTS = {
    "p1": 5,
    "p2": 7,
    "p3": 10
}

# =========================
# FRONTEND
# =========================
@app.route("/")
def home():
    return render_template("index.html")


# =========================
# PRICE CALCULATION (TRUTH)
# =========================
def calculate_total(items):
    total = 0

    for item in items:
        pid = item.get("id")
        qty = int(item.get("qty", 0))

        if pid not in PRODUCTS:
            raise Exception("Invalid product")

        if qty <= 0:
            raise Exception("Invalid quantity")

        total += PRODUCTS[pid] * qty

    return total


# =========================
# CREATE ORDER (SAFE)
# =========================
@app.route("/api/create-order", methods=["POST"])
def create_order():
    try:
        data = request.get_json(force=True)

        items = data.get("items", [])

        if not isinstance(items, list):
            return jsonify({"error": "Invalid items"}), 400

        amount = calculate_total(items)

        invoice = str(uuid.uuid4())

        order = {
            "invoice": invoice,
            "items": items,
            "amount": amount,
            "status": "initiated",
            "verified": False,
            "customer": {
                "name": data.get("name"),
                "phone": data.get("phone"),
                "email": data.get("email")
            }
        }

        orders.insert_one(order)

        payload = {
            "merchantId": MERCHANT_ID,
            "password": PAYSTATION_PASSWORD,
            "invoice_number": invoice,
            "currency": "BDT",
            "payment_amount": amount,
            "cust_name": data.get("name"),
            "cust_phone": data.get("phone"),
            "cust_email": data.get("email"),
            "callback_url": f"{BASE_URL}/api/payment-callback"
        }

        try:
            resp = requests.post(PAY_INIT_URL, data=payload, timeout=10)
            result = resp.json()
        except Exception:
            return jsonify({
                "error": "Payment gateway error"
            }), 502

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "error": "Server error",
            "detail": str(e)
        }), 500


# =========================
# VERIFY PAYMENT (TRUTH SOURCE)
# =========================
def verify_payment(invoice):
    try:
        res = requests.post(
            PAY_STATUS_URL,
            data={
                "merchantId": MERCHANT_ID,
                "invoice_number": invoice
            },
            timeout=10
        ).json()

        data = res.get("data", {})
        status = data.get("trx_status")

        update = {"verified": True}

        if status == "success":
            update["status"] = "paid"
        else:
            update["status"] = "failed"

        orders.update_one(
            {"invoice": invoice},
            {"$set": update}
        )

    except:
        pass


# =========================
# CALLBACK (UNTRUSTED)
# =========================
@app.route("/api/payment-callback")
def callback():
    invoice = request.args.get("invoice_number")

    if not invoice:
        return "invalid", 400

    order = orders.find_one({"invoice": invoice})

    if not order:
        return "not found", 404

    # mark intermediate state
    orders.update_one(
        {"invoice": invoice},
        {"$set": {"status": "verifying"}}
    )

    # ALWAYS verify with PayStation
    verify_payment(invoice)

    return "OK"


 