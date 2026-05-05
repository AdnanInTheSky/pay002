import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient, ASCENDING
from datetime import datetime

app = Flask(__name__, template_folder="../templates")

# =====================
# CONFIG
# =====================
MONGO_URI = os.getenv("MONGO_URI")
MERCHANT_ID = os.getenv("MERCHANT_ID")
PASSWORD = os.getenv("PAYSTATION_PASSWORD")
BASE_URL = os.getenv("BASE_URL")

PAY_URL = "https://sandbox.paystation.com.bd/initiate-payment"
STATUS_URL = "https://sandbox.paystation.com.bd/transaction-status"

# =====================
# DB
# =====================
client = MongoClient(MONGO_URI)
db = client["paystation_demo"]

orders = db["orders"]
logs = db["payment_logs"]

# Unique constraint (important)
orders.create_index("invoice", unique=True)

# =====================
# PRODUCTS (TRUTH SOURCE)
# =====================
PRODUCTS = {"p1": 5, "p2": 7, "p3": 10}


# =====================
# PRICE ENGINE
# =====================
def calc(items):
    total = 0
    for i in items:
        pid = i["id"]
        qty = int(i["qty"])

        if pid not in PRODUCTS:
            raise Exception("invalid product")

        total += PRODUCTS[pid] * qty

    return total


# =====================
# LOG FUNCTION (AUDIT)
# =====================
def log(invoice, event, data=None):
    logs.insert_one({
        "invoice": invoice,
        "event": event,
        "data": data,
        "time": datetime.utcnow()
    })


# =====================
# HOME
# =====================
@app.route("/")
def home():
    return render_template("index.html")


# =====================
# CREATE ORDER (LOCKED INITIATION)
# =====================
@app.route("/api/create-order", methods=["POST"])
def create_order():
    try:
        data = request.get_json(force=True)

        items = data.get("items", [])
        amount = calc(items)

        invoice = str(uuid.uuid4())

        order = {
            "invoice": invoice,
            "items": items,
            "amount": amount,
            "status": "INITIATED",
            "verified": False,
            "locked": True,
            "customer": {
                "name": data.get("name"),
                "email": data.get("email"),
                "phone": data.get("phone"),
                "address": data.get("address")
            }
        }

        orders.insert_one(order)
        log(invoice, "ORDER_CREATED", order)

        payload = {
            "merchantId": MERCHANT_ID,
            "password": PASSWORD,
            "invoice_number": invoice,
            "currency": "BDT",
            "payment_amount": amount,
            "cust_name": data.get("name"),
            "cust_phone": data.get("phone"),
            "cust_email": data.get("email"),
            "cust_address": data.get("address"),
            "callback_url": f"{BASE_URL}/api/callback"
        }

        r = requests.post(PAY_URL, data=payload, timeout=10).json()

        log(invoice, "PAYMENT_INITIATED", r)

        return jsonify(r)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================
# CALLBACK (NEVER TRUSTED)
# =====================
@app.route("/api/callback")
def callback():
    invoice = request.args.get("invoice_number")

    if not invoice:
        return "bad request", 400

    orders.update_one(
        {"invoice": invoice},
        {"$set": {"status": "PROCESSING"}}
    )

    log(invoice, "CALLBACK_RECEIVED", dict(request.args))

    verify_payment(invoice)

    return "OK"


# =====================
# PAYMENT VERIFICATION (SOURCE OF TRUTH)
# =====================
def verify_payment(invoice):
    try:
        res = requests.post(
            STATUS_URL,
            data={
                "merchantId": MERCHANT_ID,
                "invoice_number": invoice
            },
            timeout=10
        ).json()

        data = res.get("data", {})
        status = data.get("trx_status")

        if status == "success":
            final_status = "SUCCESS"
        elif status == "failed":
            final_status = "FAILED"
        else:
            final_status = "PROCESSING"

        orders.update_one(
            {"invoice": invoice},
            {"$set": {
                "status": final_status,
                "verified": True,
                "locked": True
            }}
        )

        log(invoice, "VERIFIED", data)

    except Exception as e:
        log(invoice, "VERIFY_ERROR", str(e))


# =====================
# ORDER STATUS API (FOR FRONTEND POLLING)
# =====================
@app.route("/api/order/<invoice>")
def get_order(invoice):
    order = orders.find_one({"invoice": invoice}, {"_id": 0})
    if not order:
        return jsonify({"error": "not found"}), 404
    return jsonify(order)


 