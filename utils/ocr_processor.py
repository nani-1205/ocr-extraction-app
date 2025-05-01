# utils/ocr_processor.py
import os
import google.generativeai as genai
from PIL import Image
import json
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('GOOGLE_API_KEY')

if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set!")

genai.configure(api_key=API_KEY)

# --- IMPORTANT: The Prompt ---
# This prompt guides Gemini to extract structured data.
# It includes examples for common document types.
# You might need to refine this prompt based on the specific documents you encounter.
EXTRACTION_PROMPT = """
Analyze the provided image, which is a picture of a document (like an ID card, passport, etc.).
Identify the key information fields present in the document and extract their values.
Return the extracted information strictly as a JSON object.

Here are examples of expected JSON structures for common document types:

Example 1: Emirates ID
{
  "document_type": "Emirates ID",
  "country": "UNITED ARAB EMIRATES",
  "residence_type": "RESIDENCE / IDENTITY CARD", // Extract the exact text
  "residence_status": "RESIDENT / Specific Status if mentioned", // Extract status if present, otherwise null or omit
  "id_number": "784-xxxx-xxxxxxx-x",
  "name": "Full Name",
  "nationality": "Nationality", // Add if visible
  "date_of_birth": "YYYY/MM/DD", // Add if visible
  "expiry_date": "YYYY/MM/DD",
  "signature_holder": "Yes / No / Cannot Determine", // Add if visible
  "card_number": "Card number if visible on back or front", // Add if visible
  "place_of_issue": "Place if mentioned" // Add if visible
}

Example 2: Passport (Generic)
{
  "document_type": "Passport",
  "issuing_country_code": "XXX", // e.g., ARE, IND, USA
  "issuing_country_name": "Full Country Name",
  "passport_no": "Passport Number",
  "surname": "Surname",
  "given_names": "Given Names",
  "nationality": "Nationality",
  "date_of_birth": "DD MMM YYYY / YYYY-MM-DD", // Extract format as seen
  "sex": "M / F / X",
  "place_of_birth": "City, Country",
  "date_of_issue": "DD MMM YYYY / YYYY-MM-DD",
  "date_of_expiry": "DD MMM YYYY / YYYY-MM-DD",
  "authority": "Issuing Authority",
  "personal_no": "Personal ID Number if present" // e.g., Emirates ID number on UAE passport
}

Example 3: Generic Document / Unable to Classify
{
  "document_type": "Unknown / Other",
  "extracted_fields": {
    "field_name_1": "value_1",
    "field_name_2": "value_2"
    // Add any key-value pairs found
  },
  "raw_text": "Full OCR text if structured extraction fails" // Optional fallback
}

Instructions:
1. Analyze the image content carefully.
2. Determine the document type if possible (e.g., "Emirates ID", "Passport", "Invoice", "Receipt", "Unknown").
3. Extract all relevant key-value pairs. Use the field names shown in the examples where applicable. If a field from the examples isn't present, omit it. If additional relevant fields are found, include them.
4. Format dates as they appear or in YYYY/MM/DD or YYYY-MM-DD format if possible.
5. Ensure the output is **ONLY** a valid JSON object, enclosed in ```json ... ``` if necessary, but ideally just the raw JSON. Do not include any other text, explanations, or markdown formatting outside the JSON structure itself.
6. If the image is unclear or extraction is not possible, return a JSON object like: {"error": "Could not extract data from image.", "document_type": "Unclear"}
"""

# Set up the model
generation_config = {
  "temperature": 0.2, # Lower temperature for more deterministic output
  "top_p": 1,
  "top_k": 32,
  "max_output_tokens": 4096, # Adjust as needed
}

safety_settings = [
  {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
  {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
  {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
  {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

# Use gemini-pro-vision model
model = genai.GenerativeModel(model_name="gemini-pro-vision",
                              generation_config=generation_config,
                              safety_settings=safety_settings)

def extract_data_with_gemini(image_path):
    """
    Uses Gemini Pro Vision to extract structured data from an image.

    Args:
        image_path (str): Path to the image file.

    Returns:
        dict: A dictionary containing the extracted data or an error message.
              Returns None if a critical error occurs during processing.
    """
    print(f"Processing image: {image_path} with Gemini Pro Vision...")
    try:
        img = Image.open(image_path)
    except FileNotFoundError:
        print(f"Error: Image file not found at {image_path}")
        return {"error": f"Image file not found: {os.path.basename(image_path)}"}
    except Exception as e:
        print(f"Error opening image {image_path}: {e}")
        return {"error": f"Failed to open image: {os.path.basename(image_path)}"}

    # Prepare the prompt parts for the API call
    prompt_parts = [
        EXTRACTION_PROMPT, # The detailed instructions and examples
        img,             # The image object itself
    ]

    try:
        # Make the API call
        response = model.generate_content(prompt_parts)

        # --- Response Parsing ---
        if not response.parts:
             # Check if the response was blocked due to safety settings or other reasons
            try:
                 # Attempt to access prompt_feedback for block reason
                 block_reason = response.prompt_feedback.block_reason
                 error_message = f"Extraction blocked by safety settings or API policy. Reason: {block_reason}"
                 print(f"Warning: {error_message}")
                 return {"error": error_message, "document_type": "Blocked"}
            except Exception:
                 # If prompt_feedback is not available or doesn't have block_reason
                 error_message = "Extraction failed. Received an empty response from the API."
                 print(f"Error: {error_message}")
                 return {"error": error_message, "document_type": "API Error"}


        response_text = response.text

        # Clean the response text - Gemini might sometimes wrap JSON in markdown backticks
        cleaned_response = response_text.strip()
        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response[7:]
        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[:-3]
        cleaned_response = cleaned_response.strip()

        # Attempt to parse the cleaned text as JSON
        try:
            extracted_data = json.loads(cleaned_response)
            if not isinstance(extracted_data, dict):
                 print(f"Warning: Gemini response parsed but is not a dictionary: {type(extracted_data)}")
                 return {"error": "API response format unexpected (not a JSON object).", "raw_response": cleaned_response}

            print("Successfully extracted data.")
            # print(f"Extracted Data: {json.dumps(extracted_data, indent=2)}") # DEBUG
            return extracted_data

        except json.JSONDecodeError as json_err:
            print(f"Error: Failed to decode JSON response from Gemini: {json_err}")
            print(f"Raw Gemini Response Text:\n{response_text}") # Log the raw response for debugging
            return {"error": "Failed to parse API response as JSON.", "raw_response": response_text}

    except genai.types.generation_types.BlockedPromptException as bpe:
         error_message = f"Extraction blocked by safety settings or API policy. Reason: {bpe}"
         print(f"Warning: {error_message}")
         return {"error": error_message, "document_type": "Blocked"}
    except Exception as e:
        # Catch other potential API errors (network issues, invalid key etc.)
        print(f"An error occurred during the Gemini API call: {e}")
        # You might want to check for specific exception types from the google-generativeai library
        return {"error": f"An unexpected error occurred during AI processing: {e}"}