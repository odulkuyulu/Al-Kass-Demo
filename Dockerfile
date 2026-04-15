FROM python:3.12-slim

# Install system deps for Azure Speech SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 libasound2 libgstreamer1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# gunicorn + gevent for WebSocket support
CMD ["gunicorn", \
     "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "alkass_translation.web_app:app"]
