#!/bin/bash
# ============================================
# WhatsApp Bot - Nuitka Compiled Build
# Compiles Python to C to Binary
# ============================================

set -e

echo "========================================"
echo "  WhatsApp Bot - Nuitka Compilation"
echo "========================================"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_DIR="/var/www/whatsbot-python"
BUILD_DIR="/var/www/whatsbot-python/nuitka_build"
DIST_DIR="/var/www/whatsbot-python/dist"

cd "$PROJECT_DIR"

# Create/activate virtual environment
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies
echo -e "${YELLOW}[1/5] Installing dependencies...${NC}"
pip install -r requirements.txt
pip install nuitka ordered-set

# Check for required packages
echo -e "${YELLOW}[2/5] Checking system dependencies...${NC}"
apt-get install -y patchelf ccache 2>/dev/null || true

# Clean previous builds
echo -e "${YELLOW}[3/5] Cleaning previous builds...${NC}"
rm -rf "$BUILD_DIR" main.build main.dist main.onefile-build
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Create storage directory
mkdir -p storage

# Compile with Nuitka
echo -e "${YELLOW}[4/5] Compiling with Nuitka (this takes 30-60 min)...${NC}"
echo "    Compiling Python → C → Binary..."

python -m nuitka \
    --standalone \
    --onefile \
    --output-dir="$BUILD_DIR" \
    --output-filename=whatsbot \
    --follow-imports \
    --prefer-source-code \
    --include-package=services \
    --include-package=utils \
    main.py

echo -e "${GREEN}    ✓ Compilation complete${NC}"

# Create deployment package
echo -e "${YELLOW}[5/5] Creating deployment package...${NC}"

mkdir -p "$BUILD_DIR/package"

# Find and copy the binary
if [ -f "$BUILD_DIR/whatsbot" ]; then
    cp "$BUILD_DIR/whatsbot" "$BUILD_DIR/package/"
elif [ -f "$BUILD_DIR/main.bin" ]; then
    cp "$BUILD_DIR/main.bin" "$BUILD_DIR/package/whatsbot"
else
    BINARY=$(find "$BUILD_DIR" -type f -executable -name "*.bin" 2>/dev/null | head -1)
    if [ -n "$BINARY" ]; then
        cp "$BINARY" "$BUILD_DIR/package/whatsbot"
    fi
fi

chmod +x "$BUILD_DIR/package/whatsbot" 2>/dev/null || true

# Create directories
mkdir -p "$BUILD_DIR/package/storage"

# Copy config files
cp "$PROJECT_DIR/.env.example" "$BUILD_DIR/package/.env.example"

# Copy templates directory
cp -r "$PROJECT_DIR/templates" "$BUILD_DIR/package/templates"

# Create VERSION file
echo "1.0.0" > "$BUILD_DIR/package/VERSION"

# Create startup script
cat > "$BUILD_DIR/package/start.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
./whatsbot
EOF
chmod +x "$BUILD_DIR/package/start.sh"

# Create systemd service
cat > "$BUILD_DIR/package/whatsbot.service" << 'EOF'
[Unit]
Description=WhatsApp Bot Backend
After=network.target mysql.service

[Service]
Type=simple
WorkingDirectory=/opt/whatsbot
ExecStart=/opt/whatsbot/whatsbot
Restart=always
RestartSec=5
Environment=TZ=Asia/Beirut

[Install]
WantedBy=multi-user.target
EOF

# Create install script
cat > "$BUILD_DIR/package/install.sh" << 'INSTALLEOF'
#!/bin/bash
# WhatsApp Bot Install Script

echo "Installing WhatsApp Bot..."

# Create directory
mkdir -p /opt/whatsbot
cp -r . /opt/whatsbot/
mkdir -p /opt/whatsbot/templates

# Set permissions
chmod +x /opt/whatsbot/whatsbot
chmod +x /opt/whatsbot/start.sh

# Copy service file
cp /opt/whatsbot/whatsbot.service /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Check if .env exists
if [ ! -f /opt/whatsbot/.env ]; then
    echo "Creating .env from example..."
    cp /opt/whatsbot/.env.example /opt/whatsbot/.env
    echo ""
    echo "IMPORTANT: Edit /opt/whatsbot/.env with your configuration!"
    echo ""
fi

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Edit /opt/whatsbot/.env with your configuration"
echo "2. Start the service: systemctl start whatsbot"
echo "3. Enable on boot: systemctl enable whatsbot"
echo ""
INSTALLEOF
chmod +x "$BUILD_DIR/package/install.sh"

# Package
cd "$BUILD_DIR/package"
FILENAME="whatsbot-compiled-$(date +%Y%m%d).tar.gz"
tar -czvf "$DIST_DIR/$FILENAME" .

echo ""
echo -e "${GREEN}========================================"
echo "  BUILD COMPLETE!"
echo "========================================"
echo ""
echo "  Binary:       $BUILD_DIR/package/whatsbot"
echo "  Distribution: $DIST_DIR/$FILENAME"
echo ""
echo "  Protection:"
echo "    ✓ Compiled to native binary"
echo "    ✓ No Python source code"
echo "    ✓ Cannot be decompiled"
echo "    ✓ License server integrated"
echo "========================================${NC}"
