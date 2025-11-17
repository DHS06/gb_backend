# app.py
import os
import sys
import re
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask_cors import CORS
from bson import ObjectId
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import cloudinary
import cloudinary.uploader
import logging

# load .env for local development
load_dotenv()

# configure logging so Render logs show clear errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


# ---------- Cloudinary config (safe) ----------
cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
cloud_api_key = os.getenv("CLOUDINARY_API_KEY")
cloud_api_secret = os.getenv("CLOUDINARY_API_SECRET")

if cloud_name and cloud_api_key and cloud_api_secret:
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=cloud_api_key,
        api_secret=cloud_api_secret
    )
    logger.info("Cloudinary configured.")
else:
    logger.warning("Cloudinary environment variables missing or incomplete. Image uploads will fail.")


# ---------- MongoDB / Atlas connection ----------
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "GreenBuddyDB")

if not MONGO_URI:
    logger.error("MONGO_URI environment variable not set. Set it to your Atlas connection string.")

try:
    # Recommended options for Atlas (pymongo will parse mongodb+srv URIs)
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    # Force a small server selection to surface connection errors early
    client.server_info()
    db = client[MONGO_DB_NAME]
    logger.info(f"Connected to MongoDB database: {MONGO_DB_NAME}")
except Exception as e:
    logger.exception("Failed to connect to MongoDB. Check MONGO_URI, network access, and Atlas user/whitelist.")
    raise


# ---------- Collections ----------
users_collection = db["users"]
plant_collection = db["plants"]
plant_care_rules_collection = db["plant_care_rules"]
reminders_collection = db["reminders"]
care_guide_collection = db["care_guide_data"]


# ---------- Helpers ----------
def serialize_plant_doc(plant):
    if not plant:
        return None

    for key, value in list(plant.items()):
        if isinstance(value, datetime):
            plant[key] = value.isoformat()

    if "_id" in plant and isinstance(plant["_id"], ObjectId):
        plant["_id"] = str(plant["_id"])

    if "care_guide_id" in plant and isinstance(plant["care_guide_id"], ObjectId):
        plant["care_guide_id"] = str(plant["care_guide_id"])

    return plant


# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    """Health check endpoint"""
    return jsonify({
        "status": "ok",
        "message": "GreenBuddy Backend API is running",
        "endpoints": {
            "health": "/",
            "add_plant": "/add_plant",
            "get_garden": "/garden/<uid>",
            "search": "/search",
            "add_care_guide": "/care_guide/add"
        }
    }), 200


@app.route("/users/create", methods=["POST"])
def create_user_profile():
    try:
        data = request.get_json()
        uid = data.get("uid")
        username = data.get("username")
        email = data.get("email")

        if not uid:
            return jsonify({"error": "Missing user ID (uid)"}), 400

        if users_collection.find_one({"uid": uid}):
            return jsonify({"message": "User profile already exists"}), 200

        user_document = {"uid": uid, "username": username, "email": email, "created_at": datetime.utcnow()}
        users_collection.insert_one(user_document)
        return jsonify({"status": "success", "message": "User profile created"}), 201

    except Exception as e:
        logger.exception("Error in create_user_profile")
        return jsonify({"error": str(e)}), 500


# Allowed care categories
SUPPORTED_TYPES = {
    "indoor": "Indoor",
    "outdoor": "Outdoor",
    "flower": "Flower",
    "vegetable": "Vegetable",
    "herbs": "Herbs",
    "cactus": "Cactus"
}

# --- Utility: Normalize plant type ---
def normalize_type(text):
    """Cleans and normalizes text for flexible matching"""
    if not text:
        return None

    text = text.lower()

    # Remove emojis & non-letters
    text = re.sub(r'[^a-z]', ' ', text)

    # Remove extra spaces
    text = " ".join(text.split())

    # Remove plural forms (plants → plant)
    if text.endswith("s"):
        text = text[:-1]

    return text


# --- Utility: Resolve best match type ---
def resolve_type(raw_text):
    cleaned = normalize_type(raw_text)

    if not cleaned:
        return None

    # Try exact keys
    for key in SUPPORTED_TYPES.keys():
        if cleaned == key:
            return SUPPORTED_TYPES[key]

    # Try partial matches
    for key in SUPPORTED_TYPES.keys():
        if key in cleaned:
            return SUPPORTED_TYPES[key]

    return None


# ================================
#       ROUTE: ADD PLANT
# ================================
@app.route("/add_plant", methods=["POST"])
def add_plant():
    try:
        # ---- Get form fields exactly like Flutter sends ----
        user_id = request.form.get("uid")
        plant_name = request.form.get("plantName")
        
        raw_type = request.form.get("plantType")  # value from Flutter
        plant_type = resolve_type(raw_type)       # normalize and clean type
        
        last_watered = request.form.get("lastWateredDate")
        last_fertilized = request.form.get("lastFertilizedDate")
        last_repotted = request.form.get("lastRepottedDate")

        # ---- Validate required fields ----
        if not user_id or not plant_name or not raw_type:
            return jsonify({"status": "error", "message": "Missing required fields."}), 400

        # ---- Validate resolved plant type ----
        if not plant_type:
            return jsonify({
                "status": "error",
                "message": f"Unsupported or unknown plant type '{raw_type}'"
            }), 400

        print("🌱 RAW TYPE:", raw_type)
        print("🌿 RESOLVED TYPE:", plant_type)

        # ---- Fetch care rules (case-insensitive) ----
        rules = plant_care_rules_collection.find_one(
            {"plantType": {"$regex": f"^{plant_type}$", "$options": "i"}}
        )

        if not rules:
            return jsonify({
                "status": "error",
                "message": f"Care rules for plant type '{plant_type}' not found."
            }), 404

        # ---- Handle Image Upload ----
        image_url = None
        if "plantImage" in request.files:
            image = request.files["plantImage"]
            filename = secure_filename(image.filename)
            path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            image.save(path)
            image_url = f"/{path}"

        # ---- Insert Into Database ----
        plant = {
            "userId": user_id,
            "plantName": plant_name,
            "plantType": plant_type,
            "imageUrl": image_url,
            "lastWateredDate": last_watered,
            "lastFertilizedDate": last_fertilized,
            "lastRepottedDate": last_repotted,
            "wateringFrequencyDays": rules.get("wateringFrequencyDays"),
            "fertilizingFrequencyDays": rules.get("fertilizingFrequencyDays"),
            "repottingFrequencyMonths": rules.get("repottingFrequencyMonths"),
            "sunlightNeeds": rules.get("sunlightNeeds", ""),
            "createdAt": datetime.utcnow()
        }

        result = plant_collection.insert_one(plant)
        plant["_id"] = str(result.inserted_id)

        return jsonify({"status": "success", "message": "Plant added successfully!", "data": plant}), 201

    except Exception as e:
        print("❌ ERROR ADDING PLANT:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/garden/<uid>", methods=["GET"])
