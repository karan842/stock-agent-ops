FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential git && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["python", "main.py"]
