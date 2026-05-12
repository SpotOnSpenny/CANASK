FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /canask_webapp

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build arg to switch requirements file
ARG REQUIREMENTS_FILE=app_requirements/requirements.webapp.txt
COPY app_requirements/ app_requirements/
RUN pip install --no-cache-dir -r ${REQUIREMENTS_FILE}

# Copy project
COPY . .

# Default command (overridden in docker-compose)
CMD ["flask", "run", "--host=0.0.0.0"]