FROM python:3.13-slim

WORKDIR /frontend

COPY . /frontend

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
