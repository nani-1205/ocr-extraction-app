# utils/ocr_processor.py
import os
import google.generativeai as genai
from google.api_core import exceptions as google_api_exceptions # For specific API error handling
from PIL import Image
import json
from dotenv import load_dotenv

# --- Load Environment Variables ---
# Load from .env file in the parent directory or current directory
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv() # Load from current directory or standard locations


API_KEY = os.getenv('GOOGLE_API_KEY')

if not API_KEY:
    # Use a more specific error or handle it gracefully depending on application needs
    raise ValueError("GOOGLE_API_KEY environment variable not set or not found in .env file!")

genai.configure(api_key=API_KEY)

# --- Prompt for Structured Data Extraction ---
# This prompt guides Gemini to extract structured data.
# It includes examples for common document types.
# Refine this prompt based on the specific documents and required output structure.
EXTRACTION_PROMPT = """
Analyze the provided image, which is a picture of a document (like an ID card, passport, invoice, receipt, etc.).
Identify the key information fields present in the document and extract their values.
Return the extracted information strictly as a JSON object that adheres to the requested format.

Here are examples of potential JSON structures for common document types:

Example 1: Emirates ID
{
  "document_type": "Emirates ID",
  "country": "UNITED ARAB EMIRATES",
  "residence_type": "RESIDENCE / IDENTITY CARD", // Extract the exact text for type
  "residence_status": "RESIDENT", // Extract status if present, otherwise null
  "id_number": "784-xxxx-xxxxxxx-x",
  "name": "Full Name As Printed",
  "nationality": "Nationality",
  "date_of_birth": "YYYY/MM/DD", // Or format as seen if different
  "expiry_date": "YYYY/MM/DD",
  "signature_holder": "Yes / No / Cannot Determine",
  "card_number": "Card number if visible", // e.g., from back side
  "place_of_issue": "Place if mentioned" // e.g., DUBAI
}

Example 2: Passport (Generic)
{
  "document_type": "Passport",
  "issuing_country_code": "XXX", // e.g., ARE, IND, USA, SGP
  "issuing_country_name": "Full Country Name", // e.g., REPUBLIC OF SINGAPORE
  "passport_no": "Passport Number", // e.g., K0000000E
  "surname": "Surname", // e.g., WONG
  "given_names": "Given Names", // e.g., KARA YUN EN
  "nationality": "Nationality", // e.g., SINGAPORE CITIZEN
  "date_of_birth": "DD MMM YYYY", // Extract format as seen, e.g., 03 MAY 1977
  "sex": "M / F / X", // e.g., F
  "place_of_birth": "City, Country", // e.g., SINGAPORE
  "date_of_issue": "DD MMM YYYY", // e.g., 30 OCT 2017
  "date_of_expiry": "DD MMM YYYY", // e.g., 30 OCT 2022
  "authority": "Issuing Authority", // e.g., MINISTRY OF HOME AFFAIRS
  "personal_no": "Personal ID Number if present" // e.g., S7788888H (National ID No)
}

Example 3: Generic Document / Unable to Classify
{
  "document_type": "Unknown / Other / Specific Type (e.g., Invoice)",
  "extracted_fields": {
    "field_name_1": "value_1",
    "field_name_2": "value_2"
    // Add any key-value pairs found
  },
  "raw_text": "Full OCR text if structured extraction fails significantly" // Optional fallback
}

Instructions:
1. Carefully analyze the image content. Prioritize accuracy.
2. Determine the document type if possible (e.g., "Emirates ID", "Passport", "Invoice", "Receipt", "Unknown"). Use the "document_type" field.
3. Extract all relevant key-value pairs. Use the field names shown in the examples where applicable (e.g., "id_number", "passport_no", "name", "expiry_date"). Use standard field names like 'surname' and 'given_names' for passports if the label is just 'Name'.
4. If a field from the examples is not present in the document, omit it entirely from the JSON output.
5. If additional relevant fields are clearly identifiable (e.g., "company", "occupation", "file_number"), include them using descriptive snake_case keys (e.g., "company_name", "job_title").
6. Format dates as they appear on the document or consistently as YYYY/MM/DD or YYYY-MM-DD if possible. For passports, often DD MMM YYYY is used.
7. Ensure the output is **ONLY** a single, valid JSON object. Do not include any text, explanations, apologies, or markdown formatting (like ```json ... ```) outside the JSON structure itself.
8. If the image is completely unreadable, illegible, or not a document, return a JSON object like: {"error": "Could not extract data from image. Image unclear or not a document.", "document_type": "Unclear"}
"""

# --- Model Configuration ---
generation_config = {
  "temperature": 0.2, # Lower temperature for more factual, less creative output
  "top_p": 0.95,
  "top_k": 40,        # Adjusted Top K
  "max_output_tokens": 8192, # Increased token limit for potentially complex documents with Gemini 1.5
  "response_mime_type": "application/json", # Request JSON output directly
}

