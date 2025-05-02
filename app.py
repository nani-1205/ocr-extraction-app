# app.py (FastAPI Version)
import os
import uuid
import shutil # For saving UploadFile efficiently
from fastapi import FastAPI, File, UploadFile, Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from werkzeug.utils import secure_filename # Still useful for filenames
from dotenv import load_dotenv
import datetime
import time
import traceback
from typing import Optional, Dict, Any # For type hinting

# Load environment variables FIRST
load_dotenv()

# Import utility functions AFTER loading env vars
from utils import db_handler
# db_connection_error = db_handler.connection_error # Check status if needed at startup
from utils.ocr_processor import extract_data_with_gemini
from utils.file_converter import convert_pdf_to_image, is_image_file, is_pdf_file

# --- Configuration ---
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}
API_SECRET_KEY = os.getenv('API_SECRET_KEY') # Load the API key

# --- FastAPI App Instance ---
# Add metadata for OpenAPI docs
app = FastAPI(
    title="OCR Extraction API",
    description="Uploads document files (Image or PDF) and extracts structured data using Google Gemini Vision.",
    version="1.1.0", # Example version
    contact={
        "name": "API Support",
        "url": "http://example.com/support", # Replace with actual URL if available
        "email": "support@example.com",    # Replace with actual email
    },
    license_info={
        "name": "Apache 2.0", # Or your chosen license
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)

# Ensure upload folder exists at startup
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    print(f"Upload folder '{UPLOAD_FOLDER}' ensured.")
except OSError as e:
    print(f"Error creating upload folder {UPLOAD_FOLDER}: {e}")
    # Consider exiting if the folder is critical and cannot be created


# --- Helper Functions ---
def allowed_file(filename: str):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- API Key Dependency ---
async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """Dependency function to verify the provided API key via the X-API-Key header."""
    if not API_SECRET_KEY:
        print("CRITICAL SERVER ERROR: API_SECRET_KEY is not configured.")
        # Do not expose internal state; raise a standard server error for the client.
        raise HTTPException(status_code=503, detail="Service Unavailable: API Key configuration error.")
    if not x_api_key or x_api_key != API_SECRET_KEY:
        print(f"API Authentication Failed. Provided Key: {'Present but Invalid' if x_api_key else 'Missing'}")
        raise HTTPException(status_code=401, detail="Unauthorized: Missing or invalid API key.")
    # If the key is valid, we don't need to return it, just let the request proceed.
    # return x_api_key


# --- Core Processing Function (Async Version) ---
async def process_uploaded_file(file: UploadFile, original_filename: str) -> Dict[str, Any]:
    """
    Handles saving, preprocessing, OCR, and DB insertion for an uploaded file.
    Raises HTTPException on failure.

    Args:
        file (UploadFile): The file object from FastAPI.
        original_filename (str): The original filename provided by the user (already secured).

    Returns:
        dict: Dictionary containing success data {"status": "success", "data": ..., "record_id": ...}.

    Raises:
        HTTPException: On any processing error (file type, conversion, OCR, DB).
    """
    temp_filename = None
    temp_filepath = None
    generated_image_path = None
    image_to_process_path = None

    try:
        # --- Prepare Filenames ---
        unique_id = uuid.uuid4().hex
        safe_original_filename = secure_filename(original_filename)
        if not safe_original_filename or '.' not in safe_original_filename:
             print(f"Error: Invalid original filename after securing: {original_filename}")
             raise HTTPException(status_code=400, detail="Invalid or disallowed filename.")
        file_ext = safe_original_filename.rsplit('.', 1)[1].lower()
        temp_filename = f"{unique_id}.{file_ext}"
        temp_filepath = os.path.join(UPLOAD_FOLDER, temp_filename)

        # --- 1. Save Uploaded File ---
        try:
            with open(temp_filepath, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            print(f"File temporarily saved to: {temp_filepath}")
        except Exception as save_err:
             print(f"Error saving uploaded file: {save_err}")
             raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {save_err}")
        finally:
            await file.close()

        # --- 2. File Preprocessing (PDF to Image) ---
        if is_image_file(temp_filename):
            image_to_process_path = temp_filepath
            print("Processing as image file.")
        elif is_pdf_file(temp_filename):
            print("Processing as PDF file. Attempting conversion to image...")
            # Note: convert_pdf_to_image is synchronous. If performance becomes an issue
            # for large PDFs, consider running it in a separate thread or process pool
            # using something like `run_in_threadpool` from `starlette.concurrency`.
            generated_image_path = convert_pdf_to_image(temp_filepath, UPLOAD_FOLDER)
            if generated_image_path:
                image_to_process_path = generated_image_path
                print(f"PDF converted to image: {generated_image_path}")
            else:
                print("Error: Failed to convert PDF to image.")
                raise HTTPException(status_code=400, detail="Failed to convert PDF to image for processing.")
        else:
             print("Error: Unsupported file type after save (internal error).")
             raise HTTPException(status_code=400, detail="Unsupported file type.")

        # --- 3. OCR Processing ---
        if image_to_process_path:
            print(f"Starting OCR processing for: {image_to_process_path}")
            start_time = time.time()
            # Note: extract_data_with_gemini is synchronous (likely involves network I/O).
            # For high concurrency, consider running in a thread pool.
            extracted_data = extract_data_with_gemini(image_to_process_path)
            end_time = time.time()
            processing_duration = round(end_time - start_time, 2)
            print(f"Gemini processing took {processing_duration:.2f} seconds.")

            # --- 4. Handle OCR Results ---
            if extracted_data and 'error' not in extracted_data:
                extracted_data['_metadata'] = {
                    'original_filename': safe_original_filename,
                    'upload_timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
                    'processing_time_seconds': processing_duration,
                    'processed_file': os.path.basename(image_to_process_path)
                }

                # --- 5. Store in MongoDB ---
                print("Attempting to insert extracted data into MongoDB...")
                # Note: insert_record is synchronous.
                record_id = db_handler.insert_record(extracted_data)

                if record_id:
                    str_record_id = str(record_id)
                    print(f"Successfully inserted. Record ID (str): {str_record_id}")
                    extracted_data.pop('_id', None) # Remove ObjectId before returning
                    return {"status": "success", "data": extracted_data, "record_id": str_record_id} # Return success dict
                else:
                    print("Error: Failed to store data in MongoDB.")
                    extracted_data.pop('_id', None)
                    raise HTTPException(status_code=500, detail="File processed, but failed to store data in MongoDB.")
            else:
                # Handle errors reported by Gemini
                error_message = "Unknown error during OCR processing."
                raw_response = None
                if extracted_data and 'error' in extracted_data:
                     error_message = extracted_data.get('error')
                     raw_response = extracted_data.get('raw_response')
                elif extracted_data is None:
                     error_message = "OCR processing function returned None."

                print(f"OCR processing failed: {error_message}")
                detail = f"OCR processing failed: {error_message}"
                if raw_response:
                    # Avoid sending potentially huge raw responses in error detail
                    detail += f" | Raw Response Snippet: {str(raw_response)[:200]}..."

                status_code = 502 if "API Error" in error_message or "API policy" in error_message or "blocked" in error_message.lower() else 500
                raise HTTPException(status_code=status_code, detail=detail)
        else:
             print("Error: No processable image was available after preprocessing step.")
             raise HTTPException(status_code=500, detail="Could not obtain a processable image from the file.")

    # --- 6. Exception Handling ---
    except HTTPException as http_exc:
         # Re-raise HTTPExceptions to be handled by FastAPI's default handler
         raise http_exc
    except Exception as e:
        # Catch any other unexpected errors during processing
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"ERROR: An unexpected error occurred during file processing ({error_type}): {error_msg}")
        traceback.print_exc()
        # Raise as an HTTPException so FastAPI returns a proper JSON error response
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred ({error_type}).")

    # --- 7. Cleanup ---
    finally:
        # This block executes regardless of success or failure
        print("Cleaning up temporary files...")
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"Removed temporary upload: {temp_filepath}")
            except OSError as e:
                print(f"Error removing temporary file {temp_filepath}: {e}")
        if generated_image_path and generated_image_path != temp_filepath and os.path.exists(generated_image_path):
            try:
                os.remove(generated_image_path)
                print(f"Removed generated image: {generated_image_path}")
            except OSError as e:
                print(f"Error removing generated image {generated_image_path}: {e}")


