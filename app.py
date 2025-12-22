"""
Flask web server for UAV Deconfliction System
"""
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import json
from datetime import datetime, timedelta
import threading
import time

# Import our modules
from database import init_db, get_all_drones_status, create_mission, get_active_missions
from deconfliction_engine import DeconflictionEngine
from drone_controller import EnhancedDroneController
from mission_executor import MissionExecutor

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Global instances
drone_controller = None
deconfliction_engine = None
mission_executor = None

# Global state
system_running = False
update_thread = None

def init_system():
    """Initialize the deconfliction system"""
    global drone_controller, deconfliction_engine, mission_executor
    
    # Initialize database
    init_db()
    
    # Create controller instances
    drone_controller = EnhancedDroneController(drone_count=4)
    deconfliction_engine = DeconflictionEngine(safety_buffer=5.0)
    mission_executor = MissionExecutor(drone_controller)
    
    # Start trajectory recording
    drone_controller.start_recording()
    
    print("="*60)
    print("UAV Deconfliction System Initialized")
    print(f"Connected to {len(drone_controller.drones)} drones")
    print("="*60)

def start_update_thread():
    """Start background thread for real-time updates"""
    global update_thread, system_running
    
    # In app.py, update the update_loop function:

    def update_loop():
        """Background update thread for real-time updates"""
        while system_running:
            try:
                # Get current drone statuses
                statuses = {}
                for drone_id in range(1, 5):  # Assuming 4 drones
                    status = drone_controller.get_drone_status(drone_id)
                    if status:
                        # Convert to JSON-serializable format
                        statuses[drone_id] = {
                            'id': drone_id,
                            'armed': status.get('armed', False),
                            'mode': status.get('mode', 'UNKNOWN'),
                            'position': status.get('position', {}),
                            'battery': status.get('battery', 0.0),
                            'status': 'active' if status.get('armed') else 'idle'
                        }
            
                # Emit via WebSocket
                socketio.emit('drone_update', {
                    'timestamp': datetime.now().isoformat(),
                    'drones': statuses,
                    'conflicts': []  # Will be populated by conflict detection
                })
            
                # Check for real-time conflicts
                check_realtime_conflicts(statuses)
            
                time.sleep(1.0)  # Update every second
            
            except Exception as e:
                print(f"Error in update loop: {e}")
                time.sleep(5.0)
    
    system_running = True
    update_thread = threading.Thread(target=update_loop, daemon=True)
    update_thread.start()

def check_realtime_conflicts(statuses):
    """Check for conflicts in real-time"""
    try:
        drones = list(statuses.keys())
        
        for i in range(len(drones)):
            for j in range(i + 1, len(drones)):
                drone1_id = drones[i]
                drone2_id = drones[j]
                
                if (drone1_id in statuses and drone2_id in statuses and
                    statuses[drone1_id] and statuses[drone2_id] and
                    statuses[drone1_id].get('position') and statuses[drone2_id].get('position')):
                    
                    pos1 = statuses[drone1_id]['position']
                    pos2 = statuses[drone2_id]['position']
                    
                    if 'x' in pos1 and 'x' in pos2:
                        distance = ((pos1['x'] - pos2['x'])**2 + 
                                   (pos1['y'] - pos2['y'])**2 + 
                                   (pos1['z'] - pos2['z'])**2)**0.5
                        
                        if distance < 5.0:  # Safety buffer
                            socketio.emit('conflict_alert', {
                                'type': 'realtime_conflict',
                                'drone1': drone1_id,
                                'drone2': drone2_id,
                                'distance': distance,
                                'position': pos1,
                                'timestamp': datetime.now().isoformat()
                            })
    except Exception as e:
        print(f"Error checking real-time conflicts: {e}")

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/visualization')
def visualization():
    """Visualization page"""
    return render_template('visualization.html')

@app.route('/history/<int:drone_id>')
def history(drone_id):
    """Drone history page"""
    return render_template('history.html', drone_id=drone_id)

