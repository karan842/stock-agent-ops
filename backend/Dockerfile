# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# Install git
RUN apt-get update && apt-get install -y git

# Install uv for dependency management
RUN pip install uv

# Silence ONNXRuntime CPU vendor warnings
ENV ORT_LOGGING_LEVEL=ERROR

# Copy project files
# We expect the build context to be the project root
COPY . /app

# Install dependencies via uv (system-wide)
RUN uv pip install --system -r requirements.txt

# Expose the FastAPI port
EXPOSE 8000

# Run the FastAPI app with Uvicorn
# Updated entry point
CMD ["python", "main.py"]