# --- API Endpoint Definition ---
@app.post(
    "/api/v1/extract", # URL Path
    status_code=200,    # Default status code on success
    summary="Upload Document for OCR Extraction",
    description="Upload an image (PNG, JPG, JPEG) or PDF file. The API processes the file "
                "using Google Gemini Vision, extracts structured data based on internal prompting, "
                "stores the result in MongoDB, and returns the extracted data and the database record ID.",
    tags=["OCR Extraction"] # Tag for grouping in API docs
)
async def extract_api(
    # Use Depends for API Key verification. It will raise HTTPException if invalid.
    api_key_verified: None = Depends(verify_api_key),
    # Define the file upload parameter. FastAPI handles multipart/form-data.
    file: UploadFile = File(..., description="The document file (image or PDF) to process.")
):
    """
    Main API endpoint. Requires API key authentication. Accepts a file upload,
    processes it, and returns the extracted JSON data or an error.
    """
    # 1. Basic File Validation (Filename check, Allowed Type)
    filename = file.filename
    if not filename:
        raise HTTPException(status_code=400, detail="No file selected or filename missing.")
    if not allowed_file(filename):
        raise HTTPException(status_code=400, detail=f"File type '{filename.rsplit('.', 1)[-1]}' not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}.")

    # 2. Secure the filename
    safe_filename = secure_filename(filename)
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid or disallowed filename.")

    # 3. Call the core processing function
    # It will return the success dictionary or raise an HTTPException
    result_data = await process_uploaded_file(file, safe_filename)

    # 4. Return successful result (FastAPI automatically converts dict to JSON)
    return result_data


