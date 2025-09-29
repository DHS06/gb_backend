from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import bcrypt

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
    """Convert MongoDB ObjectId and datetime to string for JSON"""
    doc = dict(doc)  # make a copy
    if '_id' in doc:
        doc['_id'] = str(doc['_id'])
    if 'created_at' in doc and isinstance(doc['created_at'], datetime):
        doc['created_at'] = doc['created_at'].isoformat()
    return doc


# ------------------ Home Route ------------------
@app.route('/')
def home():
    return "Welcome to GreenBuddy API!"


# ------------------ User Routes ------------------
@app.route('/register', methods=['POST'])
def register_user():
    data = request.json
    if not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password required"}), 400

    existing = users_collection.find_one({"email": data["email"]})
    if existing:
        return jsonify({"error": "User already exists"}), 400

    # Hash password before storing
    hashed_pw = bcrypt.hashpw(data["password"].encode('utf-8'), bcrypt.gensalt())
    data["password"] = hashed_pw
    users_collection.insert_one(data)
    return jsonify({"message": "User registered successfully"}), 201


@app.route('/login', methods=['POST'])
def login_user():
    data = request.json
    user = users_collection.find_one({"email": data.get("email")})
    if not user or not bcrypt.checkpw(data.get("password", "").encode('utf-8'), user['password']):
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify({"message": "Login successful", "user": serialize_doc(user)}), 200


# ------------------ User Plants (My Garden) ------------------

#  Add new plant
@app.route('/garden/add', methods=['POST'])
def add_plant():
    try:
        data = request.json
        required_fields = ["user_id", "plant_name", "plant_type", "date_acquired", "watering_frequency"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"{field} is required"}), 400

        plant = {
            "user_id": data["user_id"],
            "photo_url": data.get("photo_url", ""),  # optional
            "plant_name": data["plant_name"],
            "plant_type": data["plant_type"],  # dropdown (indoor/outdoor/flower)
            "date_acquired": data["date_acquired"],  # store as string (YYYY-MM-DD)
            "watering_frequency": data["watering_frequency"],  # dropdown
            "soil_type": data.get("soil_type", ""),
            "pot_type": data.get("pot_type", ""),
            "pot_size": data.get("pot_size", ""),
            "care_notes": data.get("care_notes", "")
        }

        result = user_plants_collection.insert_one(plant)
        return jsonify({"message": "Plant added successfully", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#  Get all plants in user's garden
@app.route('/garden/<user_id>', methods=['GET'])
def get_garden(user_id):
    try:
        plants = list(user_plants_collection.find({"user_id": user_id}))
        for plant in plants:
            plant["_id"] = str(plant["_id"])
        return jsonify(plants), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#  Remove a plant
@app.route('/garden/delete/<plant_id>', methods=['DELETE'])
def delete_plant(plant_id):
    try:
        result = user_plants_collection.delete_one({"_id": ObjectId(plant_id)})
        if result.deleted_count == 1:
            return jsonify({"message": "Plant deleted successfully"}), 200
        else:
            return jsonify({"error": "Plant not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#  Update plant details (optional if user edits later)
@app.route('/garden/update/<plant_id>', methods=['PUT'])
def update_plant(plant_id):
    try:
        data = request.json
        update_data = {k: v for k, v in data.items() if v is not None}

        result = user_plants_collection.update_one(
            {"_id": ObjectId(plant_id)},
            {"$set": update_data}
        )
        if result.matched_count == 0:
            return jsonify({"error": "Plant not found"}), 404
        return jsonify({"message": "Plant updated successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500



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


# ------------------ Care Guide (Safe Search) ------------------
@app.route('/search', methods=['GET'])
def search_plants():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([]), 200

    try:
        # Build a case-insensitive regex safely
        regex_query = {"$regex": query, "$options": "i"}

        # Search in plant_name and scientific_name
        results = care_guide_collection.find(
            {
                "$or": [
                    {"plant_name": regex_query},
                    {"scientific_name": regex_query}
                ]
            },
            {"_id": 0}  # exclude MongoDB ID from results
        )

        # Convert cursor to list safely
        plant_list = list(results)

        # Return empty list if nothing found
        return jsonify(plant_list), 200

    except Exception as e:
        # Catch all errors and return JSON instead of crashing
        return jsonify({
            "error": "Server error occurred during search",
            "details": str(e)
        }), 500



# ------------------ Main ------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)), debug=True)
