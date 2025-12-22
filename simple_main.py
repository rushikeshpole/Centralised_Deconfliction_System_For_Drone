#!/usr/bin/env python3
"""
Simplified main launcher for UAV Deconfliction System
"""
import sys
import time
import signal
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import threading
from datetime import datetime

# Initialize Flask app
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global state
drones = {}
system_running = True

# Initialize database
import sqlite3
conn = sqlite3.connect('drones.db')
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS drones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drone_id INTEGER UNIQUE,
    status TEXT DEFAULT 'idle',
    position TEXT DEFAULT '{}',
    last_update TIMESTAMP
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS missions (
    mission_id INTEGER PRIMARY KEY AUTOINCREMENT,
    drone_id INTEGER,
    waypoints TEXT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    status TEXT DEFAULT 'scheduled'
)
''')
conn.commit()
conn.close()

class SimpleDroneSimulator:
    """Simple drone simulator for testing"""
    def __init__(self):
        self.drones = {
            1: {'id': 1, 'status': 'idle', 'position': [0, 0, 0], 'armed': False, 'mode': 'GUIDED'},
            2: {'id': 2, 'status': 'idle', 'position': [5, 0, 0], 'armed': False, 'mode': 'GUIDED'},
            3: {'id': 3, 'status': 'idle', 'position': [0, 5, 0], 'armed': False, 'mode': 'GUIDED'},
            4: {'id': 4, 'status': 'idle', 'position': [5, 5, 0], 'armed': False, 'mode': 'GUIDED'},
        }
        
    def get_all_status(self):
        """Get status of all drones"""
        return self.drones
    
    def update_position(self, drone_id, position):
        """Update drone position"""
        if drone_id in self.drones:
            self.drones[drone_id]['position'] = position
            
    def check_conflict(self, drone1_id, drone2_id):
        """Check for conflict between two drones"""
        if drone1_id in self.drones and drone2_id in self.drones:
            pos1 = self.drones[drone1_id]['position']
            pos2 = self.drones[drone2_id]['position']
            
            distance = ((pos1[0]-pos2[0])**2 + (pos1[1]-pos2[1])**2 + (pos1[2]-pos2[2])**2)**0.5
            return distance < 5.0  # Safety buffer
            
        return False

# Initialize simulator
simulator = SimpleDroneSimulator()

def update_loop():
    """Background update loop"""
    while system_running:
        try:
            # Update drone positions (simulate movement)
            for drone_id in [1, 2, 3, 4]:
                drone = simulator.drones[drone_id]
                if drone['status'] == 'moving':
                    # Simulate movement
                    drone['position'][0] += 0.1
                    drone['position'][1] += 0.1
            
            # Check for conflicts
            for i in [1, 2, 3, 4]:
                for j in [1, 2, 3, 4]:
                    if i < j and simulator.check_conflict(i, j):
                        socketio.emit('conflict_alert', {
                            'drone1': i,
                            'drone2': j,
                            'timestamp': datetime.now().isoformat()
                        })
            
            # Emit update
            socketio.emit('drone_update', {
                'timestamp': datetime.now().isoformat(),
                'drones': simulator.drones
            })
            
            time.sleep(1.0)
            
        except Exception as e:
            print(f"Error in update loop: {e}")
            time.sleep(5.0)

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/drones', methods=['GET'])
def api_get_drones():
    return jsonify({
        'success': True,
        'drones': simulator.drones,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/schedule', methods=['POST'])
def api_schedule_mission():
    try:
        data = request.json
        
        # Simple conflict check
        waypoints = data['waypoints']
        drone_id = data['drone_id']
        
        # Check if waypoints conflict with other drones' positions
        conflicts = []
        for other_id, other_drone in simulator.drones.items():
            if other_id != drone_id:
                for waypoint in waypoints:
                    distance = ((waypoint[0]-other_drone['position'][0])**2 +
                               (waypoint[1]-other_drone['position'][1])**2 +
                               (waypoint[2]-other_drone['position'][2])**2)**0.5
                    if distance < 5.0:
                        conflicts.append({
                            'drone': other_id,
                            'distance': distance,
                            'waypoint': waypoint
                        })
        
        if conflicts:
            return jsonify({
                'success': False,
                'conflict': True,
                'message': 'Mission conflicts with existing drone positions',
                'conflicts': conflicts,
                'suggestions': [
                    {'type': 'altitude_adjustment', 'description': 'Increase altitude by 5 meters'},
                    {'type': 'time_shift', 'description': 'Delay mission by 30 seconds'}
                ]
            })
        
        # Simulate mission acceptance
        simulator.drones[drone_id]['status'] = 'scheduled'
        
        return jsonify({
            'success': True,
            'message': 'Mission scheduled successfully',
            'mission_id': 100 + drone_id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/control/<int:drone_id>', methods=['POST'])
def api_control_drone(drone_id):
    try:
        data = request.json
        command = data.get('command')
        
        if drone_id in simulator.drones:
            if command == 'arm':
                simulator.drones[drone_id]['armed'] = True
                simulator.drones[drone_id]['status'] = 'armed'
            elif command == 'takeoff':
                simulator.drones[drone_id]['position'][2] = data.get('altitude', 10)
                simulator.drones[drone_id]['status'] = 'flying'
            elif command == 'land':
                simulator.drones[drone_id]['position'][2] = 0
                simulator.drones[drone_id]['status'] = 'landed'
            elif command == 'goto':
                x = data.get('x', 0)
                y = data.get('y', 0)
                z = data.get('z', 10)
                simulator.drones[drone_id]['position'] = [x, y, z]
                simulator.drones[drone_id]['status'] = 'moving'
            
            return jsonify({
                'success': True,
                'message': f'Command {command} executed for drone {drone_id}'
            })
        
        return jsonify({'success': False, 'error': 'Drone not found'}), 404
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    socketio.emit('connected', {'message': 'Connected to UAV System'})

@socketio.on('request_update')
def handle_update_request():
    socketio.emit('drone_update', {
        'timestamp': datetime.now().isoformat(),
        'drones': simulator.drones
    })

def signal_handler(sig, frame):
    global system_running
    print("\nShutting down system...")
    system_running = False
    time.sleep(1)
    sys.exit(0)

if __name__ == '__main__':
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    
    print("="*60)
    print("UAV Deconfliction System (Simplified Version)")
    print("="*60)
    print("Access: http://localhost:5000")
    print("="*60)
    
    # Start update thread
    update_thread = threading.Thread(target=update_loop, daemon=True)
    update_thread.start()
    
    # Run Flask app
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
