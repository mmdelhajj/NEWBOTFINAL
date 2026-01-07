#!/bin/bash
# Nuitka build script for WhatsBot

cd /opt/whatsbot

echo "=== Building WhatsBot with Nuitka ==="
echo "This may take 10-30 minutes..."

# Stop the service first
fuser -k 8000/tcp 2>/dev/null || true
sleep 2

# Activate virtual environment
source venv/bin/activate

# Install nuitka in venv if not present
pip install nuitka ordered-set zstandard --quiet

# Clean previous builds
rm -rf main.build main.dist main.onefile-build 2>/dev/null

# Build with Nuitka
python -m nuitka \
    --standalone \
    --onefile \
    --output-filename=whatsbot-compiled \
    --include-package=services \
    --include-package=utils \
    --include-data-dir=templates=templates \
    --include-data-dir=data=data \
    --follow-imports \
    --assume-yes-for-downloads \
    main.py

if [ -f "whatsbot-compiled" ]; then
    echo ""
    echo "=== Build successful! ==="
    chmod +x whatsbot-compiled
    ls -lh whatsbot-compiled
    echo ""
    echo "To run: ./whatsbot-compiled"
else
    echo "Build failed!"
    exit 1
fi
