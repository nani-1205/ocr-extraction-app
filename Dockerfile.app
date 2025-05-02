# Dockerfile.app (for FastAPI)

# Start with a Python base image
FROM python:3.9

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1 # Prevents python creating .pyc files
ENV PYTHONUNBUFFERED 1       # Prevents python buffering stdout/stderr

# Set the working directory in the container
WORKDIR /app

# Install system dependencies needed for packages like Pillow or PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpoppler-cpp-dev \
    pkg-config \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
# Use --no-cache-dir for potentially smaller image layers
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the working directory
COPY . .

# Create uploads dir and set permissions (adjust user/group if running non-root)
RUN mkdir -p /app/uploads && chown -R nobody:nogroup /app/uploads

# Expose the port the ASGI server (Uvicorn managed by Gunicorn) will run on inside the container
EXPOSE 8000

# Define the command to run the application using Gunicorn managing Uvicorn workers
# -w 4: Number of worker processes (adjust based on available CPU cores)
# -k uvicorn.workers.UvicornWorker: Specify Uvicorn worker class for ASGI compatibility
# -b 0.0.0.0:8000: Bind to all network interfaces on port 8000 inside the container
# app:app: Look for the FastAPI instance named 'app' in the file 'app.py'
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "app:app"]