"""
Flask web server for UAV Deconfliction System - FIXED VERSION
"""
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import json
from datetime import datetime, timedelta
import threading
import time
import logging
import random
import math
from collections import deque

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor
_background_executor = ThreadPoolExecutor(max_workers=4)

# Import our modules
try:
    from database import init_db, get_all_drones_status, create_mission, get_active_missions, get_drone_trajectory
    from deconfliction_engine import DeconflictionEngine
    from drone_controller import EnhancedDroneController
    from mission_executor import MissionExecutor
except ImportError:
    # Create dummy implementations for missing modules
    print("Warning: Some modules not found. Using dummy implementations.")
    
    class DummyDatabase:
        def init_db(self): 
            print("Database initialized")
            return True
        def get_all_drones_status(self): 
            return []
        def create_mission(self, *args): 
            return 1
        def get_active_missions(self): 
            return []
        def get_drone_trajectory(self, drone_id, start, end): 
            return []
    
    class DeconflictionEngine:
        def __init__(self, safety_buffer=2.0):
            self.safety_buffer = safety_buffer
            self.missions = []
        
        def check_mission_conflict(self, drone_id, waypoints, start_time, end_time):
            return {'safe': True, 'conflicts': []}
    
    class EnhancedDroneController:
        def __init__(self, drone_count=4):
            self.drone_count = drone_count
            self.drones = {}
            self.trajectories = {i+1: deque(maxlen=100) for i in range(drone_count)}
            self.recording = False
            
            # Initialize drones with realistic positions
            for i in range(1, drone_count + 1):
                self.drones[i] = {
                    'id': i,
                    'armed': False,
                    'mode': 'GUIDED',
                    'position': {
                        'lat': -35.363217 + ((i-2) * 0.0005),
                        'lon': 149.165252 + ((i-2) * 0.0005),
                        'x': (i-2) * 10,  # -10, 0, 10, 20
                        'y': (i-2) * 10,
                        'z': 0
                    },
                    'battery': 100.0,
                    'velocity': {'x': 0, 'y': 0, 'z': 0},
                    'status': 'idle'
                }
        
        def start_recording(self):
            self.recording = True
            print("Started trajectory recording")
            return True
        
        def get_drone_status(self, drone_id):
            if drone_id in self.drones:
                drone = self.drones[drone_id]
                
                # Simulate movement if armed
                if drone['armed']:
                    # Add small random movement
                    drone['position']['x'] += random.uniform(-0.5, 0.5)
                    drone['position']['y'] += random.uniform(-0.5, 0.5)
                    drone['position']['z'] += random.uniform(-0.1, 0.1)
                    
                    # Update lat/lon based on x,y
                    drone['position']['lat'] = -35.363217 + (drone['position']['y'] / 111000)
                    drone['position']['lon'] = 149.165252 + (drone['position']['x'] / (111000 * math.cos(math.radians(-35.363217))))
                    
                    # Record trajectory
                    self.trajectories[drone_id].append({
                        'x': drone['position']['x'],
                        'y': drone['position']['y'],
                        'z': drone['position']['z'],
                        'lat': drone['position']['lat'],
                        'lon': drone['position']['lon'],
                        'timestamp': datetime.now().isoformat()
                    })
                
                return drone
            return None
        
        def get_all_status(self):
            """Get status of all drones - FIXED"""
            statuses = {}
            for drone_id in range(1, self.drone_count + 1):
                status = self.get_drone_status(drone_id)
                if status:
                    statuses[drone_id] = {
                        'id': drone_id,
                        'armed': status.get('armed', False),
                        'mode': status.get('mode', 'UNKNOWN'),
                        'position': status.get('position', {}),
                        'battery': status.get('battery', 0.0),
                        'velocity': status.get('velocity', {}),
                        'status': 'active' if status.get('armed') else 'idle',
                        'trajectory': list(self.trajectories.get(drone_id, []))[-20:]  # Last 20 points
                    }
            return statuses
        
        def get_trajectory(self, drone_id, limit=20):
            """Get trajectory for a drone"""
            if drone_id in self.trajectories:
                return list(self.trajectories[drone_id])[-limit:]
            return []
        
        def arm_drone(self, drone_id): 
            if drone_id in self.drones:
                self.drones[drone_id]['armed'] = True
                self.drones[drone_id]['status'] = 'active'
                return True
            return False
        
        def disarm_drone(self, drone_id): 
            if drone_id in self.drones:
                self.drones[drone_id]['armed'] = False
                self.drones[drone_id]['status'] = 'idle'
                return True
            return False
        
        def takeoff(self, drone_id, altitude): 
            if drone_id in self.drones:
                self.drones[drone_id]['position']['z'] = altitude
                self.drones[drone_id]['armed'] = True
                self.drones[drone_id]['status'] = 'active'
                return True
            return False
        
        def land(self, drone_id): 
            if drone_id in self.drones:
                self.drones[drone_id]['position']['z'] = 0
                return True
            return False
        
        def return_to_launch(self, drone_id): 
            if drone_id in self.drones:
                # Return to original position
                self.drones[drone_id]['position']['x'] = (drone_id-2) * 10
                self.drones[drone_id]['position']['y'] = (drone_id-2) * 10
                self.drones[drone_id]['position']['z'] = 0
                return True
            return False
        
        def goto_position(self, drone_id, x, y, z): 
            if drone_id in self.drones:
                self.drones[drone_id]['position']['x'] = x
                self.drones[drone_id]['position']['y'] = y
                self.drones[drone_id]['position']['z'] = z
                return True
            return False
        
        def emergency_stop_all(self): 
            for drone_id in self.drones:
                self.drones[drone_id]['armed'] = False
                self.drones[drone_id]['status'] = 'idle'
            return True
    
    class MissionExecutor:
        def __init__(self, drone_controller):
            self.drone_controller = drone_controller
            self.missions = {}
            self.next_mission_id = 1
        
        def schedule_mission(self, drone_id, waypoints, start_time, end_time):
            mission_id = self.next_mission_id
            self.next_mission_id += 1
            self.missions[mission_id] = {
                'drone_id': drone_id,
                'waypoints': waypoints,
                'start_time': start_time,
                'end_time': end_time,
                'status': 'scheduled'
            }
            return mission_id
    
    # Create dummy instances
    init_db = DummyDatabase().init_db
    get_all_drones_status = DummyDatabase().get_all_drones_status
    create_mission = DummyDatabase().create_mission
    get_active_missions = DummyDatabase().get_active_missions
    get_drone_trajectory = DummyDatabase().get_drone_trajectory

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='threading',  # Changed from eventlet to threading
                    ping_timeout=60,
                    ping_interval=25,
                    logger=False,  # Set to False to reduce logs
                    engineio_logger=False)

