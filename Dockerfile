FROM python:3.10-slim

WORKDIR /app

# Prevent Python from writing .pyc files & buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies (including tzdata for WAT/UTC conversions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt-get/lists/*

# Copy requirements file first (for efficient Docker layer caching)
COPY requirements.txt /app/

RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the modular application codebase
COPY . /app/

# Run the master bot engine
CMD ["python", "master_bot.py"]