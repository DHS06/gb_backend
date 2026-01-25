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


#  load .env for local development
load_dotenv()

# configure logging so Render logs show clear errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

#configure cloudinary

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


#db connection

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


def serialize_plant_doc(plant):
    
    if not plant:
        return None
    
    
    for key, value in plant.items():
        if isinstance(value, datetime):
            plant[key] = value.isoformat()
    
    
    if '_id' in plant and isinstance(plant['_id'], ObjectId):
        plant['_id'] = str(plant['_id'])


    if 'care_guide_id' in plant and isinstance(plant['care_guide_id'], ObjectId):
        plant['care_guide_id'] = str(plant['care_guide_id'])
    
        
    return plant


@app.route('/users/create', methods=['POST'])
def create_user_profile():
    try:
        data = request.get_json()
        
        uid = data.get('uid')
        username = data.get('username')
        email = data.get('email')

        if not uid:
            return jsonify({"error": "Missing user ID (uid)"}), 400

        
        if users_collection.find_one({"uid": uid}):
            return jsonify({"message": "User profile already exists"}), 200

        #create new user document
        user_document = {
            "uid": uid,
            "username": username,
            "email": email,
            "created_at": datetime.utcnow()
            
        }

        #inserting document into collection
        users_collection.insert_one(user_document)

        return jsonify({"status": "success", "message": "User profile created"}), 201

    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"error": str(e)}), 500
    


#add a new plant
@app.route('/add_plant', methods=['POST'])
def add_plant():
    try:
       
        firebase_uid = request.form.get('uid')
        plant_name = request.form.get('plantName')
        plant_type = request.form.get('plantType') 
        last_watered_date_str = request.form.get('lastWateredDate') 
        last_fertilized_date_str = request.form.get('lastFertilizedDate')
        last_rePotted_date_str = request.form.get('lastRepottedDate')

        care_guide_id = None
        if plant_name:
            care_guide_document = care_guide_collection.find_one({
                "plant_name": {"$regex": f"^{plant_name.strip()}$", "$options": "i"}
            })
            
            if care_guide_document:
                care_guide_id = care_guide_document['_id']
        
        if not all([firebase_uid, plant_name, plant_type, last_watered_date_str,
                    last_fertilized_date_str, last_rePotted_date_str]):
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

        full_image_url = None
        if 'plantImage' in request.files:
            file = request.files['plantImage']
            if file.filename != '':
                upload_result = cloudinary.uploader.upload(file)
                full_image_url = upload_result.get('secure_url')

        rules = plant_care_rules_collection.find_one({"plantType": plant_type}) 

        if not rules:
            return jsonify({"status": "error", "message": f"Care rules for plant type '{plant_type}' not found."}), 404

        watering_freq_days = rules.get('wateringFrequencyDays', 7)
        fertilizing_freq_days = rules.get('fertilizingFrequencyDays', 30) 
        repotting_freq_months = rules.get('repottingFrequencyMonths', 12) 

        last_watered_date_obj = datetime.fromisoformat(last_watered_date_str.replace('Z', '+00:00')) 
        last_fertilized_date_obj = datetime.fromisoformat(last_fertilized_date_str.replace('Z', '+00:00'))
        last_rePotted_date_obj = datetime.fromisoformat(last_rePotted_date_str.replace('Z', '+00:00'))

        date_acquired_str = request.form.get('dateAcquired') 
        date_acquired_obj = None
        if date_acquired_str:
            date_acquired_obj = datetime.fromisoformat(date_acquired_str.replace('Z', '+00:00'))

        next_watering_date = last_watered_date_obj + timedelta(days=watering_freq_days) 
        next_fertilizing_date = last_fertilized_date_obj + timedelta(days=fertilizing_freq_days)
        next_repotting_date = last_rePotted_date_obj + relativedelta(months=repotting_freq_months)

        # ✅ ONLY CHANGE IS HERE
        plant_data = {
            "uid": firebase_uid,
            "plantName": plant_name,
            "plantType": plant_type,
            "dateAcquired": date_acquired_obj,
            "soilType": request.form.get('soilType'),
            "potType": request.form.get('potType'),
            "potSize": request.form.get('potSize'),
            "careNotes": request.form.get('careNotes'),
            "photo_url": full_image_url,

            "lastWateredDate": last_watered_date_obj,
            "lastFertilizedDate": last_fertilized_date_obj,
            "lastRepottedDate": last_rePotted_date_obj,
            "nextWateringDate": next_watering_date,
            "nextFertilizingDate": next_fertilizing_date,
            "nextRepottingDate": next_repotting_date,

            "care_guide_id": care_guide_id,

            # 🔹 NEW FIELDS (AUTO ADDED)
            "isArchived": False,
            "archivedAt": None
        }

        result = plant_collection.insert_one(plant_data)
        
        plant_data["_id"] = result.inserted_id
        serialized_plant = serialize_plant_doc(plant_data)

        return jsonify({
            "status": "success",
            "message": "Plant added successfully!",
            "plant": serialized_plant
        }), 201

    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500

    

