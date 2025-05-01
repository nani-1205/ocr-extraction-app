# app.py
import os
import uuid
from flask import Flask, request, render_template, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import datetime
import time
import traceback # For printing full tracebacks

# Import utility functions
# Ensure db_handler connection check happens before using its functions
from utils import db_handler
# Access connection error if needed after module load
db_connection_error = db_handler.connection_error

# Now import other utils that might depend on successful initial setup
from utils.ocr_processor import extract_data_with_gemini
from utils.file_converter import convert_pdf_to_image, is_image_file, is_pdf_file

# Load environment variables
load_dotenv()

# --- Configuration ---
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads') # Allow overriding via .env
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'} # Allowed file types
MAX_CONTENT_LENGTH = 16 * 1024 * 1024 # 16 MB limit

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
        # Depending on severity, you might want to exit or handle this differently
        # For now, we'll let it continue, but uploads will likely fail.


# --- Helper Functions ---
def allowed_file(filename):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Routes ---
@app.route('/', methods=['GET'])
def index():
    """Renders the main upload page."""
    # Check DB connection status on loading the main page
    # Re-check connection status from the handler module variable
    current_db_error = db_handler.connection_error
    if current_db_error:
         flash(f"Warning: Database connection issue - {current_db_error}", 'error')
    else:
         # You might want to attempt a quick ping here for real-time status
         # but relying on the initial check/last known status is often sufficient
         flash("Database connection appears okay.", 'info') # Optional success message

    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles file uploads, processing, and data storage."""

    # --- 1. File Validation ---
    if 'file' not in request.files:
        flash('No file part in the request.', 'error')
        return redirect(request.url) # Redirect back to the same page (index)

    file = request.files['file']

    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(request.url)

    if not allowed_file(file.filename):
        flash(f"File type not allowed. Please upload {', '.join(ALLOWED_EXTENSIONS)}.", 'error')
        return redirect(request.url)

    original_filename = secure_filename(file.filename)
    # Create a unique filename to avoid conflicts and potential security issues
    unique_id = uuid.uuid4().hex
    file_ext = original_filename.rsplit('.', 1)[1].lower()
    temp_filename = f"{unique_id}.{file_ext}"
    temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

    # Paths for potential converted image
    image_to_process_path = None
    generated_image_path = None # Keep track if we create a temp image from PDF

    try:
        # --- 2. Save Uploaded File Temporarily ---
        file.save(temp_filepath)
        print(f"File temporarily saved to: {temp_filepath}")

        # --- 3. File Preprocessing (PDF to Image) ---
        if is_image_file(temp_filename):
            image_to_process_path = temp_filepath
            print("Processing as image file.")
        elif is_pdf_file(temp_filename):
            print("Processing as PDF file. Attempting conversion to image...")
            # Convert PDF's first page to an image (saves as PNG in uploads folder)
            generated_image_path = convert_pdf_to_image(temp_filepath, app.config['UPLOAD_FOLDER'])
            if generated_image_path:
                image_to_process_path = generated_image_path
                print(f"PDF converted to image: {generated_image_path}")
            else:
                flash('Failed to convert PDF to image for processing.', 'error')
                # Render report page with error, finally block will clean up temp_filepath
                # No need to redirect immediately
                return render_template('report.html', error='Failed to convert PDF to image.')
        else:
             # This case should not be reached due to allowed_file check, but as a safeguard:
             flash('Unsupported file type after save (internal error).', 'error')
             return redirect(url_for('index'))


        # --- 4. OCR Processing (if image available) ---
        if image_to_process_path:
            print(f"Starting OCR processing for: {image_to_process_path}")
            start_time = time.time()
            # Call Gemini AI for extraction
            extracted_data = extract_data_with_gemini(image_to_process_path)
            end_time = time.time()
            processing_duration = round(end_time - start_time, 2)
            print(f"Gemini processing took {processing_duration:.2f} seconds.")

            # --- 5. Handle OCR Results ---
            if extracted_data and 'error' not in extracted_data:
                # Successful extraction from Gemini

                # Add metadata before saving to DB
                extracted_data['_metadata'] = {
                    'original_filename': original_filename,
                    'upload_timestamp': datetime.datetime.utcnow().isoformat() + 'Z', # ISO 8601 format UTC
                    'processing_time_seconds': processing_duration,
                    'processed_file': os.path.basename(image_to_process_path) # The actual image file used by OCR
                }

                # --- 6. Store in MongoDB ---
                print("Attempting to insert extracted data into MongoDB...")
                # CRITICAL NOTE: insert_one MUTATES the original extracted_data dict
                # by adding the '_id' field (containing an ObjectId) upon success.
                record_id = db_handler.insert_record(extracted_data)

                if record_id:
                    # --- SUCCESS PATH ---
                    flash('File processed and data stored successfully!', 'success')
                    # Convert ObjectId to string for display in template/JSON serialization
                    str_record_id = str(record_id)
                    print(f"Successfully inserted. Record ID (str): {str_record_id}")

                    # --- FIX: Remove the ObjectId added by insert_one ---
                    # The `tojson` filter in Jinja2 cannot serialize ObjectId.
                    # We remove it from the dict before passing it to the template.
                    removed_id = extracted_data.pop('_id', None) # Use pop with None default
                    if removed_id:
                         print(f"DEBUG: Removed '_id' field (value: {removed_id}) from data before rendering.")
                    else:
                         # This would be strange if insert succeeded but _id wasn't added
                         print("WARNING: insert_record succeeded but '_id' was not found in extracted_data after insert.")

                    # Now extracted_data should be safely JSON serializable for the template
                    print(f"DEBUG: Rendering report. Record ID (str): {str_record_id}")

                    try:
                         # Pass the modified extracted_data (without _id) to the template
                         return render_template('report.html', data=extracted_data, record_id=str_record_id)
                    except Exception as render_err:
                         # Catch errors specifically during template rendering
                         print(f"ERROR: Exception occurred during render_template: {render_err}")
                         traceback.print_exc() # Print full traceback for render error
                         flash('Error occurred while generating the report page. Please check logs.', 'error')
                         return redirect(url_for('index')) # Redirect on render error

                else:
                    # Handle DB insert failure (record_id is None)
                    flash('File processed, but failed to store data in MongoDB. Check server logs.', 'error')
                    # Show extracted data (which won't have _id here) even if DB fails
                    return render_template('report.html', data=extracted_data, error='Failed to save to database.')
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
                flash(f'OCR processing failed: {error_message}', 'error')
                return render_template('report.html', error=error_message, raw_response=raw_response)
        else:
             # This case might occur if PDF conversion failed and returned None
             print("Error: No processable image was available after preprocessing.")
             flash('Could not obtain a processable image from the uploaded file.', 'error')
             # Render report page with specific error
             return render_template('report.html', error='Could not obtain a processable image.')

    # --- 7. Global Exception Handling ---
    except Exception as e:
        # Catch any unexpected errors during the entire process
        error_type = type(e).__name__
        error_msg = str(e)
        # Log the detailed error and traceback for backend debugging
        print(f"ERROR: An unexpected error occurred during upload/processing ({error_type}): {error_msg}")
        traceback.print_exc() # Print the full stack trace to the console/log file

        # Flash a user-friendly, generic, and JSON-serializable message
        flash(f'An unexpected error occurred ({error_type}). Please check server logs or contact support.', 'error')
        # Redirect to the index page on major errors
        return redirect(url_for('index'))

    # --- 8. Cleanup ---
    finally:
        # This block executes whether the try block succeeded or failed
        print("Cleaning up temporary files...")
        # Clean up the original temporary uploaded file
        if 'temp_filepath' in locals() and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"Removed temporary upload: {temp_filepath}")
            except OSError as e:
                print(f"Error removing temporary file {temp_filepath}: {e}")

        # Clean up the generated image ONLY if it was created and is different from the temp upload
        if 'generated_image_path' in locals() and generated_image_path and generated_image_path != temp_filepath and os.path.exists(generated_image_path):
            try:
                os.remove(generated_image_path)
                print(f"Removed generated image: {generated_image_path}")
            except OSError as e:
                print(f"Error removing generated image {generated_image_path}: {e}")


# Optional: Route to serve uploaded files (use with caution in production)
# Useful for debugging if you want to see the exact file processed
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves files from the upload folder. Use with caution."""
    # Basic security: Prevent directory traversal
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.abspath(safe_path).startswith(os.path.abspath(app.config['UPLOAD_FOLDER'])):
        return "Forbidden", 403
    # Check if file exists
    if not os.path.exists(safe_path) or not os.path.isfile(safe_path):
        return "File not found", 404
    # Consider adding more security checks if needed (e.g., check against a DB record)
    print(f"Serving file: {filename}") # Log access
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# --- Application Startup ---
if __name__ == '__main__':
    # Check initial DB connection before starting the server
    # db_connection_error is set when db_handler module is loaded
    if db_connection_error:
         print(f"\n{'='*20} WARNING {'='*20}")
         print(f"Initial MongoDB connection failed: {db_connection_error}")
         print("The application will run, but database operations will likely fail.")
         print("Please check your .env file and MongoDB server status.")
         print(f"{'='*50}\n")
         # Consider exiting if DB is critical: exit(1)
    else:
         print("Initial MongoDB connection check successful.")

    # Run the Flask development server
    # Use debug=True only for development - it enables auto-reloading and the debugger
    # For production, use a proper WSGI server like Gunicorn or uWSGI
    # Read FLASK_DEBUG env var for debug mode control
    is_debug_mode = os.environ.get('FLASK_DEBUG', 'True').lower() in ['true', '1', 'yes']
    print(f"Starting Flask app (Debug mode: {is_debug_mode})...")
    app.run(debug=is_debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))