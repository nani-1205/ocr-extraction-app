# utils/file_converter.py
import fitz  # PyMuPDF
import os
from PIL import Image
import io

def convert_pdf_to_image(pdf_path, output_folder):
    """
    Converts the first page of a PDF to a PNG image.
    Returns the path to the generated image or None if failed.
    """
    try:
        doc = fitz.open(pdf_path)
        if not doc or doc.page_count == 0:
            print(f"Error: Could not open or empty PDF: {pdf_path}")
            return None

        page = doc.load_page(0)  # Load the first page (index 0)
        pix = page.get_pixmap(dpi=300) # Render page to pixmap with higher DPI for better OCR
        doc.close()

        img_filename = f"{os.path.splitext(os.path.basename(pdf_path))[0]}.png"
        img_path = os.path.join(output_folder, img_filename)

        # Save pixmap data directly to PNG
        pix.save(img_path)

        # Optional: Optimize image size slightly using Pillow
        # try:
        #     img = Image.open(img_path)
        #     img.save(img_path, optimize=True)
        # except Exception as pil_err:
        #     print(f"Warning: Could not optimize image {img_path}: {pil_err}")

        print(f"Successfully converted {pdf_path} to {img_path}")
        return img_path

    except Exception as e:
        print(f"Error converting PDF {pdf_path} to image: {e}")
        # Clean up potentially incomplete image file if error occurred during saving
        if 'img_path' in locals() and os.path.exists(img_path):
            try:
                os.remove(img_path)
            except OSError as rm_err:
                print(f"Error cleaning up failed image {img_path}: {rm_err}")
        return None

def is_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

def is_pdf_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'pdf'

# Add is_docx_file if you implement DOCX handling later
# def is_docx_file(filename):
#     return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'docx'