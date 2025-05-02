# app.py
import os
import uuid
from flask import Flask, request, render_template, redirect, url_for, flash, send_from_directory, jsonify # Added jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import datetime
import time
import traceback # For printing full tracebacks

# Load environment variables FIRST
load_dotenv()

# Import utility functions AFTER loading env vars
from utils import db_handler
db_connection_error = db_handler.connection_error
from utils.ocr_processor import extract_data_with_gemini
from utils.file_converter import convert_pdf_to_image, is_image_file, is_pdf_file


# --- Configuration ---
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads') # Allow overriding via .env
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'} # Allowed file types
MAX_CONTENT_LENGTH = 16 * 1024 * 1024 # 16 MB limit
# Load API key from environment (used for authentication)
API_SECRET_KEY = os.getenv('API_SECRET_KEY')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
# Use environment variable for FLASK_SECRET_KEY in production for security
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_very_secret_key_for_dev_only')

# Ensure upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
        print(f"Created upload folder: {UPLOAD_FOLDER}")
    except OSError as e:
        print(f"Error creating upload folder {UPLOAD_FOLDER}: {e}")
        # Consider handling this more gracefully if uploads are critical


# --- Helper Functions ---
def allowed_file(filename):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Core Processing Function ---
def process_uploaded_file(file_storage, original_filename):
    """
    Handles saving, preprocessing, OCR, and DB insertion for an uploaded file.
    Encapsulates the core logic used by both UI and API routes.

    Args:
        file_storage (FileStorage): The file object from Flask request.files.
        original_filename (str): The original filename provided by the user (already secured).

    Returns:
        tuple: (result_dict, status_code)
               result_dict contains success/error info.
               status_code is the suggested HTTP status.
    """
    temp_filename = None
    temp_filepath = None
    generated_image_path = None
    image_to_process_path = None

    try:
        # Generate unique filename and path
        unique_id = uuid.uuid4().hex
        # Ensure original_filename is safe before extracting extension
        safe_original_filename = secure_filename(original_filename)
        if not safe_original_filename or '.' not in safe_original_filename:
             print(f"Error: Invalid original filename after securing: {original_filename}")
             return {"status": "error", "message": "Invalid or disallowed filename."}, 400

        file_ext = safe_original_filename.rsplit('.', 1)[1].lower()
        temp_filename = f"{unique_id}.{file_ext}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

        # --- 1. Save Uploaded File Temporarily ---
        file_storage.save(temp_filepath)
        print(f"File temporarily saved to: {temp_filepath}")

        # --- 2. File Preprocessing (PDF to Image) ---
        if is_image_file(temp_filename):
            image_to_process_path = temp_filepath
            print("Processing as image file.")
        elif is_pdf_file(temp_filename):
            print("Processing as PDF file. Attempting conversion to image...")
            generated_image_path = convert_pdf_to_image(temp_filepath, app.config['UPLOAD_FOLDER'])
            if generated_image_path:
                image_to_process_path = generated_image_path
                print(f"PDF converted to image: {generated_image_path}")
            else:
                print("Error: Failed to convert PDF to image.")
                return {"status": "error", "message": "Failed to convert PDF to image for processing."}, 400 # Bad Request
        else:
             # This case should not be reached due to prior allowed_file check
             print("Error: Unsupported file type after save (internal error).")
             return {"status": "error", "message": "Unsupported file type."}, 400 # Bad Request


        # --- 3. OCR Processing (if image available) ---
        if image_to_process_path:
            print(f"Starting OCR processing for: {image_to_process_path}")
            start_time = time.time()
            # Call Gemini AI for extraction
            extracted_data = extract_data_with_gemini(image_to_process_path)
            end_time = time.time()
            processing_duration = round(end_time - start_time, 2)
            print(f"Gemini processing took {processing_duration:.2f} seconds.")

            # --- 4. Handle OCR Results ---
            if extracted_data and 'error' not in extracted_data:
                # Successful extraction from Gemini
                # Add metadata before saving to DB
                extracted_data['_metadata'] = {
                    'original_filename': safe_original_filename, # Use the secured filename
                    'upload_timestamp': datetime.datetime.utcnow().isoformat() + 'Z', # ISO 8601 format UTC
                    'processing_time_seconds': processing_duration,
                    'processed_file': os.path.basename(image_to_process_path) # The actual image file used by OCR
                }

                # --- 5. Store in MongoDB ---
                print("Attempting to insert extracted data into MongoDB...")
                # CRITICAL NOTE: insert_one MUTATES the original extracted_data dict
                # by adding the '_id' field (containing an ObjectId) upon success.
                record_id = db_handler.insert_record(extracted_data)

                if record_id:
                    # Successfully inserted
                    str_record_id = str(record_id)
                    print(f"Successfully inserted. Record ID (str): {str_record_id}")
                    # Remove the ObjectId (_id) before returning the data payload
                    extracted_data.pop('_id', None)
                    return {"status": "success", "data": extracted_data, "record_id": str_record_id}, 200 # OK
                else:
                    # DB Insert failed
                    print("Error: Failed to store data in MongoDB.")
                    # Remove potentially added _id before returning preview
                    extracted_data.pop('_id', None)
                    # Return extracted data even on DB error for API context, but flag error
                    return {"status": "error", "message": "File processed, but failed to store data in MongoDB.", "extracted_data_preview": extracted_data}, 500 # Internal Server Error
            else:
                # Handle errors reported by Gemini or OCR processing failure
                error_message = "Unknown error during OCR processing."
                raw_response = None
                if extracted_data and 'error' in extracted_data:
                     error_message = extracted_data.get('error')
                     raw_response = extracted_data.get('raw_response') # Pass raw response if available
                elif extracted_data is None:
                     error_message = "OCR processing function returned None."

                print(f"OCR processing failed: {error_message}")
                result = {"status": "error", "message": f"OCR processing failed: {error_message}"}
                if raw_response:
                    result["raw_api_response"] = raw_response # Include raw response in API error if available
                # Determine appropriate status code for AI errors
                status_code = 502 if "API Error" in error_message or "API policy" in error_message or "blocked" in error_message.lower() else 500
                return result, status_code
        else:
             # This case might occur if PDF conversion failed and returned None
             print("Error: No processable image was available after preprocessing step.")
             return {"status": "error", "message": "Could not obtain a processable image from the file."}, 500 # Internal Server Error

    # --- 6. Global Exception Handling within processing ---
    except Exception as e:
        # Catch any unexpected errors during the core file processing steps
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"ERROR: An unexpected error occurred during file processing ({error_type}): {error_msg}")
        traceback.print_exc() # Log the full traceback
        return {"status": "error", "message": f"An unexpected server error occurred during processing ({error_type})."}, 500 # Internal Server Error

    # --- 7. Cleanup ---
    finally:
        # This block executes whether the try block succeeded or failed
        print("Cleaning up temporary files...")
        # Clean up the original temporary uploaded file
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"Removed temporary upload: {temp_filepath}")
            except OSError as e:
                print(f"Error removing temporary file {temp_filepath}: {e}")

        # Clean up the generated image ONLY if it was created and is different from the temp upload
        if generated_image_path and generated_image_path != temp_filepath and os.path.exists(generated_image_path):
            try:
                os.remove(generated_image_path)
                print(f"Removed generated image: {generated_image_path}")
            except OSError as e:
                print(f"Error removing generated image {generated_image_path}: {e}")


