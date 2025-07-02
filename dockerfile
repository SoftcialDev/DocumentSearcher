# Use an official Python 3.10 base image
FROM python:3.10-slim

# Set environment variables to reduce output and enable UTF-8
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8

# Install system dependencies
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \ 
    curl \
    build-essential && \
    apt-get purge -y build-essential && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Set JAVA_HOME for Tika
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:$PATH"

# Create app directory
WORKDIR /app

# Copy requirements into image
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip \
 && pip install --no-cache-dir torch==2.2.2+cpu torchvision==0.17.2+cpu torchaudio==2.2.2+cpu \
      -f https://download.pytorch.org/whl/cpu/torch_stable.html \
 && pip install --no-cache-dir -r requirements.txt \
 && python -m pip uninstall -y pip


# Copy project files
COPY . .

EXPOSE 80

# Default command
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:80", "--timeout", "600", "main:app"]

