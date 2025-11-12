import os
from flask import Flask, request, jsonify, send_from_directory
from pymongo import MongoClient
from dotenv import load_dotenv
from werkzeug.utils import secure_filename #for safe file names
from flask_cors import CORS #for allowing cross-origin requests
from bson import ObjectId
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import cloudinary
import cloudinary.uploader


#load environment variables( for local testing)
load_dotenv()

app = Flask(_name_)
CORS(app)


cloudinary.config( 
  cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"), 
  api_key = os.getenv("CLOUDINARY_API_KEY"), 
  api_secret = os.getenv("CLOUDINARY_API_SECRET") 
)


#db connection
MONGO_URI = os.getenv('MONGO_URI')
if not MONGO_URI:
    
    raise RuntimeError("MONGO_URI environment variable not set!")

client = MongoClient(MONGO_URI)
db = client['GreenBuddyDB']

users_collection = db['users']

#for add_new_plant screen
plant_collection = db['plants']

plant_care_rules_collection = db['plant_care_rules']

 #set new reminder
reminders_collection = db['reminders']

care_guide_collection = db['care_guide_data']


def serialize_plant_doc(plant):
    
    if not plant:
        return None
    
    # Convert any datetime objects to ISO strings
    for key, value in plant.items():
        if isinstance(value, datetime):
            plant[key] = value.isoformat()
    
    # Convert ObjectId to string
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
    



@app.route('/add_plant', methods=['POST'])
def add_plant():
    try:
       
        firebase_uid = request.form.get('uid')
        plant_name = request.form.get('plantName')
        plant_type = request.form.get('plantType') 
        last_watered_date_str = request.form.get('lastWateredDate') 
        last_fertilized_date_str = request.form.get('lastFertilizedDate')
        last_rePotted_date_str = request.form.get('lastRepottedDate')

        #
        care_guide_id = None  
        if plant_name:
            # Use rege
            care_guide_document = care_guide_collection.find_one({
                "plant_name": {"$regex": f"^{plant_name.strip()}$", "$options": "i"}
            })
            
            if care_guide_document:
                care_guide_id = care_guide_document['_id']
        

        if not all([firebase_uid, plant_name, plant_type, last_watered_date_str,last_fertilized_date_str,last_rePotted_date_str]):
            return jsonify({"status": "error", "message": "Missing required fields (uid, plantName, plantType, lastWateredDate)"}), 400

        
        full_image_url = None
        if 'plantImage' in request.files:
            file = request.files['plantImage']
            if file.filename != '':
                upload_result = cloudinary.uploader.upload(file)
                full_image_url = upload_result.get('secure_url')

         
        
        # Find the care rules for this plant type
        rules = plant_care_rules_collection.find_one({"plantType": plant_type}) 

        if not rules:
            return jsonify({"status": "error", "message": f"Care rules for plant type '{plant_type}' not found."}), 404

        
        watering_freq_days = rules.get('wateringFrequencyDays', 7)
        fertilizing_freq_days = rules.get('fertilizingFrequencyDays', 30) 
        repotting_freq_months = rules.get('repottingFrequencyMonths', 12) 

        # Convert date strings from Flutter into Python datetime objects
        
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

        #  Create the new plant document 
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
            
            "care_guide_id": care_guide_id
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
       #reminder data is sent as JSON , so used request.get_json
        data = request.get_json()
        
        firebase_uid = data.get('uid')
        note = data.get('note')

        if not firebase_uid or not note:
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

       #data document to save in db
        reminder_data = {
            "uid": firebase_uid,
            "note": note,
            "date": data.get('date'),
            "time": data.get('time'),
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
            {"_id": 0} #exclude db id from results
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
        plants_cursor = list(plant_collection.find({"uid": uid}))

        
        serialized_plants = []
        for plant in plants_cursor:
            serialized_plants.append(serialize_plant_doc(plant))
      
        
        return jsonify(serialized_plants)
    except Exception as e:
        print(f"Error in get_garden: {e}")
        return jsonify({"error": str(e)}), 500
    
#api endpoint to get single plants detail
@app.route("/plant/<plant_id>", methods=["GET"])
def get_plant_details(plant_id):
    try:
        #find single plant  by unique mongodb id
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
    
        result = plant_collection.delete_one({"_id": ObjectId(plant_id)})

        if result.deleted_count == 1:
            
            return jsonify({"status": "success", "message": "Plant deleted successfully"}), 200
        else:
           
            return jsonify({"status": "error", "message": "Plant not found"}), 404

     except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred"}), 500
     



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
        last_watered_date_str = request.form.get('lastWateredDate')
        last_fertilized_date_str = request.form.get('lastFertilizedDate')
        last_rePotted_date_str = request.form.get('lastRepottedDate')

        

        if not all([plant_name, plant_type, last_watered_date_str,last_fertilized_date_str,last_rePotted_date_str]):
            return jsonify({"status": "error", "message": "Missing required fields (plantName, plantType, lastWateredDate)"}), 400

        
        rules = plant_care_rules_collection.find_one({"plantType": plant_type})
        if not rules:
            return jsonify({"status": "error", "message": f"Care rules for plant type '{plant_type}' not found."}), 404

        # Get the frequencies
        watering_freq_days = rules.get('wateringFrequencyDays', 7)
        fertilizing_freq_days = rules.get('fertilizingFrequencyDays', 30)
        repotting_freq_months = rules.get('repottingFrequencyMonths', 12)

        #  Parse Dates & Calculate Next Dates 
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
  
        #  Build the update document
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
                
        # Update the document in MongoDB 
       
        result = plant_collection.update_one(
            {"_id": ObjectId(plant_id)},
            {
                "$set": update_data,
                "$unset": {"wateringFrequency": ""} 
            }
        )

        if result.matched_count == 0:
            return jsonify({"status": "error", "message": "Plant not found"}), 404
        
        # Fetch the fully updated plant to send back
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
        #find documents with matching user id and sor tthem by date they were created
        cursor = reminders_collection.find({"uid": uid}).sort("date", -1)
        
        reminders_list = []
        for reminder in cursor:
            reminders_list.append({
                "_id": str(reminder["_id"]),
                "note": reminder.get("note", ""),
                "date": reminder.get("date", ""),
                "time": reminder.get("time", ""),
            })
            
        return jsonify(reminders_list), 200

    except Exception as e:
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route("/plant/log_care/<plant_id>", methods=["POST"])
def log_plant_care(plant_id):
    try:
        data = request.get_json()
        care_type = data.get('careType') # e.g., "water", "fertilize"

        if not care_type:
            return jsonify({"status": "error", "message": "Missing 'careType' in request"}), 400

        #  Find the plant
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
        
        #  Return the full, updated plant
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
    """Fetches a single care guide document by its _id."""
    try:
        guide = care_guide_collection.find_one({"_id": ObjectId(guide_id)})
        
        if not guide:
            return jsonify({"error": "Care guide not found"}), 404
        
       
        guide['_id'] = str(guide['_id'])
        
        return jsonify(guide), 200

    except Exception as e:
        print(f"Error in get_care_guide_details: {e}")
        return jsonify({"error": "An internal server error occurred"}), 500

if _name_ == '_main_':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
