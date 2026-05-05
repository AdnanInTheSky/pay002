import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIG
# =========================
MONGO_URI = os.getenv("MONGO_URI")
MERCHANT_ID = os.getenv("MERCHANT_ID")
PASSWORD = os.getenv("PAYSTATION_PASSWORD")
BASE_URL = os.getenv("BASE_URL")

PAY_URL = "https://api.paystation.com.bd/initiate-payment"
STATUS_URL = "https://api.paystation.com.bd/transaction-status"

app = Flask(__name__, template_folder="../templates")

# =========================
# DB
# =========================
client = MongoClient(MONGO_URI)
db = client.get_default_database()
orders = db["orders"]

# =========================
# PRODUCTS
# =========================
PRODUCTS = {
    "p1": 5,
    "p2": 7,
    "p3": 10
}

# =========================
# PRICE ENGINE (TRUTH)
# =========================
def calc(items):
    total = 0
    for i in items:
        pid = i["id"]
        qty = int(i["qty"])

        if pid not in PRODUCTS:
            raise Exception("invalid product")

        total += PRODUCTS[pid] * qty

    return total


# =========================
# HOME
# =========================
@app.route("/")
def home():
    return render_template("index.html")


# =========================
# CREATE ORDER
# =========================
@app.route("/api/create-order", methods=["POST"])
def create_order():
    try:
        data = request.get_json()

        items = data.get("items", [])
        amount = calc(items)

        invoice = str(uuid.uuid4())

        order = {
            "invoice": invoice,
            "items": items,
            "amount": amount,
            "status": "initiated",
            "verified": False,
            "customer": {
                "name": data.get("name"),
                "email": data.get("email"),
                "phone": data.get("phone"),
                "address": data.get("address")
            }
        }

        orders.insert_one(order)

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
            "callback_url": f"{BASE_URL}/api/payment-callback"
        }

        res = requests.post(PAY_URL, data=payload, timeout=10).json()

        return jsonify(res)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# VERIFY PAYMENT
# =========================
def verify(invoice):
    try:
        r = requests.post(
            STATUS_URL,
            data={
                "merchantId": MERCHANT_ID,
                "invoice_number": invoice
            },
            timeout=10
        ).json()

        data = r.get("data", {})
        status = data.get("trx_status")

        update = {"verified": True}

        if status == "success":
            update["status"] = "success"
        else:
            update["status"] = "failed"

        orders.update_one({"invoice": invoice}, {"$set": update})

    except:
        pass


# =========================
# CALLBACK (UNTRUSTED)
# =========================
@app.route("/api/payment-callback")
def callback():
    invoice = request.args.get("invoice_number")

    if not invoice:
        return "bad request", 400

    orders.update_one(
        {"invoice": invoice},
        {"$set": {"status": "verifying"}}
    )

    verify(invoice)

    return "OK"


# =========================
# DEBUG
# =========================
@app.route("/api/orders")
def get_orders():
    return jsonify(list(orders.find({}, {"_id": 0})))