
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

# ------------------ Database Connection ------------------
MONGO_URI = os.getenv("MONGO_URI", "your-mongodb-connection-string")
client = MongoClient(MONGO_URI)
db = client['greenbuddy']

# Collections
users_collection = db['users']
user_plants_collection = db['user_plants']
reminders_collection = db['reminders']
care_guide_collection = db['care_guide']


# ------------------ Helper ------------------
def serialize_doc(doc):
    """Convert MongoDB ObjectId to string"""
    doc['_id'] = str(doc['_id'])
    return doc


# ------------------ User Routes ------------------
@app.route('/register', methods=['POST'])
def register_user():
    data = request.json
    if not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password required"}), 400

    existing = users_collection.find_one({"email": data["email"]})
    if existing:
        return jsonify({"error": "User already exists"}), 400

    users_collection.insert_one(data)
    return jsonify({"message": "User registered successfully"}), 201


@app.route('/login', methods=['POST'])
def login_user():
    data = request.json
    user = users_collection.find_one({"email": data["email"], "password": data["password"]})
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify({"message": "Login successful", "user": serialize_doc(user)}), 200


# ------------------ User Plants ------------------
@app.route('/add_plant', methods=['POST'])
def add_plant():
    data = request.json
    if not data.get("user_id") or not data.get("plant_name"):
        return jsonify({"error": "user_id and plant_name required"}), 400

    user_plants_collection.insert_one(data)
    return jsonify({"message": "Plant added successfully"}), 201


@app.route('/my_garden/<user_id>', methods=['GET'])
def get_my_garden(user_id):
    plants = user_plants_collection.find({"user_id": user_id})
    return jsonify([serialize_doc(p) for p in plants]), 200


# ------------------ Reminders ------------------
@app.route('/add_reminder', methods=['POST'])
def add_reminder():
    data = request.json
    if not data.get("user_id") or not data.get("reminder_text"):
        return jsonify({"error": "user_id and reminder_text required"}), 400

    data["created_at"] = datetime.utcnow()
    reminders_collection.insert_one(data)
    return jsonify({"message": "Reminder added successfully"}), 201


@app.route('/reminders/<user_id>', methods=['GET'])
def get_reminders(user_id):
    reminders = reminders_collection.find({"user_id": user_id})
    return jsonify([serialize_doc(r) for r in reminders]), 200


# ------------------ Care Guide ------------------
@app.route('/search', methods=['GET'])
def search_plants():
    query = request.args.get('query', '')
    if not query:
        return jsonify([]), 200

    # Search in both plant_name and scientific_name
    results = care_guide_collection.find(
        {
            "$or": [
                {"plant_name": {"$regex": query, "$options": "i"}},
                {"scientific_name": {"$regex": query, "$options": "i"}}
            ]
        },
        {"_id": 0}  # exclude MongoDB ID
    )
    return jsonify(list(results)), 200


# ------------------ Main ------------------
if __name__ == '__main__':
    app.run(debug=True)