# Global instances
drone_controller = None
deconfliction_engine = None
mission_executor = None

# Global state
system_running = False
update_thread = None
connected_clients = set()

def _schedule_worker(drone_id, waypoints, start_time, end_time, request_meta):
    """
    Background worker that performs the expensive conflict checks and mission scheduling.
    It should log results and (optionally) notify the client via SocketIO when done.
    """
    try:
        # Run deconfliction check (may call DB)
        conflict_result = deconfliction_engine.check_mission_conflict(
            drone_id, waypoints, start_time, end_time
        )

        if not conflict_result.get('safe', True):
            # Log and notify via SocketIO (or update DB)
            logger.info(f"Scheduling denied for drone {drone_id} due to conflict: {conflict_result}")
            # Optionally emit SocketIO event to originating client (use request_meta to find client id)
            socketio.emit('schedule_result', {
                'success': False,
                'conflict': True,
                'details': conflict_result,
                'request_id': request_meta.get('request_id')
            })
            return

        # Schedule mission (this may insert many rows; mission_executor should use batched DB inserts)
        mission_id = mission_executor.schedule_mission(
            drone_id, waypoints, start_time, end_time
        )

        socketio.emit('schedule_result', {
            'success': True,
            'mission_id': mission_id,
            'message': 'Mission scheduled successfully',
            'request_id': request_meta.get('request_id')
        })

    except Exception as e:
        logger.exception("Error scheduling mission in background")
        socketio.emit('schedule_result', {
            'success': False,
            'error': str(e),
            'request_id': request_meta.get('request_id')
        })