def get_garden(uid):
    try:
        plants_cursor = list(plant_collection.find({"uid": uid}))
        serialized_plants = [serialize_plant_doc(p) for p in plants_cursor]
        return jsonify(serialized_plants)
    except Exception as e:
        logger.exception("Error in get_garden")
        return jsonify({"error": str(e)}), 500


# ✅ NEW: Search endpoint for care guides
@app.route("/search", methods=["GET"])
def search_plants():
    """
    Search for plants in care guide database
    """
    try:
        query = request.args.get("query", "").strip()
        
        if not query or len(query) < 2:
            return jsonify([]), 200
        
        # Search in care guide collection
        results = care_guide_collection.find({
            "plant_name": {"$regex": query, "$options": "i"}
        }).limit(10)
        
        plants = []
        for doc in results:
            plants.append({
                "plant_name": doc.get("plant_name"),
                "scientific_name": doc.get("scientific_name", ""),
                "image_url": doc.get("image_url", ""),
                "watering_schedule": doc.get("watering_schedule", ""),
                "sunlight_needs": doc.get("sunlight_needs", ""),
                "soil_type": doc.get("soil_type", ""),
                "fertilizer_tips": doc.get("fertilizer_tips", "")
            })
        
        return jsonify(plants), 200
        
    except Exception as e:
        logger.exception("Error in search_plants")
        return jsonify({"error": str(e)}), 500


# ✅ NEW: Add care guide endpoint
@app.route("/care_guide/add", methods=["POST"])
def add_care_guide():
    """
    Add a new plant care guide to the community database
    """
    try:
        # Get form data
        plant_name = request.form.get("plant_name")
        scientific_name = request.form.get("scientific_name", "")
        watering_schedule = request.form.get("watering_schedule")
        sunlight_needs = request.form.get("sunlight_needs")
        soil_type = request.form.get("soil_type")
        fertilizer_tips = request.form.get("fertilizer_tips")
        
        logger.info(f"Received care guide request for: {plant_name}")
        
        # Validate required fields
        if not all([plant_name, watering_schedule, sunlight_needs, soil_type, fertilizer_tips]):
            missing = []
            if not plant_name: missing.append("plant_name")
            if not watering_schedule: missing.append("watering_schedule")
            if not sunlight_needs: missing.append("sunlight_needs")
            if not soil_type: missing.append("soil_type")
            if not fertilizer_tips: missing.append("fertilizer_tips")
            
            return jsonify({
                "error": "Missing required fields",
                "missing_fields": missing
            }), 400
        
        # Handle image upload
        image_url = None
        if "image" in request.files:
            file = request.files["image"]
            if file and file.filename:
                if cloud_name and cloud_api_key and cloud_api_secret:
                    try:
                        upload_result = cloudinary.uploader.upload(file)
                        image_url = upload_result.get("secure_url")
                        logger.info(f"Image uploaded successfully: {image_url}")
                    except Exception as upload_error:
                        logger.error(f"Cloudinary upload failed: {upload_error}")
                        return jsonify({"error": "Image upload failed"}), 500
                else:
                    logger.warning("Cloudinary credentials missing")
                    return jsonify({"error": "Image upload not configured"}), 500
        else:
            return jsonify({"error": "Image is required"}), 400
        
        # Check if plant already exists in care guide
        existing = care_guide_collection.find_one({
            "plant_name": {"$regex": f"^{plant_name.strip()}$", "$options": "i"}
        })
        
        if existing:
            return jsonify({
                "error": f"Care guide for '{plant_name}' already exists in database"
            }), 409
        
        # Create care guide document
        care_guide_data = {
            "plant_name": plant_name.strip(),
            "scientific_name": scientific_name.strip(),
            "watering_schedule": watering_schedule.strip(),
            "sunlight_needs": sunlight_needs.strip(),
            "soil_type": soil_type.strip(),
            "fertilizer_tips": fertilizer_tips.strip(),
            "image_url": image_url,
            "created_at": datetime.utcnow(),
            "status": "active"
        }
        
        # Insert into database
        result = care_guide_collection.insert_one(care_guide_data)
        logger.info(f"Care guide added: {plant_name} (ID: {result.inserted_id})")
        
        return jsonify({
            "status": "success",
            "message": "Care guide added successfully",
            "plant_name": plant_name,
            "id": str(result.inserted_id)
        }), 201
        
    except Exception as e:
        logger.exception("Error in add_care_guide")
        return jsonify({
            "status": "error",
            "error": "Internal server error",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
