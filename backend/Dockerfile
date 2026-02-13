FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential git && rm -rf /var/lib/apt/lists/*

# Copy everything from root (backend/ + src/ + others)
COPY . /app

# Set python path so we can import 'backend.state', 'src.utils' etc
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir -r backend/requirements.txt

EXPOSE 8000

CMD ["python", "-m", "backend.main"]
