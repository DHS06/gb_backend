# app.py
import os
import sys
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
    # don't crash; just warn. Uploads will fail if not configured.
    logger.warning("Cloudinary environment variables missing or incomplete. Image uploads will fail.")


# ---------- MongoDB / Atlas connection ----------
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "GreenBuddyDB")

if not MONGO_URI:
    logger.error("MONGO_URI environment variable not set. Set it to your Atlas connection string.")
    # Stop the process so Render shows failing deploy (preferred), uncomment to exit:
    # sys.exit("MONGO_URI environment variable not set!")

try:
    # Recommended options for Atlas (pymongo will parse mongodb+srv URIs)
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    # Force a small server selection to surface connection errors early
    client.server_info()
    db = client[MONGO_DB_NAME]
    logger.info(f"Connected to MongoDB database: {MONGO_DB_NAME}")
except Exception as e:
    # Log full error so you can debug in Render logs
    logger.exception("Failed to connect to MongoDB. Check MONGO_URI, network access, and Atlas user/whitelist.")
    # Re-raise so Render marks the deploy as failed (recommended)
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


@app.route("/add_plant", methods=["POST"])
def add_plant():
    try:
        firebase_uid = request.form.get("uid")
        plant_name = request.form.get("plantName")
        plant_type = request.form.get("plantType")
        last_watered_date_str = request.form.get("lastWateredDate")
        last_fertilized_date_str = request.form.get("lastFertilizedDate")
        last_rePotted_date_str = request.form.get("lastRepottedDate")

        care_guide_id = None
        if plant_name:
            care_guide_document = care_guide_collection.find_one({
                "plant_name": {"$regex": f"^{plant_name.strip()}$", "$options": "i"}
            })
            if care_guide_document:
                care_guide_id = care_guide_document["_id"]

        if not all([firebase_uid, plant_name, plant_type, last_watered_date_str, last_fertilized_date_str, last_rePotted_date_str]):
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

        full_image_url = None
        if "plantImage" in request.files:
            file = request.files["plantImage"]
            if file and file.filename:
                if cloud_name and cloud_api_key and cloud_api_secret:
                    upload_result = cloudinary.uploader.upload(file)
                    full_image_url = upload_result.get("secure_url")
                else:
                    logger.warning("Skipping Cloudinary upload because credentials are missing.")

        rules = plant_care_rules_collection.find_one({"plantType": plant_type})
        if not rules:
            return jsonify({"status": "error", "message": f"Care rules for plant type '{plant_type}' not found."}), 404

        watering_freq_days = rules.get("wateringFrequencyDays", 7)
        fertilizing_freq_days = rules.get("fertilizingFrequencyDays", 30)
        repotting_freq_months = rules.get("repottingFrequencyMonths", 12)

        last_watered_date_obj = datetime.fromisoformat(last_watered_date_str.replace("Z", "+00:00"))
        last_fertilized_date_obj = datetime.fromisoformat(last_fertilized_date_str.replace("Z", "+00:00"))
        last_rePotted_date_obj = datetime.fromisoformat(last_rePotted_date_str.replace("Z", "+00:00"))

        date_acquired_str = request.form.get("dateAcquired")
        date_acquired_obj = None
        if date_acquired_str:
            date_acquired_obj = datetime.fromisoformat(date_acquired_str.replace("Z", "+00:00"))

        next_watering_date = last_watered_date_obj + timedelta(days=watering_freq_days)
        next_fertilizing_date = last_fertilized_date_obj + timedelta(days=fertilizing_freq_days)
        next_repotting_date = last_rePotted_date_obj + relativedelta(months=repotting_freq_months)

        plant_data = {
            "uid": firebase_uid,
            "plantName": plant_name,
            "plantType": plant_type,
            "dateAcquired": date_acquired_obj,
            "soilType": request.form.get("soilType"),
            "potType": request.form.get("potType"),
            "potSize": request.form.get("potSize"),
            "careNotes": request.form.get("careNotes"),
            "photo_url": full_image_url,
            "lastWateredDate": last_watered_date_obj,
            "lastFertilizedDate": last_fertilized_date_obj,
            "lastRepottedDate": last_rePotted_date_obj,
            "nextWateringDate": next_watering_date,
            "nextFertilizingDate": next_fertilizing_date,
            "nextRepottingDate": next_repotting_date,
            "care_guide_id": care_guide_id
        }

        result = plant_collection.insert_one(plant_data)
        plant_data["_id"] = result.inserted_id
        serialized_plant = serialize_plant_doc(plant_data)

        return jsonify({"status": "success", "message": "Plant added successfully!", "plant": serialized_plant}), 201

    except Exception as e:
        logger.exception("Error in add_plant")
        return jsonify({"status": "error", "message": "An internal server error occurred", "details": str(e)}), 500


@app.route("/garden/<uid>", methods=["GET"])
def get_garden(uid):
    try:
        plants_cursor = list(plant_collection.find({"uid": uid}))
        serialized_plants = [serialize_plant_doc(p) for p in plants_cursor]
        return jsonify(serialized_plants)
    except Exception as e:
        logger.exception("Error in get_garden")
        return jsonify({"error": str(e)}), 500


# (other routes remain unchanged) ...
# You can keep rest of routes (reminders, update, delete, care_guide, etc.) as in your original file.

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