# --- Web UI Routes ---
@app.route('/', methods=['GET'])
def index():
    """Renders the main upload page (UI)."""
    current_db_error = db_handler.connection_error
    if current_db_error:
         flash(f"Warning: Database connection issue - {current_db_error}", 'error')
    else:
         flash("Database connection appears okay.", 'info')
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file_ui():
    """Handles file uploads from the HTML form (UI)."""
    # Basic file checks for UI route
    if 'file' not in request.files:
        flash('No file part in the request.', 'error')
        return redirect(request.url) # Redirect back to index

    file = request.files['file']
    filename = file.filename

    if filename == '':
        flash('No file selected.', 'error')
        return redirect(request.url)

    if not allowed_file(filename):
        flash(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}.", 'error')
        return redirect(request.url)

    # Secure the filename before passing it to the processing function
    safe_filename = secure_filename(filename)
    if not safe_filename:
        flash('Invalid filename provided.', 'error')
        return redirect(request.url)

    # Call the core processing function
    result_data, status_code = process_uploaded_file(file, safe_filename)

    # Handle result specifically for the UI
    if status_code == 200 and result_data.get("status") == "success":
        # Success case: Render the report page
        flash('File processed and data stored successfully!', 'success')
        return render_template('report.html',
                               data=result_data.get("data"),
                               record_id=result_data.get("record_id"))
    else:
        # Error case: Flash the error message and redirect to index
        error_message = result_data.get("message", "An unknown error occurred during processing.")
        flash(f'Processing failed: {error_message}', 'error')
        # Optionally: Log more details from result_data if needed
        # print(f"UI Upload Error Details: {result_data}")
        return redirect(url_for('index')) # Redirect to index page on any processing error