@app.route('/api/drones', methods=['GET'])
def api_get_drones():
    """Get status of all drones"""
    try:
        drones = get_all_drones_status()
        return jsonify({
            'success': True,
            'drones': drones,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/missions', methods=['GET'])
def api_get_missions():
    """Get all missions"""
    try:
        missions = get_active_missions()
        return jsonify({
            'success': True,
            'missions': missions
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule', methods=['POST'])
def api_schedule_mission():
    """Schedule a new mission with deconfliction check"""
    try:
        data = request.json
        
        required_fields = ['drone_id', 'waypoints', 'start_time', 'end_time']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        drone_id = int(data['drone_id'])
        waypoints = data['waypoints']
        start_time = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
        end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
        
        # Validate inputs
        if drone_id not in [1, 2, 3, 4]:
            return jsonify({
                'success': False,
                'error': 'Invalid drone ID. Must be 1-4'
            }), 400
        
        if start_time >= end_time:
            return jsonify({
                'success': False,
                'error': 'Start time must be before end time'
            }), 400
        
        # Perform deconfliction check
        conflict_result = deconfliction_engine.check_mission_conflict(
            drone_id, waypoints, start_time, end_time
        )
        
        if not conflict_result['safe']:
            return jsonify({
                'success': False,
                'conflict': True,
                'message': 'Mission conflicts with existing trajectories',
                'details': conflict_result
            }), 409  # Conflict status code
        
        # Schedule the mission
        mission_id = mission_executor.schedule_mission(
            drone_id, waypoints, start_time, end_time
        )
        
        if mission_id:
            return jsonify({
                'success': True,
                'mission_id': mission_id,
                'message': 'Mission scheduled successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to schedule mission'
            }), 500
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/control/<int:drone_id>', methods=['POST'])
def api_control_drone(drone_id):
    """Send direct control commands to a drone"""
    try:
        data = request.json
        command = data.get('command')
        
        if not command:
            return jsonify({'success': False, 'error': 'No command specified'}), 400
        
        result = False
        message = ''
        
        if command == 'arm':
            result = drone_controller.arm_drone(drone_id)
            message = 'Armed' if result else 'Failed to arm'
        elif command == 'takeoff':
            altitude = float(data.get('altitude', 10.0))
            result = drone_controller.takeoff(drone_id, altitude)
            message = f'Taking off to {altitude}m' if result else 'Failed to takeoff'
        elif command == 'land':
            result = drone_controller.land(drone_id)
            message = 'Landing' if result else 'Failed to land'
        elif command == 'rtl':
            result = drone_controller.return_to_launch(drone_id)
            message = 'Returning to launch' if result else 'Failed to RTL'
        elif command == 'disarm':
            result = drone_controller.disarm_drone(drone_id)
            message = 'Disarmed' if result else 'Failed to disarm'
        elif command == 'goto':
            x = float(data.get('x', 0))
            y = float(data.get('y', 0))
            z = float(data.get('z', 10))
            result = drone_controller.goto_position(drone_id, x, y, z)
            message = f'Going to ({x}, {y}, {z})' if result else 'Failed to go to position'
        else:
            return jsonify({'success': False, 'error': f'Unknown command: {command}'}), 400
        
        return jsonify({
            'success': result,
            'message': message,
            'drone_id': drone_id,
            'command': command
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/trajectory/<int:drone_id>', methods=['GET'])
def api_get_trajectory(drone_id):
    """Get trajectory data for a drone"""
    try:
        # In a real implementation, this would query the database
        # For now, return simulated data
        from database import get_drone_trajectory
        
        hours_back = int(request.args.get('hours', 1))
        start_time = datetime.now() - timedelta(hours=hours_back)
        
        trajectory = get_drone_trajectory(drone_id, start_time, datetime.now())
        
        return jsonify({
            'success': True,
            'drone_id': drone_id,
            'trajectory': trajectory
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/emergency', methods=['POST'])
def api_emergency_stop():
    """Emergency stop all drones"""
    try:
        drone_controller.emergency_stop_all()
        return jsonify({
            'success': True,
            'message': 'Emergency stop initiated'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print(f'Client connected: {request.sid}')
    emit('connected', {
        'message': 'Connected to UAV Deconfliction System',
        'timestamp': datetime.now().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    print(f'Client disconnected: {request.sid}')

@socketio.on('request_update')
def handle_update_request():
    """Handle update request from client"""
    statuses = drone_controller.get_all_status()
    emit('drone_update', {
        'timestamp': datetime.now().isoformat(),
        'drones': statuses
    })

if __name__ == '__main__':
    # Initialize system
    init_system()
    
    # Start update thread
    start_update_thread()
    
    # Run Flask app
    print(f"Starting web server on http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
