# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by Pillow
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copy project files for installation
COPY pyproject.toml ./
COPY src/ ./src/

# Install Python dependencies
RUN pip install --no-cache-dir .

# Expose webhook port (default 8443)
EXPOSE 8443

# Run the bot
CMD ["python", "-m", "bot.bot_runner"]