def init_system():
    """Initialize the deconfliction system"""
    global drone_controller, deconfliction_engine, mission_executor
    
    # Initialize database
    init_db()
    
    # Create controller instances
    drone_controller = EnhancedDroneController(drone_count=4)  # 4 drones
    deconfliction_engine = DeconflictionEngine(safety_buffer=2.0)
    mission_executor = MissionExecutor(drone_controller)
    
    # Start trajectory recording
    drone_controller.start_recording()
    
    print("="*60)
    print("UAV Deconfliction System Initialized")
    print(f"Connected to {drone_controller.drone_count} drones")
    print(f"Center coordinates: -35.363217, 149.165252")
    print("="*60)

def start_update_thread():
    """Start background thread for real-time updates"""
    global update_thread, system_running
    
    def update_loop():
        """Background update thread for real-time updates"""
        update_counter = 0
        
        while system_running:
            try:
                # Get current drone statuses
                statuses = drone_controller.get_all_status()
                
                # Check for conflicts
                conflicts = check_realtime_conflicts(statuses)
                
                # Prepare update data
                update_data = {
                    'timestamp': datetime.now().isoformat(),
                    'drones': statuses,
                    'conflicts': conflicts,
                    'update_id': update_counter
                }
                
                # Emit to all connected clients using socketio.emit() in app context
                # This is the FIX: Don't use broadcast=True, socketio.emit() already broadcasts
                with app.app_context():
                    socketio.emit('drone_update', update_data)
                
                update_counter += 1
                
                # Sleep for update interval (faster updates for better visualization)
                time.sleep(0.5)  # Update every 500ms
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(1.0)
    
    system_running = True
    update_thread = threading.Thread(target=update_loop, daemon=True)
    update_thread.start()
    print("✅ Update thread started - Sending updates every 500ms")

def check_realtime_conflicts(statuses):
    """Check for conflicts in real-time"""
    conflicts = []
    
    try:
        drones = list(statuses.keys())
        
        for i in range(len(drones)):
            for j in range(i + 1, len(drones)):
                drone1_id = drones[i]
                drone2_id = drones[j]
                
                drone1 = statuses.get(drone1_id)
                drone2 = statuses.get(drone2_id)
                
                if drone1 and drone2 and drone1.get('position') and drone2.get('position'):
                    pos1 = drone1['position']
                    pos2 = drone2['position']
                    
                    # Check if we have x,y,z coordinates
                    if all(k in pos1 for k in ['x', 'y', 'z']) and all(k in pos2 for k in ['x', 'y', 'z']):
                        # Calculate 3D Euclidean distance
                        dx = pos1['x'] - pos2['x']
                        dy = pos1['y'] - pos2['y']
                        dz = pos1['z'] - pos2['z']
                        distance = math.sqrt(dx**2 + dy**2 + dz**2)
                        
                        if distance < 2.0:  # Safety buffer
                            conflict = {
                                'drone1': drone1_id,
                                'drone2': drone2_id,
                                'distance': distance,
                                'position': {
                                    'x': (pos1['x'] + pos2['x']) / 2,
                                    'y': (pos1['y'] + pos2['y']) / 2,
                                    'z': (pos1['z'] + pos2['z']) / 2,
                                    'lat': (pos1.get('lat', -35.363217) + pos2.get('lat', -35.363217)) / 2,
                                    'lon': (pos1.get('lon', 149.165252) + pos2.get('lon', 149.165252)) / 2
                                },
                                'timestamp': datetime.now().isoformat(),
                                'severity': 'high' if distance < 1.0 else 'medium' if distance < 1.5 else 'low'
                            }
                            conflicts.append(conflict)
                            
                            # Also emit individual conflict alert
                            with app.app_context():
                                socketio.emit('conflict_alert', conflict)
    
    except Exception as e:
        logger.error(f"Error checking real-time conflicts: {e}")
    
    return conflicts

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

import statistics
from collections import defaultdict

# Add these new API endpoints after the existing ones