#set new reminder
@app.route('/reminders/add', methods=['POST'])
def add_reminder():
    try:
       
        data = request.get_json()
        
        firebase_uid = data.get('uid')
        note = data.get('note')

        if not firebase_uid or not note:
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

       
        reminder_data = {
        "uid": firebase_uid,
        "plant_id": ObjectId(data.get("plant_id")),
        "note": note,
        "date": data.get('date'),
        "time": data.get('time'),
        "isActive": True
        }


       
        reminders_collection.insert_one(reminder_data)

        
        return jsonify({
            "status": "success",
            "message": "Reminder added successfully!"
        }), 201

    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500
    
#care guide screen
@app.route('/search', methods=['GET'])
def search_plants():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([]), 200

    try:
        
        regex_query = {"$regex": query, "$options": "i"}

       
        results = care_guide_collection.find(
            {
                "$or": [
                    {"plant_name": regex_query},
                    {"scientific_name": regex_query}
                ]
            },
            {"_id": 0} 
        ).collation({"locale": "en", "strength": 1})

        
        plant_list = list(results)

        return jsonify(plant_list), 200

    except Exception as e:
        return jsonify({
            "error": "Server error occurred during search",
            "details": str(e)
        }), 500
    

#my garden screen
@app.route("/garden/<uid>", methods=["GET"])
def get_garden(uid):
    try:
        plants_cursor = list(
            plant_collection.find({
                "uid": uid,
                "$or": [
                    {"isArchived": False},
                    {"isArchived": {"$exists": False}}
                ]
            })
        )

        serialized_plants = [
            serialize_plant_doc(p) for p in plants_cursor
        ]

        return jsonify(serialized_plants), 200

    except Exception as e:
        print(f"Error in get_garden: {e}")
        return jsonify({"error": "Server error"}), 500


    

