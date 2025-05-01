# Dockerfile.app

# Start with a Python base image
FROM python:3.9

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1 # Prevents python creating .pyc files
ENV PYTHONUNBUFFERED 1       # Prevents python buffering stdout/stderr

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that might be needed for packages like Pillow or PyMuPDF
# This might need adjustments based on the exact base image and package requirements
# For Debian/Ubuntu based images (like python:3.9):
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpoppler-cpp-dev \
    pkg-config \
    # Add any other system libs needed by PyMuPDF or other dependencies
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the working directory
COPY . .

# Create the uploads directory if it doesn't exist (though it should from COPY .)
# Ensure the user running the app has permissions. We'll run as root by default,
# but for better security, consider creating and using a non-root user.
RUN mkdir -p /app/uploads && chown -R nobody:nogroup /app/uploads
# Expose the port Gunicorn will run on *inside* the container
EXPOSE 5000

# Define the command to run the application using Gunicorn
# -w 4: Number of worker processes (adjust based on CPU cores available)
# -b 0.0.0.0:5000: Bind to all network interfaces on port 5000 inside the container
# app:app: Look for the Flask app instance named 'app' in the file 'app.py'
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]