import { MongoClient } from "mongodb";
import fetch from "node-fetch";

const PRODUCTS = { p1: 1200, p2: 2200, p3: 3200 };

export default async function handler(req, res) {
  const { cart, total } = req.body;

  let verified = 0;
  for (let id of cart) verified += PRODUCTS[id];

  if (verified !== total)
    return res.status(400).json({ error: "Tampered price" });

  const invoice = "INV" + Date.now();

  const client = new MongoClient(process.env.MONGO_URI);
  await client.connect();
  await client.db("paystation").collection("orders").insertOne({
    invoice_number: invoice,
    cart,
    amount: verified,
    status: "pending",
  });

  const ps = await fetch("https://sandbox.paystation.com.bd/initiate-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      merchant_id: process.env.MERCHANT_ID,
      password: process.env.PAYSTATION_PASSWORD,
      invoice_number: invoice,
      amount: verified,
      currency: "BDT",
      callback_url: process.env.BASE_URL + "/api/payment-callback",
    }),
  });

  const data = await ps.json();

  res.json({ payment_url: data.payment_url });
}