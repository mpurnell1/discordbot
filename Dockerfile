# Gary's runtime image. Built on the NAS by deploy/deploy.sh; the SQLite DB
# and logs live on a bind mount (see docker-compose.yml), never in the image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
