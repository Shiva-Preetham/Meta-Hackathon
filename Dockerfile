# Signal: offline demo image. Serves the dashboard on :8000 with the synthetic sample.
# Online mode: docker run -e MODEL_API_KEY=... -p 8000:8000 signal
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY signalhq ./signalhq
COPY data/sample ./data/sample
COPY scripts ./scripts

ENV SIGNAL_DB_PATH=/data/signal.db \
    SIGNAL_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "signalhq", "demo", "--host", "0.0.0.0", "--port", "8000"]
