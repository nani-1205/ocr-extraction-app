# utils/db_handler.py
import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure, ConfigurationError
from dotenv import load_dotenv
from urllib.parse import quote_plus # <--- IMPORT ADDED HERE

# Load environment variables from .env file in the parent directory or current directory
# Useful if running scripts directly from the utils directory sometimes
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv() # Load from current directory or standard locations


MONGO_USER = os.getenv('MONGO_USER')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD')
MONGO_HOST = os.getenv('MONGO_HOST')
MONGO_PORT = os.getenv('MONGO_PORT')
MONGO_AUTH_DB = os.getenv('MONGO_AUTH_DB', 'admin') # Default authSource to 'admin' if not set
MONGO_DB_NAME = os.getenv('MONGO_DB_NAME')
MONGO_COLLECTION_NAME = os.getenv('MONGO_COLLECTION_NAME')

# --- Construct the MongoDB URI ---
MONGO_URI = None
if not all([MONGO_HOST, MONGO_PORT, MONGO_DB_NAME, MONGO_COLLECTION_NAME]):
    print("Error: Critical MongoDB environment variables missing (HOST, PORT, DB_NAME, COLLECTION_NAME). Connection cannot be established.")
    # Set URI to None or handle this case appropriately later
else:
    # Handles cases with or without authentication
    if MONGO_USER and MONGO_PASSWORD:
        # Escape username and password for the URI
        try:
            escaped_user = quote_plus(MONGO_USER)
            escaped_password = quote_plus(MONGO_PASSWORD)
            MONGO_URI = f"mongodb://{escaped_user}:{escaped_password}@{MONGO_HOST}:{MONGO_PORT}/{MONGO_AUTH_DB}?authSource={MONGO_AUTH_DB}"
            print(f"Constructed MongoDB URI with authentication (password hidden).")
        except Exception as e:
             print(f"Error during URI encoding: {e}") # Should not happen with standard strings
             MONGO_URI = None # Prevent connection attempt with bad URI
    elif MONGO_HOST and MONGO_PORT:
         # Connection without authentication
         MONGO_URI = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
         print(f"Constructed MongoDB URI without authentication.")
    else:
         # This case is covered by the initial 'not all' check, but as a safeguard:
         print("Error: MongoDB Host/Port not defined for unauthenticated connection.")
         MONGO_URI = None


# Global client and db/collection variables
client = None
db = None
collection = None
connection_error = None # Stores the last connection error message

def connect_to_db():
    """Establishes connection to MongoDB and sets up db and collection objects."""
    global client, db, collection, connection_error

    # Don't attempt connection if essential config is missing or URI construction failed
    if MONGO_URI is None:
        connection_error = "MongoDB URI could not be constructed due to missing environment variables or encoding error."
        print(f"Skipping DB connection: {connection_error}")
        return False

    if client and db and collection: # Already connected
        try:
            # Quick check if connection is still alive
            client.admin.command('ping')
            print("MongoDB connection already established and active.")
            return True
        except (ConnectionFailure, OperationFailure) as e:
             print(f"Existing MongoDB connection lost: {e}. Attempting reconnect.")
             client = None # Force reconnect
             db = None
             collection = None
             # Fall through to reconnect logic


    print(f"Attempting to connect to MongoDB...")
    # Avoid logging password in production/shared logs
    log_uri = MONGO_URI
    if 'escaped_password' in locals() and escaped_password:
        log_uri = MONGO_URI.replace(escaped_password, '*****')
    elif MONGO_PASSWORD: # Handle case where encoding might have failed but password exists
        log_uri = MONGO_URI.replace(MONGO_PASSWORD, '*****')
    print(f"Using URI: {log_uri}")

    try:
        # Set a reasonable connection timeout
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # 5 second timeout

        # The ismaster/hello command is cheap and does not require auth. Verifies server reachability.
        client.admin.command('ping') # 'ping' is preferred over 'ismaster' in modern pymongo

        print("MongoDB server reached successfully.")
        db = client[MONGO_DB_NAME]

        # Check if collection exists, create if not (implicitly on first insert)
        # Explicit check can be useful for logging/confirmation
        if MONGO_COLLECTION_NAME not in db.list_collection_names():
            print(f"Collection '{MONGO_COLLECTION_NAME}' not found in database '{MONGO_DB_NAME}'. It will be created automatically on the first data insertion.")
            # Optionally create it explicitly if needed for specific setup:
            # try:
            #     db.create_collection(MONGO_COLLECTION_NAME)
            #     print(f"Explicitly created collection '{MONGO_COLLECTION_NAME}'.")
            # except Exception as create_err:
            #     print(f"Warning: Could not explicitly create collection: {create_err}")
        else:
             print(f"Collection '{MONGO_COLLECTION_NAME}' found in database '{MONGO_DB_NAME}'.")


        collection = db[MONGO_COLLECTION_NAME]
        connection_error = None # Reset error on successful connection
        print(f"Successfully connected to MongoDB. Using database '{MONGO_DB_NAME}' and collection '{MONGO_COLLECTION_NAME}'.")
        return True

    except ConfigurationError as e:
        connection_error = f"MongoDB Configuration Error (check URI format, options, credentials, esp. escaping): {e}"
        print(f"Error: {connection_error}")
        client = None
        db = None
        collection = None
        return False
    except ConnectionFailure as e:
        # This can include DNS resolution issues, network problems, firewall blocks, server down
        connection_error = f"MongoDB Connection Failure (check host '{MONGO_HOST}', port '{MONGO_PORT}', network access, firewall, server status): {e}"
        print(f"Error: {connection_error}")
        client = None
        db = None
        collection = None
        return False
    except OperationFailure as e:
        # Often related to authentication, permissions, or commands failing after connection
         connection_error = f"MongoDB Operation Failure (check user '{MONGO_USER}', password, authSource '{MONGO_AUTH_DB}', database permissions): {e}"
         print(f"Error: {connection_error}")
         # Check for common authentication error code
         if e.code == 18: # AuthenticationFailed code
             print("Hint: Authentication failed. Double-check username, password, and authSource.")
         client = None
         db = None
         collection = None
         return False
    except Exception as e:
        # Catch-all for other unexpected errors during connection
        connection_error = f"An unexpected error occurred during MongoDB connection: {type(e).__name__} - {e}"
        print(f"Error: {connection_error}")
        client = None
        db = None
        collection = None
        return False


