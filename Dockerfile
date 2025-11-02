# Use the official Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /app

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Create a directory for token storage
RUN mkdir -p /data
VOLUME ["/data"]

# Environment variables (set via hosting platform)
ENV PYTHONUNBUFFERED=1

# Start the bot
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python3", "bot.py"]
