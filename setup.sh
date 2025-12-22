#!/bin/bash

# UAV Deconfliction System Setup Script
echo "Setting up UAV Deconfliction System..."

# Update and install system dependencies
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv sqlite3

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install flask flask-socketio flask-cors
pip install plotly pandas numpy scipy
pip install pymavlink dronekit
pip install pyyaml
pip install eventlet  # For WebSocket support

# Create necessary directories
mkdir -p templates static logs

# Initialize database
python3 -c "
from database import init_db
init_db()
print('Database initialized successfully')
"

# Download Leaflet for frontend (if needed)
cd static
wget https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
wget https://unpkg.com/leaflet@1.9.4/dist/leaflet.css
cd ..

echo "=========================================="
echo "Setup completed successfully!"
echo "Next steps:"
echo "1. Ensure ArduPilot SITL is running"
echo "2. Start Gazebo with drones"
echo "3. Run: ./run.sh"
echo "=========================================="
