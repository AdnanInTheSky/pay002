import os
import uuid
import traceback
import requests
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MERCHANT_ID = os.getenv("MERCHANT_ID")
PAYSTATION_PASSWORD = os.getenv("PAYSTATION_PASSWORD")
BASE_URL = os.getenv("BASE_URL")

app = Flask(__name__, template_folder="../templates")

# ----- Mongo (single DB from URI) -----
client = MongoClient(MONGO_URI)
db = client.get_default_database()
orders = db["orders"]

# ----- Product truth (backend authority) -----
PRODUCTS = {
    "p1": {"name": "Product 1", "price": 500},
    "p2": {"name": "Product 2", "price": 800},
    "p3": {"name": "Product 3", "price": 1200},
}

# ----- Home -----
@app.route("/")
def home():
    return render_template("index.html", products=PRODUCTS)

# ----- Create Order (verify cart) -----
@app.route("/api/create-order", methods=["POST"])
def create_order():
    try:
        data = request.get_json()

        items = data["items"]
        frontend_total = int(data["frontend_total"])

        # Recalculate from backend truth
        server_total = 0
        for item in items:
            pid = item["id"]
            qty = int(item["qty"])

            if pid not in PRODUCTS:
                return jsonify({"error": "Invalid product"}), 400

            server_total += PRODUCTS[pid]["price"] * qty

        # Reject tampered price
        if server_total != frontend_total:
            return jsonify({
                "error": "Price mismatch",
                "server_total": server_total
            }), 400

        invoice = str(uuid.uuid4())[:12]

        orders.insert_one({
            "invoice": invoice,
            "items": items,
            "amount": server_total,
            "status": "initiated"
        })

        payload = {
            "merchantId": MERCHANT_ID,
            "password": PAYSTATION_PASSWORD,
            "invoice_number": invoice,
            "currency": "BDT",
            "payment_amount": server_total,
            "cust_name": data["name"],
            "cust_phone": data["phone"],
            "cust_email": data["email"],
            "callback_url": f"{BASE_URL}/api/payment-callback",
        }

        ps = requests.post(
            "https://sandbox.paystation.com.bd/initiate-payment",
            data=payload
        ).json()

        return jsonify(ps)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ----- Callback (most important) -----
@app.route("/api/payment-callback")
def payment_callback():
    status = request.args.get("status")
    invoice = request.args.get("invoice_number")
    trx_id = request.args.get("trx_id")

    orders.update_one(
        {"invoice": invoice},
        {"$set": {"status": status, "trx_id": trx_id}}
    )

    return "OK"

# ----- Debug -----
@app.route("/api/orders")
def all_orders():
    return jsonify(list(orders.find({}, {"_id": 0})))