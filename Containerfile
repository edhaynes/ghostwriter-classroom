FROM registry.access.redhat.com/ubi9/python-311:latest

USER root

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set permissions for OpenShift
RUN chown -R 1001:0 /app && \
    chmod -R g=u /app

USER 1001

# Expose port
EXPOSE 8081

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