def get_collection():
    """Returns the collection object, trying to connect if necessary."""
    if collection:
        # Optional: Add a quick ping check here too if you suspect intermittent connection issues
        # try:
        #     client.admin.command('ping')
        #     return collection
        # except (ConnectionFailure, OperationFailure):
        #     print("Connection lost in get_collection. Attempting reconnect.")
        #     if connect_to_db():
        #         return collection
        #     else:
        #         print("Error: Reconnect failed in get_collection.")
        #         return None
        return collection # Return existing collection if it seems ok
    elif connect_to_db():
        # connect_to_db() sets the global 'collection' if successful
        return collection
    else:
        # Connection failed
        print(f"Error: Cannot get collection, MongoDB connection failed. Last error: {connection_error}")
        return None

def insert_record(data):
    """Inserts a single document into the collection."""
    col = get_collection()
    if col is None:
        print("Error: Cannot insert record, no MongoDB collection available.")
        return None # Indicate failure

    if not isinstance(data, dict):
        print(f"Error: Data to insert must be a dictionary, got {type(data)}.")
        return None

    try:
        result = col.insert_one(data)
        print(f"Successfully inserted record with ID: {result.inserted_id}")
        return result.inserted_id
    except OperationFailure as e:
         print(f"Error inserting record into MongoDB (OperationFailure - check permissions?): {e}")
         # Update connection error state if it seems related to connection/auth
         if "authenticate" in str(e).lower() or "auth" in str(e).lower():
              global connection_error
              connection_error = f"Operation Failure during insert (likely auth/permissions): {e}"
         return None
    except Exception as e:
        print(f"Error inserting record into MongoDB: {type(e).__name__} - {e}")
        return None

def get_record_by_id(record_id):
    """Retrieves a record by its MongoDB ObjectId string."""
    col = get_collection()
    if col is None:
        print("Error: Cannot get record, no MongoDB collection available.")
        return None

    try:
        # Import ObjectId here where needed
        from bson.objectid import ObjectId
        # Validate and convert the string ID to ObjectId
        obj_id = ObjectId(str(record_id)) # Ensure it's a string first, then convert
        record = col.find_one({"_id": obj_id})
        if record:
            print(f"Successfully retrieved record with ID: {record_id}")
        else:
            print(f"No record found with ID: {record_id}")
        return record # Returns None if not found, which is standard for find_one
    except TypeError:
        print(f"Error: Invalid format for record_id '{record_id}'. Must be a 12-byte input or a 24-character hex string.")
        return None
    except Exception as e:
        print(f"Error retrieving record {record_id} from MongoDB: {type(e).__name__} - {e}")
        return None

# --- Initial Connection Attempt ---
# Attempt to connect when the module is first loaded.
# The application startup check in app.py relies on the 'connection_error' variable.
print("db_handler module loaded. Attempting initial MongoDB connection...")
connect_to_db()
if connection_error:
    print(f"Initial MongoDB connection failed: {connection_error}")
else:
    print("Initial MongoDB connection check successful.")