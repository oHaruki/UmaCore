#!/bin/bash
# Installation script for UmaCore Bot as systemd service
# Run this script on your Raspberry Pi

set -e

echo "=========================================="
echo "UmaCore Bot - Systemd Service Installer"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo -e "${RED}Error: Do not run this script as root. Run as your user (pi) and use sudo when needed.${NC}"
   exit 1
fi

# Get current user and home directory
CURRENT_USER=$(whoami)
HOME_DIR=$(eval echo ~$CURRENT_USER)
PROJECT_DIR="$HOME_DIR/UmaCore"

echo -e "${GREEN}Installing for user: $CURRENT_USER${NC}"
echo -e "${GREEN}Project directory: $PROJECT_DIR${NC}"
echo ""

# Check if project directory exists
if [ ! -d "$PROJECT_DIR" ]; then
    echo -e "${RED}Error: Project directory not found at $PROJECT_DIR${NC}"
    echo "Please make sure you're in the UmaCore directory or clone the repository first."
    exit 1
fi

cd "$PROJECT_DIR"

# Step 1: Install system dependencies
echo -e "${YELLOW}Step 1: Installing system dependencies...${NC}"
sudo apt-get update
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    chromium \
    chromium-driver \
    xvfb \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils

echo -e "${GREEN}✓ System dependencies installed${NC}"
echo ""

# Step 2: Create Python virtual environment
echo -e "${YELLOW}Step 2: Creating Python virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3.11 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${YELLOW}Virtual environment already exists, skipping...${NC}"
fi

# Step 3: Install Python dependencies
echo -e "${YELLOW}Step 3: Installing Python dependencies...${NC}"
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo -e "${GREEN}✓ Python dependencies installed${NC}"
deactivate
echo ""

# Step 4: Check for .env file
echo -e "${YELLOW}Step 4: Checking configuration...${NC}"
if [ ! -f ".env" ]; then
    echo -e "${RED}Warning: .env file not found!${NC}"
    echo "Please create a .env file with the following variables:"
    echo "  DISCORD_TOKEN=your_token_here"
    echo "  CHANNEL_ID=your_channel_id"
    echo "  DATABASE_URL=postgresql://user:password@host:5432/database"
    echo "  LOG_LEVEL=INFO"
    echo ""
    read -p "Press Enter to continue anyway (you'll need to create .env manually)..."
else
    echo -e "${GREEN}✓ .env file found${NC}"
fi
echo ""

# Step 5: Create logs directory
echo -e "${YELLOW}Step 5: Creating logs directory...${NC}"
mkdir -p logs
chmod 755 logs
echo -e "${GREEN}✓ Logs directory created${NC}"
echo ""

# Step 6: Install systemd service
echo -e "${YELLOW}Step 6: Installing systemd service...${NC}"

# Update service file with actual paths
sed "s|/home/pi/UmaCore|$PROJECT_DIR|g" umacore-bot.service > /tmp/umacore-bot.service
sed -i "s|User=pi|User=$CURRENT_USER|g" /tmp/umacore-bot.service
sed -i "s|Group=pi|Group=$CURRENT_USER|g" /tmp/umacore-bot.service

sudo cp /tmp/umacore-bot.service /etc/systemd/system/umacore-bot.service
sudo chmod 644 /etc/systemd/system/umacore-bot.service
rm /tmp/umacore-bot.service

# Reload systemd
sudo systemctl daemon-reload
echo -e "${GREEN}✓ Systemd service installed${NC}"
echo ""

# Step 7: Enable and start service
echo -e "${YELLOW}Step 7: Enabling and starting service...${NC}"
echo ""
echo "The service is now installed. To start it, run:"
echo -e "${GREEN}  sudo systemctl enable umacore-bot${NC}"
echo -e "${GREEN}  sudo systemctl start umacore-bot${NC}"
echo ""
echo "To check status:"
echo -e "${GREEN}  sudo systemctl status umacore-bot${NC}"
echo ""
echo "To view logs:"
echo -e "${GREEN}  sudo journalctl -u umacore-bot -f${NC}"
echo ""
echo "Or check the log file:"
echo -e "${GREEN}  tail -f $PROJECT_DIR/bot.log${NC}"
echo ""

read -p "Do you want to enable and start the service now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo systemctl enable umacore-bot
    sudo systemctl start umacore-bot
    echo ""
    echo -e "${GREEN}✓ Service enabled and started!${NC}"
    echo ""
    echo "Checking status..."
    sleep 2
    sudo systemctl status umacore-bot --no-pager
fi

echo ""
echo -e "${GREEN}=========================================="
echo "Installation complete!"
echo "==========================================${NC}"