# --- Safety Settings ---
# Adjusted HARM_CATEGORY_DANGEROUS_CONTENT threshold to be less strict
# This may be necessary for processing documents like passports/IDs which can
# sometimes be flagged, but be mindful of Google's AUP.
safety_settings = [
  {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
  {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
  {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
  # --- V V V --- ADJUSTED THRESHOLD HERE --- V V V ---
  {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"} # Less strict than BLOCK_MEDIUM_AND_ABOVE
  # --- ^ ^ ^ --- END OF ADJUSTMENT --- ^ ^ ^ ---
]

# --- Initialize the Generative Model ---
# Use the recommended replacement model (gemini-1.5-flash or gemini-1.5-pro)
# Using "-latest" points to the most recent stable version.
MODEL_NAME = "gemini-1.5-flash-latest" # Or "gemini-1.5-pro-latest" for potentially higher quality but slower/more expensive
try:
    model = genai.GenerativeModel(model_name=MODEL_NAME,
                                  generation_config=generation_config,
                                  safety_settings=safety_settings)
    print(f"Initialized Generative AI Model: {MODEL_NAME}")
except Exception as model_init_err:
     print(f"FATAL: Failed to initialize Generative AI Model ({MODEL_NAME}): {model_init_err}")
     # Depending on application structure, you might want to raise this error
     # or handle it in a way that prevents the app from starting without the model.
     model = None # Ensure model is None if initialization fails


# --- Main Extraction Function ---
def extract_data_with_gemini(image_path):
    """
    Uses the configured Gemini model to extract structured data from an image file.

    Args:
        image_path (str): Path to the image file (e.g., JPG, PNG).

    Returns:
        dict: A dictionary containing the extracted data, or a dictionary with an 'error' key
              if extraction fails or an error occurs.
    """
    if model is None:
         print("Error: Generative AI model was not initialized successfully.")
         return {"error": "AI model not available."}

    print(f"Processing image: {os.path.basename(image_path)} with Gemini {model.model_name}...")
    try:
        img = Image.open(image_path)
        # Ensure image is in RGB format, as many models prefer it.
        # Handle potential transparency (alpha channel) in PNGs etc.
        if img.mode == 'RGBA' or img.mode == 'P': # P is palette mode
             img = img.convert('RGB')
        elif img.mode != 'RGB':
            # Attempt conversion for other modes if necessary, log a warning if unusual
            print(f"Warning: Image mode is {img.mode}. Attempting conversion to RGB.")
            try:
                 img = img.convert('RGB')
            except Exception as convert_err:
                 print(f"Error: Failed to convert image {os.path.basename(image_path)} to RGB: {convert_err}")
                 return {"error": f"Failed to convert image to suitable format: {os.path.basename(image_path)}"}

    except FileNotFoundError:
        print(f"Error: Image file not found at {image_path}")
        return {"error": f"Image file not found: {os.path.basename(image_path)}"}
    except Exception as e:
        # Catch other image opening/processing errors (e.g., corrupted file)
        print(f"Error opening or preparing image {os.path.basename(image_path)}: {e}")
        return {"error": f"Failed to open/prepare image: {os.path.basename(image_path)} - {e}"}

    # Prepare the prompt parts for the API call
    # The order can sometimes matter: instructions first, then the image.
    prompt_parts = [
        EXTRACTION_PROMPT, # The detailed instructions and examples
        img,               # The PIL Image object
    ]

    try:
        # Make the API call to generate content
        response = model.generate_content(prompt_parts)

        # --- Response Handling ---
        # Check for safety blocks first using prompt_feedback if available
        block_reason = None
        finish_reason_safety = False
        try:
            if response.prompt_feedback:
                block_reason = response.prompt_feedback.block_reason
                if block_reason:
                    error_message = f"Extraction blocked by API policy. Reason: {block_reason}"
                    print(f"Warning: {error_message}")
                    # Try to get more detail from safety_ratings if block_reason exists
                    details = " Check safety ratings in logs."
                    try:
                         if response.candidates and response.candidates[0].safety_ratings:
                              ratings_str = ", ".join([f"{r.category.name}={r.probability.name}" for r in response.candidates[0].safety_ratings])
                              details = f" Details: {ratings_str}"
                    except Exception: pass # Ignore if details aren't available
                    return {"error": error_message + details, "document_type": "Blocked"}
            # Check candidate finish reason too (sometimes block is here)
            if response.candidates and response.candidates[0].finish_reason.name == 'SAFETY':
                 finish_reason_safety = True

        except AttributeError:
            print("Warning: Could not access prompt_feedback or candidates for safety check.")
            pass # Continue processing, but be aware feedback might be missing

        # Check if the response is empty or blocked by safety (even if prompt_feedback didn't catch it)
        if not response.parts or finish_reason_safety:
             error_message = "Extraction failed."
             if finish_reason_safety:
                  error_message += " Reason: SAFETY."
                  # Log candidate info if available for debugging SAFETY blocks
                  try:
                       print(f"Candidate info (SAFETY block): {response.candidates}")
                       # Extract specific rating details if possible
                       if response.candidates and response.candidates[0].safety_ratings:
                            ratings_str = ", ".join([f"{r.category.name}={r.probability.name} (Blocked: {r.blocked})" for r in response.candidates[0].safety_ratings])
                            error_message += f" Details: {ratings_str}"
                  except Exception as log_err:
                       print(f"Error logging candidate info: {log_err}")
             elif not response.parts:
                  error_message += " Received an empty response from the API (no parts)."
             else:
                  # Should not happen if finish_reason_safety is True, but as fallback
                  error_message += " Unknown reason (empty parts or safety)."

             print(f"Error: {error_message}")
             return {"error": error_message, "document_type": "API Error/Blocked"}


        # Access the response text (should be JSON formatted due to mime_type)
        response_text = response.text
        # print(f"Raw Gemini Response Text:\n{response_text}") # Uncomment for deep debugging

        # Attempt to parse the response text as JSON
        try:
            extracted_data = json.loads(response_text)
            if not isinstance(extracted_data, dict):
                 # Model returned valid JSON, but not a JSON object (e.g., a list or string)
                 print(f"Warning: Gemini response parsed but is not a JSON object (dictionary): {type(extracted_data)}")
                 return {"error": "API response format unexpected (not a JSON object).", "raw_response": response_text}

            print("Successfully extracted and parsed JSON data from Gemini.")
            return extracted_data # Success!

        except json.JSONDecodeError as json_err:
            # The model failed to return valid JSON despite the mime_type request
            print(f"Error: Failed to decode JSON response from Gemini: {json_err}")
            print(f"Raw Gemini Response Text was:\n{response_text}") # Log the problematic text
            # Provide a structured error, including the raw response for debugging
            return {"error": "Failed to parse API response as JSON.", "raw_response": response_text}
        except Exception as parse_err:
             # Catch other potential errors during parsing
             print(f"An unexpected error occurred during response parsing: {parse_err}")
             return {"error": f"Unexpected response parsing error: {parse_err}", "raw_response": response_text}


    # --- Exception Handling for API Call ---
    except genai.types.generation_types.BlockedPromptException as bpe:
         # Specific exception for blocked prompts (might be less common now with feedback checks)
         error_message = f"Extraction blocked by API policy (BlockedPromptException). Reason: {bpe}"
         print(f"Warning: {error_message}")
         return {"error": error_message, "document_type": "Blocked"}

    except google_api_exceptions.GoogleAPIError as api_err:
         # Handle specific Google API errors (e.g., 4xx, 5xx status codes)
         print(f"A Google API error occurred during the Gemini call: {api_err}")
         # Try to get status code and message for better context
         error_details = f"Status Code: {api_err.code} - Message: {api_err.message}" if hasattr(api_err, 'code') and hasattr(api_err, 'message') else str(api_err)
         # Specific check for common issues like invalid API key or quota exceeded
         error_code = getattr(api_err, 'code', None)
         if error_code == 400: # Bad Request (often invalid API key format or model issues)
              error_details += " (Check API Key, Model Name, Request Format)"
         elif error_code == 403: # Forbidden (Permissions, API not enabled?)
              error_details += " (Check API Key Permissions / Billing / API Enabled status)"
         elif error_code == 429: # Resource Exhausted (Quota)
              error_details += " (Rate limit or quota exceeded)"
         elif error_code == 404: # Not Found (Model name typo? Endpoint issue?)
              error_details += f" (Model '{MODEL_NAME}' might be invalid or unavailable in region)"

         return {"error": f"Google API Error: {error_details}"}

    except Exception as e:
        # Catch other potential runtime errors (network issues, library bugs etc.)
        error_type = type(e).__name__
        print(f"An unexpected error occurred during the Gemini API call: {error_type} - {e}")
        return {"error": f"An unexpected error occurred during AI processing: {error_type} - {e}"}

# --- Optional: Example Usage (for testing the module directly) ---
if __name__ == '__main__':
    # This block runs only when the script is executed directly (e.g., python utils/ocr_processor.py)
    print("\n--- Testing OCR Processor ---")
    # Create a dummy image file path for testing (replace with a real path)
    # Make sure you have a test image (e.g., 'test_image.jpg') in the same directory or provide the full path
    test_image_path = 'test_image.jpg' # <--- PUT A REAL IMAGE PATH HERE FOR TESTING

    if os.path.exists(test_image_path):
        print(f"Attempting extraction from: {test_image_path}")
        extracted_info = extract_data_with_gemini(test_image_path)

        print("\n--- Extraction Result ---")
        if extracted_info:
            # Pretty print the JSON result
            print(json.dumps(extracted_info, indent=2))
        else:
            # This case should ideally not happen if extract_data_with_gemini always returns a dict
            print("Extraction function returned None or empty value.")

        if extracted_info and 'error' in extracted_info:
            print(f"\nExtraction failed with error: {extracted_info['error']}")
            if 'raw_response' in extracted_info:
                 print("\n--- Raw Response (if available) ---")
                 print(extracted_info['raw_response'])
    else:
        print(f"Test image not found at: {test_image_path}")
        print("Please place a test image (e.g., 'test_image.jpg') in the script's directory or update the path.")

    print("\n--- OCR Processor Test Complete ---")