# --- API Endpoint ---
@app.route('/api/v1/extract', methods=['POST'])
def extract_api():
    """API endpoint for programmatic file extraction."""
    # 1. Check API Key Authentication
    provided_key = request.headers.get('X-API-Key')
    if not API_SECRET_KEY:
        # Log this misconfiguration but return a standard error to client
        print("CRITICAL ERROR: API_SECRET_KEY is not configured on the server.")
        return jsonify({"status": "error", "message": "API endpoint not configured correctly."}), 503 # Service Unavailable
    if not provided_key or provided_key != API_SECRET_KEY:
        print(f"API Auth Failed. Provided Key: {'Present but Invalid' if provided_key else 'Missing'}")
        # Use 401 for missing/bad credentials
        return jsonify({"status": "error", "message": "Unauthorized: Missing or invalid API key."}), 401

    # 2. Check for file part in the multipart request
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No 'file' part found in the multipart request."}), 400

    file = request.files['file']
    filename = file.filename

    # 3. Check if file is selected (has a filename)
    if not filename: # Check if filename is empty or None
        return jsonify({"status": "error", "message": "No file selected or filename missing."}), 400

    # 4. Check allowed file type
    if not allowed_file(filename):
        return jsonify({"status": "error", "message": f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}."}), 400

    # 5. Secure the filename
    safe_filename = secure_filename(filename)
    if not safe_filename:
        return jsonify({"status": "error", "message": "Invalid or disallowed filename."}), 400

    # 6. Call the core processing function
    result_data, status_code = process_uploaded_file(file, safe_filename)

    # 7. Return JSON response from the processing function
    return jsonify(result_data), status_code


# --- Optional Static File Route (for debugging uploads) ---
# Use with caution in production, Nginx should handle static files.
@app.route('/uploads/<path:filename>') # Use path converter for flexibility
def uploaded_file(filename):
    """Serves files from the upload folder. USE WITH CAUTION."""
    # Prevent directory traversal attacks
    safe_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
    safe_path = os.path.abspath(os.path.join(safe_dir, filename))
    if not safe_path.startswith(safe_dir):
        print(f"Attempted directory traversal: {filename}")
        return "Forbidden", 403

    if not os.path.exists(safe_path) or not os.path.isfile(safe_path):
        return "File not found", 404

    print(f"Serving file from upload dir: {filename}") # Log access
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# --- Application Startup ---
if __name__ == '__main__':
    # Check initial DB connection status
    if db_connection_error:
         print(f"\n{'='*20} WARNING {'='*20}")
         print(f"Initial MongoDB connection failed: {db_connection_error}")
         print("The application will run, but database operations will likely fail.")
         print("Please check your .env file and MongoDB server status.")
         print(f"{'='*50}\n")
    else:
         print("Initial MongoDB connection check successful.")

    # Check if API key is configured for the API endpoint
    if not API_SECRET_KEY:
        print(f"\n{'='*20} WARNING {'='*20}")
        print("API_SECRET_KEY is not set in the environment variables (.env).")
        print("The API endpoint '/api/v1/extract' requires this for authentication and will be inaccessible.")
        print(f"{'='*50}\n")

    # Determine debug mode from environment variable
    is_debug_mode = os.environ.get('FLASK_DEBUG', 'True').lower() in ['true', '1', 'yes']
    print(f"Starting Flask app (Debug mode: {is_debug_mode})...")

    # Run the Flask development server OR use Gunicorn if running via Docker CMD
    # The host='0.0.0.0' makes it accessible externally (within Docker network or from host)
    # The port is typically 5000 for Flask dev server or Gunicorn binding
    # Note: If running with `python app.py`, this runs the dev server.
    # If running via `gunicorn app:app`, Gunicorn handles the execution.
    app.run(debug=is_debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))