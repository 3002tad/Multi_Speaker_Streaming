FROM python:3.12-slim

WORKDIR /app
COPY meeting_service/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r /app/requirements.lock.txt
COPY meeting_service /app/meeting_service

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
EXPOSE 8002
CMD ["uvicorn", "meeting_service.app.main:socket_app", "--host", "0.0.0.0", "--port", "8002"]
