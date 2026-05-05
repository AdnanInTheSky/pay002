import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

# =========================
# CONFIG
# =========================
MONGO_URI = os.getenv("MONGO_URI")
MERCHANT_ID = os.getenv("MERCHANT_ID")
PAYSTATION_PASSWORD = os.getenv("PAYSTATION_PASSWORD")
BASE_URL = os.getenv("BASE_URL")

PAY_INIT_URL = "https://sandbox.paystation.com.bd/initiate-payment"
PAY_STATUS_URL = "https://sandbox.paystation.com.bd/transaction-status"

app = Flask(__name__, template_folder="../templates")
limiter = Limiter(get_remote_address, app=app)

# =========================
# DB
# =========================
client = MongoClient(MONGO_URI)
db = client["paystation_demo"]
orders = db["orders"]

orders.create_index([("invoice", ASCENDING)], unique=True)

# =========================
# FIXED PRODUCTS (TRUTH SOURCE)
# =========================
PRODUCTS = {
    "p1": {"name": "Product 1", "price": 5},
    "p2": {"name": "Product 2", "price": 7},
    "p3": {"name": "Product 3", "price": 10},
}

# =========================
# PRICE CALCULATION (SERVER ONLY)
# =========================
def calculate_total(items):
    total = 0

    for item in items:
        pid = item.get("id")
        qty = int(item.get("qty", 0))

        if pid not in PRODUCTS:
            raise ValueError("Invalid product")

        if qty <= 0:
            raise ValueError("Invalid quantity")

        total += PRODUCTS[pid]["price"] * qty

    return total


# =========================
# PAYMENT VERIFICATION (TRUTH SOURCE)
# =========================
def verify_payment(invoice):
    order = orders.find_one({"invoice": invoice})
    if not order:
        return

    # prevent double processing
    if order.get("verified"):
        return

    try:
        res = requests.post(
            PAY_STATUS_URL,
            data={
                "merchantId": MERCHANT_ID,
                "invoice_number": invoice
            },
            timeout=10
        ).json()
    except:
        return

    if res.get("status") != "success":
        return

    data = res.get("data", {})
    status = data.get("trx_status")

    update = {
        "verified": True,
        "trx_id": data.get("trx_id")
    }

    if status == "success":
        update["status"] = "paid"
    elif status in ["failed", "refund"]:
        update["status"] = "failed"
    else:
        update["status"] = "processing"

    orders.update_one({"invoice": invoice}, {"$set": update})


# =========================
# HOME
# =========================
@app.route("/")
def home():
    return render_template("index.html", products=PRODUCTS)


# =========================
# CREATE ORDER (SECURE)
# =========================
@app.route("/api/create-order", methods=["POST"])
@limiter.limit("5 per minute")
def create_order():
    data = request.get_json()

    if not isinstance(data.get("items"), list):
        return jsonify({"error": "Invalid items"}), 400

    try:
        total = calculate_total(data["items"])
    except:
        return jsonify({"error": "Invalid cart"}), 400

    invoice = str(uuid.uuid4())

    orders.insert_one({
        "invoice": invoice,
        "items": data["items"],
        "amount": total,
        "status": "initiated",
        "verified": False,
        "customer": {
            "name": data.get("name"),
            "phone": data.get("phone"),
            "email": data.get("email")
        }
    })

    payload = {
        "merchantId": MERCHANT_ID,
        "password": PAYSTATION_PASSWORD,
        "invoice_number": invoice,
        "currency": "BDT",
        "payment_amount": total,
        "cust_name": data.get("name"),
        "cust_phone": data.get("phone"),
        "cust_email": data.get("email"),
        "callback_url": f"{BASE_URL}/api/payment-callback"
    }

    try:
        resp = requests.post(PAY_INIT_URL, data=payload, timeout=10).json()
    except:
        return jsonify({"error": "Payment gateway error"}), 502

    return jsonify(resp)


# =========================
# CALLBACK (UNTRUSTED TRIGGER ONLY)
# =========================
@app.route("/api/payment-callback")
def payment_callback():
    invoice = request.args.get("invoice_number")

    if not invoice:
        return "invalid", 400

    # NEVER TRUST CALLBACK
    verify_payment(invoice)

    return "OK"


 