from pymongo import MongoClient
from pprint import pprint

# Use your exact URI (DB name included for safety)
MONGO_URI = "mongodb+srv://adnan:aFdbDSQzHk8G4cs6@cluster0.7fvc3no.mongodb.net/paystation_demo?retryWrites=true&w=majority"

client = MongoClient(MONGO_URI)
db = client["paystation_demo"]
orders = db["orders"]

# Fetch all documents, newest first, limit to 50 to avoid console spam
all_orders = orders.find().sort("_id", -1).limit(50)

print("📦 ALL ORDERS IN DATABASE (newest first):\n")
count = 0
for order in all_orders:
    count += 1
    print(f"--- Order #{count} ---")
    pprint(order)
    print("-" * 40)

if count == 0:
    print("⚠️ No orders found in the database.")
else:
    print(f"\n✅ Displayed {count} order(s).")