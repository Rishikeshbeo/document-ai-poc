# One image, two roles: the service and the sample host application. They are
# the same code, started with different commands (see docker-compose.yml), which
# keeps the build simple and means there is only one thing to rebuild.

FROM python:3.12-slim

# Fail fast and log straight through, so `docker logs` shows the startup banner
# and any traceback without buffering.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so editing the code does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The demo invoices, including the pair a good model reads wrongly. Generated
# rather than committed into the image, so they always match make_samples.py.
RUN python make_samples.py

# The database holds registered applications and everything learned, so it lives
# on a volume — a rebuilt container keeps its clients and their corrections.
ENV DOCAI_DB=/data/docai.sqlite3
VOLUME ["/data"]

EXPOSE 8077 8090

# 0.0.0.0 because a container's localhost is not reachable from the host.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
