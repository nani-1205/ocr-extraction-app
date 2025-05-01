# utils/db_handler.py
import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure, ConfigurationError
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env

MONGO_USER = os.getenv('MONGO_USER')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD')
MONGO_HOST = os.getenv('MONGO_HOST')
MONGO_PORT = os.getenv('MONGO_PORT')
MONGO_AUTH_DB = os.getenv('MONGO_AUTH_DB')
MONGO_DB_NAME = os.getenv('MONGO_DB_NAME')
MONGO_COLLECTION_NAME = os.getenv('MONGO_COLLECTION_NAME')

# Construct the MongoDB URI
# Handles cases with or without authentication
if MONGO_USER and MONGO_PASSWORD:
    MONGO_URI = f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/{MONGO_AUTH_DB}?authSource={MONGO_AUTH_DB}"
else:
     MONGO_URI = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/" # No auth

# Global client and db/collection variables
client = None
db = None
collection = None
connection_error = None

def connect_to_db():
    """Establishes connection to MongoDB and sets up db and collection objects."""
    global client, db, collection, connection_error
    if client and db and collection: # Already connected
        return True

    if not all([MONGO_HOST, MONGO_PORT, MONGO_DB_NAME, MONGO_COLLECTION_NAME]):
        connection_error = "MongoDB environment variables missing (HOST, PORT, DB_NAME, COLLECTION_NAME)"
        print(f"Error: {connection_error}")
        return False

    print(f"Attempting to connect to MongoDB at: {MONGO_HOST}:{MONGO_PORT}")
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # 5 second timeout
        # The ismaster command is cheap and does not require auth.
        client.admin.command('ismaster')
        print("MongoDB connection successful.")
        db = client[MONGO_DB_NAME]

        # Check if collection exists, create if not (implicitly on first insert)
        # We can explicitly check if needed, but pymongo handles creation well.
        if MONGO_COLLECTION_NAME not in db.list_collection_names():
            print(f"Collection '{MONGO_COLLECTION_NAME}' not found. It will be created on first insert.")
            # Optionally create it explicitly: db.create_collection(MONGO_COLLECTION_NAME)

        collection = db[MONGO_COLLECTION_NAME]
        connection_error = None # Reset error on successful connection
        print(f"Using database '{MONGO_DB_NAME}' and collection '{MONGO_COLLECTION_NAME}'.")
        return True

    except ConfigurationError as e:
        connection_error = f"MongoDB Configuration Error (check URI format, credentials): {e}"
        print(f"Error: {connection_error}")
        client = None
        db = None
        collection = None
        return False
    except ConnectionFailure as e:
        connection_error = f"MongoDB Connection Failure (check host, port, network): {e}"
        print(f"Error: {connection_error}")
        client = None
        db = None
        collection = None
        return False
    except OperationFailure as e: # Often related to authentication
         connection_error = f"MongoDB Operation Failure (check user, password, authSource): {e}"
         print(f"Error: {connection_error}")
         client = None
         db = None
         collection = None
         return False
    except Exception as e:
        connection_error = f"An unexpected error occurred during MongoDB connection: {e}"
        print(f"Error: {connection_error}")
        client = None
        db = None
        collection = None
        return False


def get_collection():
    """Returns the collection object, trying to connect if necessary."""
    if collection:
        return collection
    elif connect_to_db():
        return collection
    else:
        print("Error: Cannot get collection, MongoDB connection failed.")
        return None

def insert_record(data):
    """Inserts a single document into the collection."""
    col = get_collection()
    if col is None:
        print("Error: Cannot insert record, no MongoDB collection available.")
        return None

    try:
        result = col.insert_one(data)
        print(f"Successfully inserted record with ID: {result.inserted_id}")
        return result.inserted_id
    except Exception as e:
        print(f"Error inserting record into MongoDB: {e}")
        return None

def get_record_by_id(record_id):
    """Retrieves a record by its MongoDB ObjectId."""
    col = get_collection()
    if col is None:
        print("Error: Cannot get record, no MongoDB collection available.")
        return None
    try:
        # Import ObjectId here to avoid circular dependencies if needed elsewhere
        from bson.objectid import ObjectId
        record = col.find_one({"_id": ObjectId(record_id)})
        return record
    except Exception as e:
        print(f"Error retrieving record {record_id} from MongoDB: {e}")
        return None

# Attempt initial connection on module load
connect_to_db()