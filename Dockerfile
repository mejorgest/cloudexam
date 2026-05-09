# =============================================================
# Multi-stage Dockerfile for React Agent IDE
# Backend: Python with FastAPI
# Frontend: TypeScript/React with Vite (built and served by FastAPI)
# =============================================================

# =================== STAGE 1: Build Frontend ===================
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

# Copy package files first for caching
COPY frontend/package*.json ./

# Install dependencies
RUN npm ci --production=false

# Copy frontend source
COPY frontend/ ./

# Build the frontend (creates dist/ folder)
RUN npm run build

# =================== STAGE 2: Python Backend ===================
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Configure git for versioning system
RUN git config --global --add safe.directory /app/workspace && \
    git config --global user.email "agent@localhost" && \
    git config --global user.name "React Agent"

# Install uv package manager (faster than pip)
RUN pip install uv

# Copy requirements first for caching
COPY requirements1.txt .

# Install Python dependencies
RUN uv pip install --system --no-cache -r requirements1.txt

# Copy all backend code
COPY . /app

# Copy built frontend from stage 1
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# Create necessary directories
RUN mkdir -p /app/workspace /app/data /app/skills /app/workspace/medical_images

# Set environment variable to indicate Docker environment
ENV DOCKER_ENV=true
ENV PYTHONUNBUFFERED=1

# Static files path for Vite build
ENV STATIC_DIR=/app/frontend/dist

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/workspace/state || exit 1

# Run the React Agent application
CMD ["uvicorn", "host_and_client_react_agent:app", "--host", "0.0.0.0", "--port", "8000"]
