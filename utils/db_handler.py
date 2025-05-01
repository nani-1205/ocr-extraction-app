# utils/db_handler.py
import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure, ConfigurationError
from dotenv import load_dotenv
from urllib.parse import quote_plus # For escaping credentials
# Import ObjectId where it's used to avoid potential circular imports if used elsewhere
from bson.objectid import ObjectId

# --- Load Environment Variables ---
# Load from .env file in the parent directory or current directory
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
escaped_password_for_log = '*****' # Placeholder for logging

if not all([MONGO_HOST, MONGO_PORT, MONGO_DB_NAME, MONGO_COLLECTION_NAME]):
    print("Error: Critical MongoDB environment variables missing (HOST, PORT, DB_NAME, COLLECTION_NAME). Connection cannot be established.")
else:
    # Handles cases with or without authentication
    if MONGO_USER and MONGO_PASSWORD:
        # Escape username and password for the URI
        try:
            escaped_user = quote_plus(MONGO_USER)
            escaped_password = quote_plus(MONGO_PASSWORD)
            escaped_password_for_log = '*****' # Ensure password isn't logged
            MONGO_URI = f"mongodb://{escaped_user}:{escaped_password}@{MONGO_HOST}:{MONGO_PORT}/{MONGO_AUTH_DB}?authSource={MONGO_AUTH_DB}"
            print(f"Constructed MongoDB URI with authentication (password hidden).")
        except Exception as e:
             print(f"Error during URI encoding: {e}")
             MONGO_URI = None # Prevent connection attempt with bad URI
    elif MONGO_HOST and MONGO_PORT:
         # Connection without authentication
         MONGO_URI = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
         print(f"Constructed MongoDB URI without authentication.")
    else:
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

    # Check if already connected and connection is alive
    if client and db and collection:
        try:
            client.admin.command('ping')
            print("MongoDB connection already established and active.")
            return True
        except (ConnectionFailure, OperationFailure) as e:
             print(f"Existing MongoDB connection lost: {e}. Attempting reconnect.")
             # Reset globals to force reconnect logic below
             client = None
             db = None
             collection = None


    print(f"Attempting to connect to MongoDB...")
    # Use the already constructed MONGO_URI, log safely
    log_uri = MONGO_URI
    if MONGO_PASSWORD: # Mask password if present, regardless of escaping success
        if 'escaped_password' in locals() and escaped_password:
            log_uri = MONGO_URI.replace(escaped_password, escaped_password_for_log)
        else: # Fallback if escaping failed but password exists
             log_uri = MONGO_URI.replace(MONGO_PASSWORD, escaped_password_for_log)
    print(f"Using URI: {log_uri}")

    try:
        # Set a reasonable connection timeout
        # Consider adding retry logic here if needed using pymongo options
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # 5 second timeout

        # The ping command verifies server reachability and authentication (if credentials provided)
        client.admin.command('ping')

        print("MongoDB server reached and authenticated (if applicable) successfully.")
        db = client[MONGO_DB_NAME]

        # Check if collection exists (for logging purposes - MongoDB creates it on first insert)
        try:
             collection_names = db.list_collection_names()
             if MONGO_COLLECTION_NAME not in collection_names:
                 print(f"Collection '{MONGO_COLLECTION_NAME}' not found in database '{MONGO_DB_NAME}'. It will be created automatically on the first data insertion.")
             else:
                 print(f"Collection '{MONGO_COLLECTION_NAME}' found in database '{MONGO_DB_NAME}'.")
        except OperationFailure as list_coll_err:
             # This might happen if the user lacks listCollections permission
             print(f"Warning: Could not list collections (permissions issue?): {list_coll_err}. Proceeding...")


        collection = db[MONGO_COLLECTION_NAME] # Get collection object regardless of existence check
        connection_error = None # Reset error on successful connection
        print(f"Successfully connected to MongoDB. Using database '{MONGO_DB_NAME}' and collection '{MONGO_COLLECTION_NAME}'.")
        return True

    except ConfigurationError as e:
        connection_error = f"MongoDB Configuration Error (check URI format, options, credentials, esp. escaping): {e}"
        print(f"Error: {connection_error}")
        client = None; db = None; collection = None # Reset globals
        return False
    except ConnectionFailure as e:
        connection_error = f"MongoDB Connection Failure (check host '{MONGO_HOST}', port '{MONGO_PORT}', network access, firewall, server status): {e}"
        print(f"Error: {connection_error}")
        client = None; db = None; collection = None
        return False
    except OperationFailure as e:
         connection_error = f"MongoDB Operation Failure (check user '{MONGO_USER}', password, authSource '{MONGO_AUTH_DB}', database/collection permissions): {e}"
         print(f"Error: {connection_error}")
         if e.code == 18: # AuthenticationFailed code
             print("Hint: Authentication failed. Double-check username, password, and authSource.")
         client = None; db = None; collection = None
         return False
    except Exception as e:
        connection_error = f"An unexpected error occurred during MongoDB connection: {type(e).__name__} - {e}"
        print(f"Error: {connection_error}")
        client = None; db = None; collection = None
        return False