@app.route("/plant/<plant_id>", methods=["GET"])
def get_plant_details(plant_id):
    try:
       
        plant = plant_collection.find_one({"_id": ObjectId(plant_id)})
        
        if not plant:
            return jsonify({"error": "Plant not found"}), 404
        
        serialized_plant = serialize_plant_doc(plant)
         
        return jsonify(serialized_plant), 200

    except Exception as e:
        print(f"Error in get_plant_details: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route("/plant/delete/<plant_id>", methods=["DELETE"])
def delete_plant(plant_id):
    try:
        result = plant_collection.update_one(
            {"_id": ObjectId(plant_id)},
            {"$set": {"isArchived": True}}
        )

        if result.matched_count == 1:
            return jsonify({
                "status": "success",
                "message": "Plant archived successfully"
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Plant not found"
            }), 404

    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({
            "status": "error",
            "message": "An internal server error occurred"
        }), 500


     



@app.route("/plant/update/<plant_id>", methods=["POST"])
def update_plant(plant_id):
    try:
        
        plant_name = request.form.get('plantName')

        
        care_guide_id = None  # Default to None
        if plant_name:
            
            care_guide_document = care_guide_collection.find_one({
                "plant_name": {"$regex": f"^{plant_name.strip()}$", "$options": "i"}
            })
            
            if care_guide_document:
                care_guide_id = care_guide_document['_id']
        

        plant_type = request.form.get('plantType')
        print("⬅️ Received plantType from request:", plant_type)

        last_watered_date_str = request.form.get('lastWateredDate')
        last_fertilized_date_str = request.form.get('lastFertilizedDate')
        last_rePotted_date_str = request.form.get('lastRepottedDate')

        

        if not all([plant_name, plant_type, last_watered_date_str,last_fertilized_date_str,last_rePotted_date_str]):
            return jsonify({"status": "error", "message": "Missing required fields (plantName, plantType, lastWateredDate)"}), 400

        
        rules = plant_care_rules_collection.find_one({
        "plantType": {"$regex": f"^{plant_type.strip()}$", "$options": "i"} 
        })
        if not rules:
            return jsonify({"status": "error", "message": f"Care rules for plant type '{plant_type}' not found."}), 404

        
        watering_freq_days = rules.get('wateringFrequencyDays', 7)
        fertilizing_freq_days = rules.get('fertilizingFrequencyDays', 30)
        repotting_freq_months = rules.get('repottingFrequencyMonths', 12)

        
        last_watered_date_obj = datetime.fromisoformat(last_watered_date_str.replace('Z', '+00:00'))
        last_fertilized_date_obj = datetime.fromisoformat(last_fertilized_date_str.replace('Z', '+00:00'))
        last_rePotted_date_obj = datetime.fromisoformat(last_rePotted_date_str.replace('Z', '+00:00'))

        date_acquired_str = request.form.get('dateAcquired')
        date_acquired_obj = None
        if date_acquired_str:
            date_acquired_obj = datetime.fromisoformat(date_acquired_str.replace('Z', '+00:00'))

        
        next_watering_date = last_watered_date_obj + timedelta(days=watering_freq_days)
        next_fertilizing_date = last_fertilized_date_obj + timedelta(days=fertilizing_freq_days)
        next_repotting_date = last_rePotted_date_obj + relativedelta(months=repotting_freq_months)
  
        
        update_data = {
            "plantName": plant_name,
            "plantType": plant_type,
            "dateAcquired": date_acquired_obj,
            "soilType": request.form.get('soilType'),
            "potType": request.form.get('potType'),
            "potSize": request.form.get('potSize'),
            "careNotes": request.form.get('careNotes'),
            
            
            "lastWateredDate": last_watered_date_obj,
            "lastFertilizedDate": last_fertilized_date_obj,
            "lastRepottedDate": last_rePotted_date_obj,
            "nextWateringDate": next_watering_date,
            "nextFertilizingDate": next_fertilizing_date,
            "nextRepottingDate": next_repotting_date,
            "care_guide_id": care_guide_id
        }

       
        if 'plantImage' in request.files:
            file = request.files['plantImage']
            if file.filename != '':
                upload_result = cloudinary.uploader.upload(file)
                update_data["photo_url"] = upload_result.get('secure_url')
                
        
       
        result = plant_collection.update_one(
            {"_id": ObjectId(plant_id)},
            {
                "$set": update_data,
                "$unset": {"wateringFrequency": ""} 
            }
        )

        if result.matched_count == 0:
            return jsonify({"status": "error", "message": "Plant not found"}), 404
        
       
        updated_plant = plant_collection.find_one({"_id": ObjectId(plant_id)})
        serialized_plant = serialize_plant_doc(updated_plant)

        return jsonify({"status": "success", 
                        "message": "Plant updated successfully",
                        "plant": serialized_plant 
                        }), 200

    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500
    

@app.route("/reminders/<uid>", methods=["GET"])
def get_reminders(uid):
    try:
        reminders = reminders_collection.find({
            "uid": uid,
            "$or": [
                {"isActive": True},
                {"isActive": {"$exists": False}}
            ]
        })

        result = []
        for r in reminders:
            r["_id"] = str(r["_id"])
            result.append(r)

        return jsonify(result), 200

    except Exception as e:
        print(e)
        return jsonify({"error": "Server error"}), 500

    
@app.route('/users/update_profile_pic', methods=['POST'])
def update_profile_pic():
    try:
        if 'profileImage' not in request.files:
            return jsonify({"status": "error", "message": "No image file part"}), 400
        
        file = request.files['profileImage']
        uid = request.form.get('uid')

        if not uid:
             return jsonify({"status": "error", "message": "Missing user ID (uid)"}), 400

        if file.filename == '':
            return jsonify({"status": "error", "message": "No selected file"}), 400

        
        if file:
            upload_result = cloudinary.uploader.upload(file)
            secure_url = upload_result.get('secure_url')
            
            if not secure_url:
                return jsonify({"status": "error", "message": "Failed to upload to Cloudinary"}), 500

            
            result = users_collection.update_one(
                {"uid": uid},
                {"$set": {"photo_url": secure_url}}
            )

            if result.matched_count == 0:
                return jsonify({"status": "error", "message": "User not found"}), 404

          
            return jsonify({
                "status": "success",
                "message": "Profile picture updated successfully",
                "photo_url": secure_url
            }), 200

    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500
    
@app.route('/users/delete_profile_pic', methods=['POST'])
def delete_profile_pic():
    try:
        data = request.get_json()
        uid = data.get('uid')

        if not uid:
            return jsonify({"status": "error", "message": "Missing user ID (uid)"}), 400

        # Remove the photo_url field from the user's document
        result = users_collection.update_one(
            {"uid": uid},
            {"$unset": {"photo_url": ""}} 
        )

        if result.matched_count == 0:
            return jsonify({"status": "error", "message": "User not found"}), 404

        return jsonify({
            "status": "success",
            "message": "Profile picture deleted successfully"
        }), 200

    except Exception as e:
        print(f"An error occurred in delete_profile_pic: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500
    
@app.route('/users/update_username', methods=['POST'])
def update_username():
    try:
        data = request.get_json()
        
        uid = data.get('uid')
        new_username = data.get('username')

        if not uid or not new_username:
            return jsonify({"status": "error", "message": "Missing uid or new username"}), 400

        
        result = users_collection.update_one(
            {"uid": uid},
            {"$set": {"username": new_username}}
        )

        if result.matched_count == 0:
            return jsonify({"status": "error", "message": "User not found"}), 404

        return jsonify({
            "status": "success",
            "message": "Username updated successfully"
        }), 200

    except Exception as e:
        print(f"An error occurred in update_username: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500

@app.route("/plant/log_care/<plant_id>", methods=["POST"])
def log_plant_care(plant_id):
    try:
        data = request.get_json()
        care_type = data.get('careType') # e.g., "water", "fertilize"

        if not care_type:
            return jsonify({"status": "error", "message": "Missing 'careType' in request"}), 400

        
        plant = plant_collection.find_one({"_id": ObjectId(plant_id)})
        if not plant:
            return jsonify({"status": "error", "message": "Plant not found"}), 404
        
       
        rules = plant_care_rules_collection.find_one({"plantType": plant.get("plantType")})
        if not rules:
            return jsonify({"status": "error", "message": "Care rules not found"}), 404

        
        update_fields = {}
        now = datetime.now()

        if care_type == 'water':
            freq = rules.get('wateringFrequencyDays', 7)
            update_fields['lastWateredDate'] = now
            update_fields['nextWateringDate'] = now + timedelta(days=freq)
        
        elif care_type == 'fertilize':
            freq = rules.get('fertilizingFrequencyDays', 30)
            update_fields['lastFertilizedDate'] = now 
            update_fields['nextFertilizingDate'] = now + timedelta(days=freq)
        
        elif care_type == 'repot':
            freq = rules.get('repottingFrequencyMonths', 12)
            update_fields['lastRepottedDate'] = now
            update_fields['nextRepottingDate'] = now + relativedelta(months=freq)
        
        else:
            return jsonify({"status": "error", "message": "Invalid 'careType'"}), 400

        
        plant_collection.update_one(
            {"_id": ObjectId(plant_id)},
            {"$set": update_fields}
        )
        
        
        updated_plant = plant_collection.find_one({"_id": ObjectId(plant_id)})
        serialized_plant = serialize_plant_doc(updated_plant)

        return jsonify({
            "status": "success",
            "message": f"'{care_type}' logged successfully!",
            "plant": serialized_plant
        }), 200

    except Exception as e:
        print(f"An error occurred in log_plant_care: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500
    
@app.route("/care_guide/plant/<guide_id>", methods=["GET"])
def get_care_guide_details(guide_id):
    
    try:
        guide = care_guide_collection.find_one({"_id": ObjectId(guide_id)})
        
        if not guide:
            return jsonify({"error": "Care guide not found"}), 404
        
      
        guide['_id'] = str(guide['_id'])
        
        return jsonify(guide), 200

    except Exception as e:
        print(f"Error in get_care_guide_details: {e}")
        return jsonify({"error": "An internal server error occurred"}), 500
    
@app.route('/care_guide/add', methods=['POST'])
def add_care_guide():
    """Add a new plant care guide to the community database"""
    try:
        # Get form data
        plant_name = request.form.get('plant_name')
        scientific_name = request.form.get('scientific_name', 'N/A')
        watering_schedule = request.form.get('watering_schedule')
        sunlight_needs = request.form.get('sunlight_needs')
        soil_type = request.form.get('soil_type')
        fertilizer_tips = request.form.get('fertilizer_tips')

        # Validate required fields
        if not all([plant_name, watering_schedule, sunlight_needs, soil_type, fertilizer_tips]):
            return jsonify({
                "status": "error", 
                "message": "Missing required fields"
            }), 400

        # Check if plant already exists in care guide
        existing_plant = care_guide_collection.find_one({
            "plant_name": {"$regex": f"^{plant_name.strip()}$", "$options": "i"}
        })
        
        if existing_plant:
            return jsonify({
                "status": "error",
                "message": "This plant already exists in our database"
            }), 409

        # Handle image upload
        image_url = ''
        if 'image' in request.files:
            file = request.files['image']
            if file.filename != '':
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    image_url = upload_result.get('secure_url')
                except Exception as upload_error:
                    print(f"Image upload error: {upload_error}")
                    pass

        # Create care guide document
        care_guide_data = {
            "plant_name": plant_name.strip(),
            "scientific_name": scientific_name.strip() if scientific_name else 'N/A',
            "image_url": image_url,
            "watering_schedule": watering_schedule.strip(),
            "sunlight_needs": sunlight_needs.strip(),
            "soil_type": soil_type.strip(),
            "fertilizer_tips": fertilizer_tips.strip(),
            "created_at": datetime.utcnow(),
            "contributed_by": "community"
        }

        # Insert into database
        result = care_guide_collection.insert_one(care_guide_data)
        care_guide_data["_id"] = str(result.inserted_id)
        
        return jsonify({
            "status": "success",
            "message": "Plant care guide added successfully!",
            "plant": care_guide_data
        }), 201

    except Exception as e:
        print(f"Error in add_care_guide: {e}")
        return jsonify({
            "status": "error", 
            "message": "An internal server error occurred",
            "details": str(e)
        }), 500
    

# -----------------------------------------------------------------------------------

from flask import Flask, request, jsonify
from pymongo import MongoClient
import os

app = Flask(__name__)

# MongoDB connection
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("MONGO_DB_NAME")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
care_plan_collection = db["care_plans"]


@app.route("/api/care-plan", methods=["POST"])
def get_care_plan():
    data = request.json

    disease_name = data.get("disease_name")
    confidence = data.get("confidence")

    if not disease_name or not confidence:
        return jsonify({
            "success": False,
            "message": "disease_name and confidence are required"
        }), 400

    care_plan = care_plan_collection.find_one(
        {"disease_name": disease_name},
        {"_id": 0}
    )

    response = {
        "success": True,
        "confidence": confidence
    }

    # HIGH confidence
    if confidence == "high" and care_plan:
        response["care_plan"] = care_plan

    # MEDIUM confidence
    elif confidence == "medium" and care_plan:
        response["note"] = (
            "This diagnosis is based on image analysis and may not be fully accurate. "
            "Please monitor your plant closely."
        )
        response["care_plan"] = care_plan

    # LOW confidence OR disease not found
    else:
        response["note"] = (
            "We could not confidently identify the disease. "
            "Below are general care steps to keep your plant safe."
        )
        response["care_plan"] = {
            "what_happening": "Possible plant stress or early-stage disease.",
            "immediate_actions": [
                "Isolate the plant",
                "Avoid overwatering",
                "Ensure proper sunlight"
            ],
            "next_7_days": [
                "Observe symptoms daily",
                "Maintain airflow",
                "Check soil moisture"
            ],
            "avoid": [
                "Random chemical use",
                "Overwatering"
            ],
            "prevention": [
                "Regular inspection",
                "Clean gardening tools"
            ],
            "consult_expert": [
                "If symptoms worsen",
                "If plant shows severe damage"
            ]
        }

    return jsonify(response), 200




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
