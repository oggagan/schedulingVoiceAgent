#!/bin/bash
# EC2 Setup Script for Voice Scheduling Agent
# Run this script on a fresh Ubuntu 22.04 EC2 instance

set -e

echo "=========================================="
echo "Voice Scheduling Agent - EC2 Setup Script"
echo "=========================================="

# Variables
APP_DIR="/home/ubuntu/voice-scheduling-agent"
VENV_DIR="$APP_DIR/venv"
LOG_DIR="/var/log/voice-agent"

# Update system
echo "[1/8] Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install required packages
echo "[2/8] Installing required packages..."
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    nginx \
    certbot \
    python3-certbot-nginx \
    git \
    curl \
    build-essential

# Create application directory
echo "[3/8] Setting up application directory..."
sudo mkdir -p $APP_DIR
sudo mkdir -p $LOG_DIR
sudo chown -R ubuntu:ubuntu $APP_DIR
sudo chown -R ubuntu:ubuntu $LOG_DIR

# Clone or copy application (assuming you've uploaded the code)
echo "[4/8] Setting up application code..."
cd $APP_DIR

# Create Python virtual environment
echo "[5/8] Creating Python virtual environment..."
python3.11 -m venv $VENV_DIR
source $VENV_DIR/bin/activate

# Install Python dependencies
echo "[6/8] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create directories
mkdir -p data logs

# Setup systemd service
echo "[7/8] Setting up systemd service..."
sudo cp deploy/voice-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable voice-agent

# Setup Nginx (optional - for production with HTTPS)
echo "[8/8] Setting up Nginx..."
sudo cp deploy/nginx.conf /etc/nginx/nginx.conf

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Create your .env file:"
echo "   cp env.example .env"
echo "   nano .env"
echo ""
echo "2. Add your API keys to .env:"
echo "   - OPENAI_API_KEY"
echo "   - GOOGLE_CLIENT_ID"
echo "   - GOOGLE_CLIENT_SECRET"
echo "   - GOOGLE_REDIRECT_URI (update for your domain)"
echo ""
echo "3. Update Google Cloud Console:"
echo "   - Add your domain to authorized redirect URIs"
echo ""
echo "4. Setup SSL with Let's Encrypt (for HTTPS):"
echo "   sudo certbot --nginx -d yourdomain.com"
echo ""
echo "5. Start the service:"
echo "   sudo systemctl start voice-agent"
echo ""
echo "6. Check status:"
echo "   sudo systemctl status voice-agent"
echo "   tail -f logs/app.log"
echo ""
echo "7. (Optional) Start Nginx for HTTPS:"
echo "   sudo systemctl restart nginx"
echo ""
