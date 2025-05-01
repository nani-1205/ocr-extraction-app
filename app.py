# app.py
import os
import uuid
from flask import Flask, request, render_template, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import datetime
import time

# Import utility functions
from utils.db_handler import insert_record, get_record_by_id, connection_error as db_connection_error
from utils.ocr_processor import extract_data_with_gemini
from utils.file_converter import convert_pdf_to_image, is_image_file, is_pdf_file

# Load environment variables
load_dotenv()

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'} # Added pdf
MAX_CONTENT_LENGTH = 16 * 1024 * 1024 # 16 MB limit

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_very_secret_key_for_dev') # Use env var for production

# Ensure upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Routes ---
@app.route('/', methods=['GET'])
def index():
    # Check DB connection status on loading the main page
    if db_connection_error:
         flash(f"Warning: Database connection issue - {db_connection_error}", 'error')
    else:
         flash("Database connection appears okay.", 'info') # Optional success message

    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file part in the request.', 'error')
        return redirect(url_for('index'))

    file = request.files['file']

    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('index'))

    if not allowed_file(file.filename):
        flash(f"File type not allowed. Please upload {', '.join(ALLOWED_EXTENSIONS)}.", 'error')
        return redirect(url_for('index'))

    original_filename = secure_filename(file.filename)
    # Create a unique filename to avoid conflicts
    unique_id = uuid.uuid4().hex
    file_ext = original_filename.rsplit('.', 1)[1].lower()
    temp_filename = f"{unique_id}.{file_ext}"
    temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

    # Paths for potential converted image
    image_to_process_path = None
    generated_image_path = None # Keep track if we create a temp image from PDF

    try:
        file.save(temp_filepath)
        print(f"File temporarily saved to: {temp_filepath}")

        # --- File Preprocessing ---
        if is_image_file(temp_filename):
            image_to_process_path = temp_filepath
            print("Processing as image file.")
        elif is_pdf_file(temp_filename):
            print("Processing as PDF file. Attempting conversion to image...")
            # Convert PDF to image (saves as PNG in uploads folder)
            generated_image_path = convert_pdf_to_image(temp_filepath, app.config['UPLOAD_FOLDER'])
            if generated_image_path:
                image_to_process_path = generated_image_path
                print(f"PDF converted to image: {generated_image_path}")
            else:
                flash('Failed to convert PDF to image for processing.', 'error')
                # No need to redirect here, finally block will clean up temp_filepath
                return render_template('report.html', error='Failed to convert PDF to image.')
        # Add elif for DOCX here if implemented later
        else:
             # Should not happen due to allowed_file check, but good practice
             flash('Unsupported file type after save.', 'error')
             return redirect(url_for('index'))


        # --- OCR Processing ---
        if image_to_process_path:
            start_time = time.time()
            extracted_data = extract_data_with_gemini(image_to_process_path)
            end_time = time.time()
            print(f"Gemini processing took {end_time - start_time:.2f} seconds.")

            if extracted_data and 'error' not in extracted_data:
                # Add metadata before saving to DB
                extracted_data['_metadata'] = {
                    'original_filename': original_filename,
                    'upload_timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                    'processing_time_seconds': round(end_time - start_time, 2),
                    'processed_file': os.path.basename(image_to_process_path) # Image used for OCR
                }

                # --- Store in MongoDB ---
                record_id = insert_record(extracted_data)

                if record_id:
                    flash('File processed and data stored successfully!', 'success')
                    # Pass the whole data structure to the template
                    return render_template('report.html', data=extracted_data, record_id=str(record_id))
                else:
                    flash('File processed, but failed to store data in MongoDB.', 'error')
                    # Show extracted data even if DB fails, but indicate the storage error
                    return render_template('report.html', data=extracted_data, error='Failed to save to database.')
            else:
                # Handle errors reported by Gemini
                error_message = extracted_data.get('error', 'Unknown error during OCR processing.') if extracted_data else 'OCR processing failed unexpectedly.'
                flash(f'OCR processing failed: {error_message}', 'error')
                return render_template('report.html', error=error_message, raw_response=extracted_data.get('raw_response'))
        else:
             # This case might occur if PDF conversion failed and returned None
             flash('Could not obtain a processable image from the uploaded file.', 'error')
             return render_template('report.html', error='Could not obtain a processable image.')


    except Exception as e:
        print(f"An unexpected error occurred during upload/processing: {e}")
        flash(f'An unexpected error occurred: {e}', 'error')
        # Redirect to index on unexpected errors, as report page might not have context
        return redirect(url_for('index'))

    finally:
        # --- Cleanup Temporary Files ---
        print("Cleaning up temporary files...")
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"Removed temporary upload: {temp_filepath}")
            except OSError as e:
                print(f"Error removing temporary file {temp_filepath}: {e}")

        # Remove the generated image ONLY if it's different from the original upload
        if generated_image_path and generated_image_path != temp_filepath and os.path.exists(generated_image_path):
            try:
                os.remove(generated_image_path)
                print(f"Removed generated image: {generated_image_path}")
            except OSError as e:
                print(f"Error removing generated image {generated_image_path}: {e}")


# Optional: Route to serve uploaded files if needed (e.g., for debugging)
# Be cautious about serving user-uploaded content directly in production
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    # Basic security: Only allow access if the file actually exists
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return "File not found", 404
    # Add more security checks if needed (e.g., check against a DB record)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    # Check initial DB connection before starting the server
    if db_connection_error:
         print(f"FATAL: Could not connect to MongoDB on startup: {db_connection_error}")
         print("Please check your .env file and MongoDB server status.")
         # Exit if DB connection fails on startup (optional, but recommended)
         # exit(1)
    else:
         print("Initial MongoDB connection check successful.")

    # Run the app (use debug=True only for development)
    app.run(debug=True, host='0.0.0.0', port=5000)