def get_collection():
    """
    Returns the MongoDB collection object.
    Attempts to connect or reconnect if necessary.
    Checks if the connection is alive before returning an existing object.
    """
    global client, db, collection # Declare intent to potentially modify globals on reconnect

    # --- Use explicit 'is not None' check ---
    if collection is not None:
        # Check if the connection is still alive before returning
        try:
            if client: # Ensure client object exists
                 client.admin.command('ping')
                 # print("DEBUG: Connection ping successful in get_collection.") # Optional debug log
                 return collection # Connection is alive, return existing collection object
            else:
                 # This state (collection exists but client is None) indicates an issue.
                 print("Warning: Collection object exists but client is None in get_collection. Forcing reconnect.")
                 # Fall through to reconnect logic by returning None here, or call connect_to_db directly
                 # For simplicity, let the main flow handle reconnect attempt
        except (ConnectionFailure, OperationFailure, AttributeError) as e:
             # Ping failed or client was None
             print(f"Connection check/ping failed in get_collection: {e}. Attempting reconnect.")
             # Reset globals to ensure connect_to_db performs a full connection attempt
             client = None
             db = None
             collection = None
             # Fall through to the connect_to_db() call below

    # If collection is None OR the ping check failed above, try to connect/reconnect
    print("DEBUG: Collection is None or connection check failed, calling connect_to_db...")
    if connect_to_db():
        # connect_to_db() should have set the global 'collection' if successful
        return collection # Return the newly established collection object
    else:
        # Connection failed
        print(f"Error: Cannot get collection, MongoDB connection failed or reconnect failed. Last error: {connection_error}")
        return None # Return None to indicate failure


def insert_record(data):
    """Inserts a single document into the MongoDB collection."""
    col = get_collection() # Use the robust get_collection function
    if col is None:
        print("Error: Cannot insert record, failed to get MongoDB collection.")
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
         # Update connection error state if it seems related to auth/permissions
         global connection_error
         connection_error = f"Operation Failure during insert (likely auth/permissions): {e}"
         return None
    except Exception as e:
        print(f"Error inserting record into MongoDB: {type(e).__name__} - {e}")
        return None

def get_record_by_id(record_id):
    """Retrieves a record by its MongoDB ObjectId string."""
    col = get_collection() # Use the robust get_collection function
    if col is None:
        print("Error: Cannot get record, failed to get MongoDB collection.")
        return None

    try:
        # Validate and convert the string ID to ObjectId
        obj_id = ObjectId(str(record_id)) # Ensure input is string before converting
    except (TypeError, ValueError, bson.errors.InvalidId): # Catch specific ObjectId conversion errors
        print(f"Error: Invalid format for record_id '{record_id}'. Must be a 12-byte input or a 24-character hex string.")
        return None

    try:
        record = col.find_one({"_id": obj_id})
        if record:
            print(f"Successfully retrieved record with ID: {record_id}")
        else:
            print(f"No record found with ID: {record_id}")
        return record # Returns the document dict or None if not found
    except OperationFailure as e:
         print(f"Error retrieving record {record_id} from MongoDB (OperationFailure - check permissions?): {e}")
         global connection_error
         connection_error = f"Operation Failure during find_one (likely auth/permissions): {e}"
         return None
    except Exception as e:
        print(f"Error retrieving record {record_id} from MongoDB: {type(e).__name__} - {e}")
        return None

# --- Initial Connection Attempt ---
# Attempt to connect when the module is first loaded.
# The application startup check in app.py relies on the 'connection_error' variable.
print("db_handler module loaded. Attempting initial MongoDB connection...")
connect_to_db() # Call connect function on module load
if connection_error:
    # This message is useful for diagnosing startup issues
    print(f"Initial MongoDB connection failed: {connection_error}")
else:
    # This confirms successful connection on startup
    print("Initial MongoDB connection check successful.")