# --- Optional Root Endpoint for Health Check / Info ---
@app.get("/", tags=["Status"], summary="API Status")
async def read_root():
    """Basic status endpoint to check if the API is running."""
    # Could add DB connection check here if desired
    # status = "OK" if not db_handler.connection_error else "Error: DB Connection Issue"
    status = "OK"
    return {"api_status": status, "version": app.version}

# --- Global Exception Handler (Optional but Recommended) ---
# This catches any Exception not already handled as an HTTPException
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handles any unexpected errors not caught elsewhere, returning a JSON response."""
    error_type = type(exc).__name__
    print(f"ERROR: Global exception handler caught unhandled error ({error_type}) for request {request.url}: {exc}")
    traceback.print_exc()
    # Return a generic server error response
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": f"An internal server error occurred: {error_type}"},
    )


# --- Application Startup Logic ---
# This block is mainly for running the app directly using `python app.py` for local development.
# When run via Gunicorn in Docker, Gunicorn handles the execution based on the CMD.
if __name__ == "__main__":
    # Perform startup checks (optional but helpful)
    if db_handler.connection_error:
         print(f"\n{'='*20} WARNING {'='*20}")
         print(f"Initial MongoDB connection failed: {db_handler.connection_error}")
         print("Database operations will likely fail.")
         print(f"{'='*50}\n")
    else:
         print("Initial MongoDB connection check successful.")

    if not API_SECRET_KEY:
        print(f"\n{'='*20} WARNING {'='*20}")
        print("API_SECRET_KEY is not set in the environment variables (.env).")
        print("The API endpoint '/api/v1/extract' will be inaccessible without a valid key.")
        print(f"{'='*50}\n")

    # Use Uvicorn for local development when running `python app.py`
    import uvicorn
    print("Starting FastAPI app with Uvicorn development server...")
    # `reload=True` automatically restarts the server when code changes.
    # Use port 8000 as standard for FastAPI/Uvicorn development.
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)