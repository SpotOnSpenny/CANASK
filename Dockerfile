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

# Run as a non-root user (defense-in-depth: a compromised app process isn't root in the container).
# UID/GID 1000 matches the typical host dev user so the dev bind-mount stays writable (Flask-Assets
# builds bundles + a .webassets-cache under data_viz/static/ at runtime). In prod there is no mount, so
# chowning the baked-in code to appuser is what makes those runtime writes possible.
RUN groupadd -g 1000 appuser \
    && useradd -u 1000 -g 1000 -m appuser \
    && chown -R appuser:appuser /canask_webapp
USER appuser

# Default command: gunicorn (never the Werkzeug dev server) so running the image without a compose
# override fails safe. docker-compose.dev.yml overrides this to `flask run --debug`.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "data_viz:app"]