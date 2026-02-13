#!/bin/bash

echo "🐳 Starting MLOps Pipeline with Docker Compose..."

# Build and start in detached mode
docker-compose up --build -d

echo "✅ Deployment successful!"
echo "--------------------------------------------------"
echo "Services are available at:"
echo "- FastAPI: http://localhost:8000/docs"
echo "- Main UI: http://localhost:8501"
echo "- Monitoring: http://localhost:8502 (if defined in compose)"
echo "--------------------------------------------------"
echo "Use 'docker-compose logs -f' to see live logs."
echo "Use 'docker-compose down' to stop the services."
