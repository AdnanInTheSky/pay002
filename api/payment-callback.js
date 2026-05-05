import { MongoClient } from "mongodb";

export default async function handler(req, res) {
  const { status, invoice_number, trx_id } = req.query;

  if (status === "Success") {
    const client = new MongoClient(process.env.MONGO_URI);
    await client.connect();

    await client.db("paystation").collection("orders").updateOne(
      { invoice_number },
      { $set: { status: "paid", trx_id } }
    );
  }

  res.send("OK");
}