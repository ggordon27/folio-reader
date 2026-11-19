FROM python:3.11-slim

WORKDIR /app

COPY server.py extract_meta_helper.py tag_seed.json ./
COPY static/ ./static/

ENV PYTHONUNBUFFERED=1
ENV FOLIO_DATA_DIR=/data

EXPOSE 8765

CMD ["python3", "server.py"]
