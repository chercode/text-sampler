# Use a slim official Python image
FROM python:3.9-slim

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Expose the port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]