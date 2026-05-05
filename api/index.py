import os
import uuid
import requests
from flask import Flask, request, jsonify
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)

# =====================
# CONFIG (SAFE LOAD)
# =====================
MONGO_URI = os.environ.get("MONGO_URI")
MERCHANT_ID = os.environ.get("MERCHANT_ID")
PASSWORD = os.environ.get("PAYSTATION_PASSWORD")
BASE_URL = os.environ.get("BASE_URL")

PAY_URL = "https://sandbox.paystation.com.bd/initiate-payment"
STATUS_URL = "https://sandbox.paystation.com.bd/transaction-status"

# =====================
# DB (LAZY INIT FIX)
# =====================
client = None
db = None
orders = None
logs = None


def init_db():
    global client, db, orders, logs

    if client is None:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client["paystation_demo"]
        orders = db["orders"]
        logs = db["payment_logs"]

        # SAFE: only ensure index once per cold start
        try:
            orders.create_index("invoice", unique=True)
        except:
            pass


# =====================
# PRODUCTS (LOCKED PRICES)
# =====================
PRODUCTS = {
    "p1": 5,
    "p2": 7,
    "p3": 10
}


# =====================
# PRICE ENGINE (ANTI MANIPULATION)
# =====================
def calc(items):
    total = 0

    for i in items:
        pid = i.get("id")
        qty = int(i.get("qty", 0))

        if pid not in PRODUCTS:
            raise Exception("Invalid product")

        if qty <= 0 or qty > 100:
            raise Exception("Invalid quantity")

        total += PRODUCTS[pid] * qty

    return total


# =====================
# LOGGING
# =====================
def log(invoice, event, data=None):
    init_db()
    logs.insert_one({
        "invoice": invoice,
        "event": event,
        "data": data,
        "time": datetime.utcnow()
    })


# =====================
# CREATE ORDER
# =====================
@app.route("/api/create-order", methods=["POST"])
def create_order():
    init_db()

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
        "created_at": datetime.utcnow(),
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

    log(invoice, "PAYMENT_INIT", r)

    return jsonify(r)


# =====================
# CALLBACK (UNTRUSTED)
# =====================
@app.route("/api/callback")
def callback():
    init_db()

    invoice = request.args.get("invoice_number")

    if not invoice:
        return "bad request", 400

    orders.update_one(
        {"invoice": invoice},
        {"$set": {"status": "PROCESSING"}}
    )

    log(invoice, "CALLBACK", dict(request.args))

    verify(invoice)

    return "OK"


# =====================
# VERIFY PAYMENT
# =====================
def verify(invoice):
    init_db()

    try:
        res = requests.post(
            STATUS_URL,
            data={
                "merchantId": MERCHANT_ID,
                "invoice_number": invoice
            },
            timeout=10
        ).json()

        status = res.get("data", {}).get("trx_status")

        final = "PENDING"

        if status == "success":
            final = "SUCCESS"
        elif status == "failed":
            final = "FAILED"

        orders.update_one(
            {"invoice": invoice},
            {"$set": {
                "status": final,
                "verified": True
            }}
        )

        log(invoice, "VERIFIED", res)

    except Exception as e:
        log(invoice, "VERIFY_ERROR", str(e))


# =====================
# GET ORDER
# =====================
@app.route("/api/order/<invoice>")
def get_order(invoice):
    init_db()

    order = orders.find_one({"invoice": invoice}, {"_id": 0})

    if not order:
        return jsonify({"error": "not found"}), 404

    return jsonify(order)