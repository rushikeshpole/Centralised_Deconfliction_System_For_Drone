#!/bin/bash

# UAV Deconfliction System Run Script
echo "Starting UAV Deconfliction System..."

# Activate virtual environment
source venv/bin/activate

# Check if database exists, create if not
if [ ! -f drones.db ]; then
    echo "Initializing database..."
    python3 -c "from database import init_db; init_db()"
fi

# Check if ArduPilot SITL is running
echo "Checking for ArduPilot SITL instances..."
if ! pgrep -f "sim_vehicle.py" > /dev/null; then
    echo "WARNING: No ArduPilot SITL instances found!"
    echo "Please start drones first using:"
    echo "  cd ~/ardupilot/Tools/autotest"
    echo "  ./sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON --console -I1"
    echo "  ./sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON --console -I2"
    echo "  ./sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON --console -I3"
    echo "  ./sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON --console -I4"
    echo ""
    echo "Continue anyway? (y/n)"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo "Exiting..."
        exit 1
    fi
fi

# Check if Gazebo is running
if ! pgrep -f "gz sim" > /dev/null; then
    echo "WARNING: Gazebo simulator not found!"
    echo "Please start Gazebo with drones first using:"
    echo "  gz sim -v4 -r multi_iris.sdf"
    echo ""
    echo "Continue anyway? (y/n)"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo "Exiting..."
        exit 1
    fi
fi


# Start the main system
echo "Starting deconfliction system..."
python3 main_integration.py

# If main script exits, stop recording
echo "System stopped."
