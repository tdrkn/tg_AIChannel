FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . .

# Ensure the local package path is on PYTHONPATH
ENV PYTHONPATH=/app

# Run as a module so imports resolve correctly
CMD ["python", "-m", "src.main"]
