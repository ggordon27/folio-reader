FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py extract_meta_helper.py tag_seed.json metadata_seed.json ./
COPY folio/ ./folio/
COPY static/ ./static/

ENV PYTHONUNBUFFERED=1
ENV FOLIO_DATA_DIR=/data

EXPOSE 8765

CMD ["python3", "server.py"]
