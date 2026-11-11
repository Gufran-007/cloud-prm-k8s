# ---------------------------------------------------------------
# Dockerfile for cloud-prm (web front-end + forecasting pipeline)
# Build:  docker build -t cloud-prm:v1 .
# Run:    docker run -p 8000:8000 cloud-prm:v1
# ---------------------------------------------------------------

# 1. Base image: a small official Debian image with Python 3.13
FROM python:3.13-slim

# 2. Python settings: no .pyc files, print logs immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3. All following commands run inside /app in the image
WORKDIR /app

# 4. Install dependencies first, so Docker can cache this layer
#    and skip it on rebuilds when only the code changes
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# 5. Copy the project code into the image
COPY src/ ./src/
COPY config/ ./config/
COPY run.py app.py ./

# 6. Do not run as root: create an ordinary user and switch to it
RUN useradd --create-home --uid 10001 appuser
USER appuser

# 7. Document the port the app listens on
EXPOSE 8000

# 8. Start command: gunicorn (a production web server) serving app.py
#    one worker process with 4 threads; 120 s timeout for slow requests
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", \
     "--timeout", "120", "app:app"]
