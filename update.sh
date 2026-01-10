#!/bin/bash
# Safe update script - preserves .env and local data

echo "=== WhatsBot Safe Update ==="

cd /opt/whatsbot

# Backup .env file
if [ -f .env ]; then
    cp .env .env.backup
    echo "✓ Backed up .env to .env.backup"
fi

# Fetch latest from GitHub
echo "Fetching updates from GitHub..."
git fetch origin

# Show what will change
echo ""
echo "Changes to be applied:"
git log HEAD..origin/main --oneline 2>/dev/null || echo "No new commits"

# Pull updates (keeps untracked files like .env)
echo ""
echo "Applying updates..."
git pull origin main

# Restore .env if it was somehow deleted
if [ ! -f .env ] && [ -f .env.backup ]; then
    cp .env.backup .env
    echo "✓ Restored .env from backup"
fi

# Reinstall dependencies if requirements.txt changed
if git diff HEAD~1 --name-only 2>/dev/null | grep -q "requirements.txt"; then
    echo "Requirements changed, installing dependencies..."
    source venv/bin/activate
    pip install -r requirements.txt
fi

# Restart service
echo ""
echo "Restarting WhatsBot service..."
systemctl restart whatsbot

sleep 3
systemctl status whatsbot --no-pager

echo ""
echo "=== Update Complete ==="
