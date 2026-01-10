#!/bin/bash
# WhatsBot Installation Script
# Usage: curl -sSL https://raw.githubusercontent.com/mmdelhajj/NEWBOTFINAL/main/install.sh | bash

set -e

echo "========================================"
echo "   WhatsBot Installation Script"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash install.sh"
    exit 1
fi

# Update system
echo "[1/8] Updating system packages..."
apt-get update -qq

# Install dependencies
echo "[2/8] Installing dependencies..."
apt-get install -y -qq python3 python3-pip python3-venv mysql-server nginx git curl

# Start MySQL
echo "[3/8] Setting up MySQL..."
systemctl start mysql
systemctl enable mysql

# Create database and user
mysql -e "CREATE DATABASE IF NOT EXISTS whatsbot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -e "CREATE USER IF NOT EXISTS 'whatsbot'@'localhost' IDENTIFIED WITH mysql_native_password BY 'whatsbot123';"
mysql -e "GRANT ALL PRIVILEGES ON whatsbot.* TO 'whatsbot'@'localhost';"
mysql -e "FLUSH PRIVILEGES;"
echo "✓ Database 'whatsbot' created"

# Clone repository
echo "[4/8] Cloning WhatsBot from GitHub..."
if [ -d "/opt/whatsbot" ]; then
    echo "Directory exists, pulling latest..."
    cd /opt/whatsbot
    git pull origin main 2>/dev/null || true
else
    git clone https://github.com/mmdelhajj/NEWBOTFINAL.git /opt/whatsbot
    cd /opt/whatsbot
fi

# Create virtual environment
echo "[5/8] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install jinja2 aiofiles itsdangerous anthropic -q

# Create .env file if not exists
echo "[6/8] Creating configuration..."
if [ ! -f .env ]; then
    cp .env.example .env
    # Update database URL
    sed -i 's|DATABASE_URL=.*|DATABASE_URL=mysql+pymysql://whatsbot:whatsbot123@localhost/whatsbot|' .env
    echo "✓ Created .env file - PLEASE EDIT WITH YOUR API KEYS!"
fi

# Create systemd service
echo "[7/8] Creating systemd service..."
cat > /etc/systemd/system/whatsbot.service << 'EOF'
[Unit]
Description=WhatsApp Bot
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/whatsbot
ExecStart=/opt/whatsbot/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
Environment=TZ=Asia/Beirut

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable whatsbot

# Configure Nginx
echo "[8/8] Configuring Nginx..."
cat > /etc/nginx/sites-available/whatsbot << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_cache_bypass $http_upgrade;
    }
}
EOF

ln -sf /etc/nginx/sites-available/whatsbot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t && systemctl restart nginx

# Start WhatsBot
systemctl start whatsbot

echo ""
echo "========================================"
echo "   Installation Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Edit /opt/whatsbot/.env with your API keys:"
echo "   - WHATSAPP_ACCOUNT_ID"
echo "   - WHATSAPP_SEND_SECRET"
echo "   - ANTHROPIC_API_KEY"
echo "   - BRAINS_API_BASE"
echo ""
echo "2. Restart the service:"
echo "   systemctl restart whatsbot"
echo ""
echo "3. Access dashboard:"
echo "   http://YOUR-SERVER-IP/dashboard"
echo "   Login: admin / admin123"
echo ""
echo "========================================"