@app.route('/api/history/conflicts', methods=['GET'])
def api_get_conflict_history():
    """Get conflict history for all drones"""
    try:
        # This would query your database for conflicts
        # For now, return dummy data
        conflicts = []
        
        # Get current positions to simulate recent conflicts
        statuses = drone_controller.get_all_status()
        drones = list(statuses.keys())
        
        # Check for current conflicts
        for i in range(len(drones)):
            for j in range(i + 1, len(drones)):
                drone1_id = drones[i]
                drone2_id = drones[j]
                
                drone1 = statuses[drone1_id]
                drone2 = statuses[drone2_id]
                
                if drone1.get('position') and drone2.get('position'):
                    pos1 = drone1['position']
                    pos2 = drone2['position']
                    
                    if all(k in pos1 for k in ['x', 'y', 'z']) and all(k in pos2 for k in ['x', 'y', 'z']):
                        dx = pos1['x'] - pos2['x']
                        dy = pos1['y'] - pos2['y']
                        dz = pos1['z'] - pos2['z']
                        distance = math.sqrt(dx**2 + dy**2 + dz**2)
                        
                        if distance < 3.0:  # Show conflicts within 3m
                            conflict = {
                                'drone1': drone1_id,
                                'drone2': drone2_id,
                                'distance': distance,
                                'timestamp': datetime.now().isoformat(),
                                'severity': 'high' if distance < 1.0 else 'medium' if distance < 2.0 else 'low',
                                'position1': pos1,
                                'position2': pos2
                            }
                            conflicts.append(conflict)
        
        # Add some historical conflicts
        for i in range(3):
            conflicts.append({
                'drone1': random.randint(1, 4),
                'drone2': random.randint(1, 4),
                'distance': random.uniform(0.5, 2.5),
                'timestamp': (datetime.now() - timedelta(minutes=random.randint(5, 60))).isoformat(),
                'severity': random.choice(['low', 'medium', 'high']),
                'position1': {'x': random.uniform(-50, 50), 'y': random.uniform(-50, 50), 'z': random.uniform(0, 30)},
                'position2': {'x': random.uniform(-50, 50), 'y': random.uniform(-50, 50), 'z': random.uniform(0, 30)}
            })
        
        return jsonify({
            'success': True,
            'conflicts': conflicts,
            'count': len(conflicts),
            'time_range': {
                'start': (datetime.now() - timedelta(hours=1)).isoformat(),
                'end': datetime.now().isoformat()
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/history/statistics', methods=['GET'])
def api_get_history_statistics():
    """Get comprehensive statistics for all drones"""
    try:
        # Get historical trajectories
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)
        
        statistics_data = {
            'total_missions': 0,
            'completed_missions': 0,
            'total_flight_time_minutes': 0,
            'total_distance_meters': 0,
            'conflict_count': 0,
            'drones': {}
        }
        
        # Get data for each drone
        for drone_id in range(1, 5):
            try:
                # Get trajectory
                trajectory = drone_controller.get_trajectory(drone_id, limit=100)
                
                if trajectory and len(trajectory) > 1:
                    # Calculate statistics
                    total_distance = 0
                    altitudes = []
                    times = []
                    
                    for i in range(1, len(trajectory)):
                        p1 = trajectory[i-1]
                        p2 = trajectory[i]
                        dx = p2.get('x', 0) - p1.get('x', 0)
                        dy = p2.get('y', 0) - p1.get('y', 0)
                        dz = p2.get('z', 0) - p1.get('z', 0)
                        total_distance += math.sqrt(dx**2 + dy**2 + dz**2)
                        
                        altitudes.append(p2.get('z', 0))
                        
                        if 'timestamp' in p1 and 'timestamp' in p2:
                            t1 = datetime.fromisoformat(p1['timestamp'])
                            t2 = datetime.fromisoformat(p2['timestamp'])
                            times.append((t2 - t1).total_seconds())
                    
                    # Calculate flight time
                    flight_time = sum(times) / 60 if times else 0  # in minutes
                    
                    # Calculate statistics
                    avg_altitude = statistics.mean(altitudes) if altitudes else 0
                    max_altitude = max(altitudes) if altitudes else 0
                    min_altitude = min(altitudes) if altitudes else 0
                    
                    statistics_data['drones'][drone_id] = {
                        'total_distance': total_distance,
                        'flight_time_minutes': flight_time,
                        'avg_altitude': avg_altitude,
                        'max_altitude': max_altitude,
                        'min_altitude': min_altitude,
                        'trajectory_points': len(trajectory),
                        'avg_speed_mps': total_distance / sum(times) if sum(times) > 0 else 0
                    }
                    
                    # Add to totals
                    statistics_data['total_distance_meters'] += total_distance
                    statistics_data['total_flight_time_minutes'] += flight_time
                    
            except Exception as e:
                print(f"Error calculating statistics for drone {drone_id}: {e}")
                continue
        
        # Get mission statistics
        missions = get_active_missions()
        statistics_data['total_missions'] = len(missions)
        statistics_data['completed_missions'] = len([m for m in missions if m.get('status') == 'completed'])
        
        # Get conflict statistics
        conflicts = check_realtime_conflicts({})
        statistics_data['conflict_count'] = len(conflicts)
        
        return jsonify({
            'success': True,
            'statistics': statistics_data,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/history/trajectory/<int:drone_id>', methods=['GET'])
def api_get_detailed_trajectory(drone_id):
    """Get detailed trajectory data for a specific drone"""
    try:
        # Get parameters
        start_time_str = request.args.get('start_time')
        end_time_str = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))
        
        # Parse times
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)
        
        if start_time_str:
            start_time = datetime.fromisoformat(start_time_str)
        if end_time_str:
            end_time = datetime.fromisoformat(end_time_str)
        
        # Get trajectory from controller
        trajectory = drone_controller.get_trajectory(drone_id, limit=limit)
        
        # Filter by time range
        filtered_trajectory = []
        for point in trajectory:
            try:
                point_time = datetime.fromisoformat(point.get('timestamp', ''))
                if start_time <= point_time <= end_time:
                    filtered_trajectory.append(point)
            except:
                continue
        
        # Calculate statistics
        stats = {
            'point_count': len(filtered_trajectory),
            'time_range': {
                'start': start_time.isoformat(),
                'end': end_time.isoformat()
            },
            'distance_stats': calculate_trajectory_statistics(filtered_trajectory)
        }
        
        return jsonify({
            'success': True,
            'drone_id': drone_id,
            'trajectory': filtered_trajectory,
            'statistics': stats
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

def calculate_trajectory_statistics(trajectory):
    """Calculate statistics from trajectory"""
    if len(trajectory) < 2:
        return {}
    
    total_distance = 0
    altitudes = []
    speeds = []
    
    for i in range(1, len(trajectory)):
        p1 = trajectory[i-1]
        p2 = trajectory[i]
        
        # Calculate distance
        dx = p2.get('x', 0) - p1.get('x', 0)
        dy = p2.get('y', 0) - p1.get('y', 0)
        dz = p2.get('z', 0) - p1.get('z', 0)
        distance = math.sqrt(dx**2 + dy**2 + dz**2)
        total_distance += distance
        
        # Collect altitudes
        altitudes.append(p2.get('z', 0))
        
        # Calculate speed if timestamps are available
        if 'timestamp' in p1 and 'timestamp' in p2:
            try:
                t1 = datetime.fromisoformat(p1['timestamp'])
                t2 = datetime.fromisoformat(p2['timestamp'])
                time_diff = (t2 - t1).total_seconds()
                if time_diff > 0:
                    speeds.append(distance / time_diff)
            except:
                pass
    
    return {
        'total_distance': total_distance,
        'avg_altitude': statistics.mean(altitudes) if altitudes else 0,
        'max_altitude': max(altitudes) if altitudes else 0,
        'min_altitude': min(altitudes) if altitudes else 0,
        'avg_speed': statistics.mean(speeds) if speeds else 0,
        'max_speed': max(speeds) if speeds else 0
    }

@app.route('/api/drones', methods=['GET'])
def api_get_drones():
    """Get status of all drones"""
    try:
        statuses = drone_controller.get_all_status()
        
        # Convert to list format
        drones_list = []
        for drone_id, status in statuses.items():
            drones_list.append({
                'id': drone_id,
                'name': f'Drone {drone_id}',
                'status': status.get('status', 'unknown'),
                'position': status.get('position', {}),
                'battery': status.get('battery', 0),
                'mode': status.get('mode', 'UNKNOWN'),
                'armed': status.get('armed', False),
                'trajectory': status.get('trajectory', [])
            })
        
        return jsonify({
            'success': True,
            'drones': drones_list,
            'timestamp': datetime.now().isoformat(),
            'count': len(drones_list),
            'system_running': system_running
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/missions', methods=['GET'])
def api_get_missions():
    """Get all missions"""
    try:
        missions = get_active_missions()
        return jsonify({
            'success': True,
            'missions': missions,
            'count': len(missions)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule', methods=['POST'])
def api_schedule_mission():
    """Schedule a new mission with deconfliction check"""
    try:
        data = request.json
        
        # Validate required fields
        if not data or 'drone_id' not in data or 'waypoints' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing required fields: drone_id and waypoints'
            }), 400
        
        try:
            drone_id = int(data['drone_id'])
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid drone_id'}), 400

        waypoints = data['waypoints']
        
        # Parse times with defaults
        start_time = datetime.now() + timedelta(seconds=10)
        end_time = start_time + timedelta(minutes=5)
        
        if 'start_time' in data and data['start_time']:
            start_time = datetime.fromisoformat(data['start_time'])
        if 'end_time' in data and data['end_time']:
            end_time = datetime.fromisoformat(data['end_time'])
        
        # Validate drone ID
        if drone_id not in [1, 2, 3, 4]:
            return jsonify({
                'success': False,
                'error': 'Invalid drone ID. Must be 1-4'
            }), 400
        
        # Check deconfliction
        # Enqueue background work. Provide a small request_id so client can match the result event.
        request_meta = {'request_id': f"req-{int(time.time()*1000)}"}
        _background_executor.submit(_schedule_worker, drone_id, waypoints, start_time, end_time, request_meta)

        # Return 202 Accepted — scheduling will happen in background. Client may listen for 'schedule_result' SocketIO event.
        return jsonify({'success': True, 'message': 'Scheduling started', 'request_id': request_meta['request_id']}), 202
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/control/<int:drone_id>', methods=['POST'])
def api_control_drone(drone_id):
    """Send direct control commands to a drone"""
    try:
        data = request.json
        if not data or 'command' not in data:
            return jsonify({'success': False, 'error': 'No command specified'}), 400
        
        command = data['command']
        result = False
        message = ''
        
        if command == 'arm':
            result = drone_controller.arm_drone(drone_id)
            message = 'Armed' if result else 'Failed to arm'
        elif command == 'disarm':
            result = drone_controller.disarm_drone(drone_id)
            message = 'Disarmed' if result else 'Failed to disarm'
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
        elif command == 'goto':
            x = float(data.get('x', 0))
            y = float(data.get('y', 0))
            z = float(data.get('z', 10))
            result = drone_controller.goto_position(drone_id, x, y, z)
            message = f'Going to ({x}, {y}, {z})' if result else 'Failed to go to position'
        elif command == 'stop':
            result = True
            message = 'Stopping drone'
        else:
            return jsonify({'success': False, 'error': f'Unknown command: {command}'}), 400
        
        # Send immediate update after control command
        statuses = drone_controller.get_all_status()
        conflicts = check_realtime_conflicts(statuses)
        with app.app_context():
            socketio.emit('drone_update', {
                'timestamp': datetime.now().isoformat(),
                'drones': statuses,
                'conflicts': conflicts
            })
        
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
        # Get trajectory from drone controller
        trajectory = drone_controller.get_trajectory(drone_id, limit=100)
        
        return jsonify({
            'success': True,
            'drone_id': drone_id,
            'trajectory': trajectory,
            'point_count': len(trajectory)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/emergency', methods=['POST'])
def api_emergency_stop():
    """Emergency stop all drones"""
    try:
        result = drone_controller.emergency_stop_all()
        message = 'Emergency stop initiated for all drones'
        
        # Send immediate update
        statuses = drone_controller.get_all_status()
        with app.app_context():
            socketio.emit('drone_update', {
                'timestamp': datetime.now().isoformat(),
                'drones': statuses,
                'conflicts': []
            })
        
        return jsonify({
            'success': result,
            'message': message
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/system/status', methods=['GET'])
def api_system_status():
    """Get system status"""
    try:
        return jsonify({
            'success': True,
            'system_running': system_running,
            'drone_count': drone_controller.drone_count if drone_controller else 0,
            'update_thread_alive': update_thread.is_alive() if update_thread else False,
            'connected_clients': len(connected_clients),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== ADD THESE API ENDPOINTS ====================

@app.route('/api/historical/trajectories', methods=['GET'])
def api_get_historical_trajectories():
    """Get historical trajectories for all drones for the past hour"""
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1)
        
        # Get trajectories for all drones (1-4)
        all_trajectories = {}
        
        for drone_id in range(1, 5):  # Drone IDs 1-4
            try:
                # Get trajectory from database
                trajectory_points = get_drone_trajectory(drone_id, start_time, end_time)
                
                # Format the data
                formatted_points = []
                for point in trajectory_points:
                    # Convert database point to proper format
                    lat = -35.363217 + (point.get('y', 0) / 111000)
                    lon = 149.165252 + (point.get('x', 0) / (111000 * math.cos(math.radians(-35.363217))))
                    
                    formatted_points.append({
                        'timestamp': point.get('timestamp'),
                        'x': point.get('x', 0),
                        'y': point.get('y', 0),
                        'z': point.get('z', 0),
                        'lat': lat,
                        'lon': lon
                    })
                
                if formatted_points:
                    all_trajectories[str(drone_id)] = formatted_points
                    
            except Exception as e:
                print(f"Error getting trajectory for drone {drone_id}: {e}")
                continue
        
        return jsonify({
            'success': True,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'trajectories': all_trajectories,
            'drone_count': len(all_trajectories)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/future/trajectories', methods=['GET'])
def api_get_future_trajectories():
    """Get future trajectories for all drones"""
    try:
        # Get time range from query parameters
        start_time_str = request.args.get('start_time')
        end_time_str = request.args.get('end_time')
        
        # Default: current time to 30 minutes in future
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=30)
        
        if start_time_str:
            start_time = datetime.fromisoformat(start_time_str)
        if end_time_str:
            end_time = datetime.fromisoformat(end_time_str)
        
        # Get future trajectories from database
        # We need to import or implement get_future_trajectories
        # For now, let's create a dummy implementation
        future_trajectories = {}
        
        # Try to get from database if function exists
        try:
            from database import get_future_trajectories
            db_trajectories = get_future_trajectories(start_time, end_time)
            
            # Convert to our format
            for drone_id, points in db_trajectories.items():
                formatted_points = []
                for point in points:
                    lat = -35.363217 + (point['position'][1] / 111000)
                    lon = 149.165252 + (point['position'][0] / (111000 * math.cos(math.radians(-35.363217))))
                    
                    formatted_points.append({
                        'timestamp': point['timestamp'],
                        'x': point['position'][0],
                        'y': point['position'][1],
                        'z': point['position'][2],
                        'lat': lat,
                        'lon': lon,
                        'is_waypoint': point.get('is_waypoint', False),
                        'segment': point.get('segment', 0)
                    })
                
                future_trajectories[str(drone_id)] = formatted_points
                
        except ImportError:
            # Generate dummy future trajectories
            print("Creating dummy future trajectories")
            for drone_id in range(1, 5):
                points = []
                current_time = start_time
                time_step = (end_time - start_time) / 20
                
                for i in range(20):
                    t = i / 20.0
                    x = 50 * math.sin(2 * math.pi * t + drone_id * 0.5)
                    y = 50 * math.cos(2 * math.pi * t + drone_id * 0.5)
                    z = 20 + 10 * math.sin(2 * math.pi * t * 2 + drone_id)
                    
                    lat = -35.363217 + (y / 111000)
                    lon = 149.165252 + (x / (111000 * math.cos(math.radians(-35.363217))))
                    
                    points.append({
                        'timestamp': current_time.isoformat(),
                        'x': x,
                        'y': y,
                        'z': z,
                        'lat': lat,
                        'lon': lon,
                        'is_waypoint': (i % 5 == 0),  # Every 5th point is a waypoint
                        'segment': i
                    })
                    
                    current_time += time_step
                
                future_trajectories[str(drone_id)] = points
        
        return jsonify({
            'success': True,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'trajectories': future_trajectories,
            'drone_count': len(future_trajectories)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== SOCKET.IO HANDLERS ====================

# Also add this WebSocket handler for historical playback
@socketio.on('request_historical_playback')
def handle_historical_playback(data):
    """Handle request for historical trajectory playback"""
    try:
        drone_id = data.get('drone_id')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        
        # Parse times
        start_time = datetime.fromisoformat(start_time_str) if start_time_str else datetime.now() - timedelta(hours=1)
        end_time = datetime.fromisoformat(end_time_str) if end_time_str else datetime.now()
        
        # Get trajectory from database
        trajectory = get_drone_trajectory(drone_id, start_time, end_time)
        
        # Format for frontend
        formatted_trajectory = []
        for point in trajectory:
            formatted_trajectory.append({
                'timestamp': point['timestamp'],
                'x': point.get('x', 0),
                'y': point.get('y', 0),
                'z': point.get('z', 0),
                'lat': -35.363217 + (point.get('y', 0) / 111000),
                'lon': 149.165252 + (point.get('x', 0) / (111000 * math.cos(math.radians(-35.363217))))
            })
        
        emit('historical_trajectory', {
            'drone_id': drone_id,
            'trajectory': formatted_trajectory,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat()
        })
        
    except Exception as e:
        emit('error', {'message': f'Error getting historical data: {str(e)}'})
        
@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    client_id = request.sid
    connected_clients.add(client_id)
    print(f'✅ Client connected: {client_id} (Total: {len(connected_clients)})')
    
    # Send initial data immediately
    statuses = drone_controller.get_all_status()
    conflicts = check_realtime_conflicts(statuses)
    
    emit('connected', {
        'message': 'Connected to UAV Deconfliction System',
        'timestamp': datetime.now().isoformat(),
        'drone_count': drone_controller.drone_count if drone_controller else 0,
        'client_id': client_id
    })
    
    # Send immediate update
    emit('drone_update', {
        'timestamp': datetime.now().isoformat(),
        'drones': statuses,
        'conflicts': conflicts
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    client_id = request.sid
    if client_id in connected_clients:
        connected_clients.remove(client_id)
    print(f'❌ Client disconnected: {client_id} (Total: {len(connected_clients)})')

@socketio.on('request_update')
def handle_update_request():
    """Handle update request from client"""
    try:
        statuses = drone_controller.get_all_status()
        conflicts = check_realtime_conflicts(statuses)
        
        emit('drone_update', {
            'timestamp': datetime.now().isoformat(),
            'drones': statuses,
            'conflicts': conflicts
        })
    except Exception as e:
        print(f"Error in update request: {e}")
        emit('error', {'message': str(e)})

@socketio.on('request_historical_state')
def handle_historical_state(data):
    """Handle request for historical drone states"""
    try:
        t = data.get('time', 0)
        # For now, just send current state
        statuses = drone_controller.get_all_status()
        
        emit('historical_update', {
            'timestamp': datetime.now().isoformat(),
            'simulation_time': t,
            'drones': statuses
        })
    except Exception as e:
        print(f"Error in historical state: {e}")

@socketio.on('control_drone')
def handle_control_drone(data):
    """Handle drone control via WebSocket"""
    try:
        drone_id = data.get('drone_id')
        command = data.get('command')
        
        if not drone_id or not command:
            emit('control_response', {'success': False, 'error': 'Missing parameters'})
            return
        
        result = False
        message = ''
        
        if command == 'takeoff':
            altitude = data.get('altitude', 10.0)
            result = drone_controller.takeoff(drone_id, altitude)
            message = f'Drone {drone_id} taking off to {altitude}m'
        elif command == 'land':
            result = drone_controller.land(drone_id)
            message = f'Drone {drone_id} landing'
        elif command == 'rtl':
            result = drone_controller.return_to_launch(drone_id)
            message = f'Drone {drone_id} returning to launch'
        elif command == 'arm':
            result = drone_controller.arm_drone(drone_id)
            message = f'Drone {drone_id} armed'
        elif command == 'disarm':
            result = drone_controller.disarm_drone(drone_id)
            message = f'Drone {drone_id} disarmed'
        else:
            message = f'Unknown command: {command}'
        
        # Send immediate update
        statuses = drone_controller.get_all_status()
        conflicts = check_realtime_conflicts(statuses)
        socketio.emit('drone_update', {
            'timestamp': datetime.now().isoformat(),
            'drones': statuses,
            'conflicts': conflicts
        })
        
        emit('control_response', {
            'success': result,
            'message': message,
            'drone_id': drone_id,
            'command': command
        })
        
    except Exception as e:
        emit('control_response', {'success': False, 'error': str(e)})

if __name__ == '__main__':
    # Initialize system
    init_system()
    
    # Start update thread
    start_update_thread()
    
    print(f"\n{'='*60}")
    print("UAV Deconfliction System Server Starting")
    print(f"Dashboard: http://localhost:5000")
    print(f"Visualization: http://localhost:5000/visualization")
    print(f"Update rate: 2 Hz (500ms interval)")
    print(f"{'='*60}\n")
    
    # Run Flask app
    try:
        socketio.run(app, 
                     host='0.0.0.0', 
                     port=5000, 
                     debug=False,  # Set to False for production
                     use_reloader=False,
                     allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\nShutting down server...")
        system_running = False
        if update_thread:
            update_thread.join(timeout=2)
        print("Server shutdown complete.")