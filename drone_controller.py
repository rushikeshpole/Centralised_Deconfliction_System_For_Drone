"""
Enhanced MAVLink controller for multiple drones with trajectory recording
"""
import time
import threading
import json
from datetime import datetime
from pymavlink import mavutil
import logging
from database import update_drone_status, add_trajectory_point, log_conflict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EnhancedDroneController:
    def __init__(self, drone_count=5):
        self.drone_count = drone_count
        self.drones = {}
        self.trajectories = {}
        self.recording = False
        self.recording_thread = None
        
        # Flight modes mapping
        self.flight_modes = {
            'STABILIZE': 0,
            'ALT_HOLD': 2,
            'AUTO': 3,
            'GUIDED': 4,
            'LOITER': 5,
            'RTL': 6,
            'LAND': 9,
            'FLIP': 14,
        }
        
        # Default connection ports (adjust based on your setup)
        self.connection_ports = {
            1: 'udp:127.0.0.1:14550',
            2: 'udp:127.0.0.1:14560',
            3: 'udp:127.0.0.1:14570',
            4: 'udp:127.0.0.1:14580',
            5: 'udp:127.0.0.1:14590'
        }

        # GPS origin from your data
        self.gps_origin = {
            'lat': -353632621 / 1e7,  # -35.3632621
            'lon': 1491652264 / 1e7,  # 149.1652264
            'alt': 584190 / 1000.0    # 584.19 meters
        }
        
        # Safety buffer (meters)
        self.safety_buffer = 5.0

        self.connect_all()
        
    def connect_all(self):
        """Connect to all drones"""
        logger.info(f"Connecting to {self.drone_count} drones...")
        
        for drone_id in range(1, self.drone_count + 1):
            conn_str = self.connection_ports.get(drone_id, f'udp:127.0.0.1:{14549 + drone_id}')
            
            try:
                master = mavutil.mavlink_connection(conn_str)
                master.wait_heartbeat(timeout=5)
                
                self.drones[drone_id] = {
                    'master': master,
                    'system': master.target_system,
                    'component': master.target_component,
                    'armed': False,
                    'mode': 'UNKNOWN',
                    'position': None,
                    'velocity': None,
                    'battery': 100.0
                }
                
                # Initialize in database
                update_drone_status(drone_id, status='connected')
                logger.info(f"✓ Drone {drone_id} connected")
                
            except Exception as e:
                logger.error(f"✗ Failed to connect to Drone {drone_id}: {e}")
                # Create placeholder for disconnected drone
                self.drones[drone_id] = {
                    'master': None,
                    'system': drone_id,
                    'component': 1,
                    'armed': False,
                    'mode': 'DISCONNECTED',
                    'position': None,
                    'velocity': None,
                    'battery': 0.0
                }
    
    # In drone_controller.py, update the get_drone_status method:

    def get_drone_status(self, drone_id):
        """Get current status of a drone - Fixed for JSON serialization"""
        if drone_id not in self.drones or not self.drones[drone_id]['master']:
            # Return default status for disconnected drone
            return {
                'armed': False,
                'mode': 'DISCONNECTED',
                'position': {'x': 0, 'y': 0, 'z': 0, 'lat': 0, 'lon': 0},
                'battery': 0.0
            }
        
        master = self.drones[drone_id]['master']
        
        try:
            status_data = {
                'armed': self.drones[drone_id]['armed'],
                'mode': self.drones[drone_id]['mode'],
                'position': self.drones[drone_id]['position'] or {'x': 0, 'y': 0, 'z': 0, 'lat': 0, 'lon': 0},
                'battery': self.drones[drone_id]['battery']
            }
            
            # Try to get fresh data if possible
            msg = master.recv_match(type='HEARTBEAT', blocking=False, timeout=0.1)
            if msg:
                status_data['armed'] = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
                
                # Find mode name
                mode_id = msg.custom_mode
                for name, id_val in self.flight_modes.items():
                    if id_val == mode_id:
                        status_data['mode'] = name
                        break
            
            # Get position
            pos_msg = master.recv_match(type='GLOBAL_POSITION_INT', blocking=False, timeout=0.1)
            if pos_msg:
                lat = pos_msg.lat / 1e7 if pos_msg.lat != 0 else 0
                lon = pos_msg.lon / 1e7 if pos_msg.lon != 0 else 0
                alt = pos_msg.relative_alt / 1000.0
                
                # Convert to local coordinates
                x, y = self.latlon_to_local(lat, lon)
                
                status_data['position'] = {
                    'lat': lat, 'lon': lon, 'alt': alt,
                    'x': x, 'y': y, 'z': alt
                }
            
            # Get battery
            batt_msg = master.recv_match(type='SYS_STATUS', blocking=False, timeout=0.1)
            if batt_msg:
                if hasattr(batt_msg, 'battery_remaining'):
                    status_data['battery'] = batt_msg.battery_remaining
            
            # Update local state
            self.drones[drone_id].update(status_data)
            
            # Update database
            from database import update_drone_status
            update_drone_status(
                drone_id,
                status='active' if status_data['armed'] else 'idle',
                armed=status_data['armed'],
                mode=status_data['mode'],
                position=status_data['position'],
                battery=status_data['battery']
            )
            
            return status_data
            
        except Exception as e:
            logger.error(f"Error getting status for drone {drone_id}: {e}")
            # Return last known status
            return {
                'armed': self.drones[drone_id].get('armed', False),
                'mode': self.drones[drone_id].get('mode', 'ERROR'),
                'position': self.drones[drone_id].get('position', {'x': 0, 'y': 0, 'z': 0}),
                'battery': self.drones[drone_id].get('battery', 0.0)
            }
    
    def start_recording(self):
        """Start recording trajectories for all drones"""
        if self.recording:
            return
        
        self.recording = True
        self.recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self.recording_thread.start()
        logger.info("Started trajectory recording")
    
    def _recording_loop(self):
        """Background loop for recording trajectories"""
        while self.recording:
            for drone_id in self.drones.keys():
                status = self.get_drone_status(drone_id)
                if status and status['position']:
                    pos = status['position']
                    
                    # Add to trajectory database
                    add_trajectory_point(drone_id, pos['x'], pos['y'], pos['z'])
                    
                    # Keep in memory for quick access
                    if drone_id not in self.trajectories:
                        self.trajectories[drone_id] = []
                    
                    self.trajectories[drone_id].append({
                        'time': datetime.now(),
                        'x': pos['x'],
                        'y': pos['y'],
                        'z': pos['z']
                    })
                    
                    # Keep last 1000 points
                    if len(self.trajectories[drone_id]) > 1000:
                        self.trajectories[drone_id].pop(0)
            
            time.sleep(0.1)  # Record at 10Hz
    
    def stop_recording(self):
        """Stop recording trajectories"""
        self.recording = False
        if self.recording_thread:
            self.recording_thread.join(timeout=2)
        logger.info("Stopped trajectory recording")
    
    def set_flight_mode(self, drone_id, mode_name):
        """Set flight mode for specific drone"""
        if drone_id not in self.drones or not self.drones[drone_id]['master']:
            logger.error(f"Drone {drone_id} not connected")
            return False
        
        if mode_name not in self.flight_modes:
            logger.error(f"Unknown mode: {mode_name}")
            return False
        
        master = self.drones[drone_id]['master']
        mode_id = self.flight_modes[mode_name]
        
        logger.info(f"Drone {drone_id}: Setting mode to {mode_name}")
        
        master.mav.set_mode_send(
            master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id
        )
        
        time.sleep(1)
        return True
    
    def arm_drone(self, drone_id):
        """Arm a specific drone"""
        logger.info(f"Drone {drone_id}: Arming...")
        
        # Set to GUIDED mode
        self.set_flight_mode(drone_id, 'GUIDED')
        time.sleep(2)
        
        if drone_id not in self.drones or not self.drones[drone_id]['master']:
            return False
        
        master = self.drones[drone_id]['master']
        
        # Arm command
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            1, 0, 0, 0, 0, 0, 0
        )
        
        # Wait for arming
        for _ in range(10):
            time.sleep(1)
            status = self.get_drone_status(drone_id)
            if status and status['armed']:
                logger.info(f"✓ Drone {drone_id}: Armed successfully")
                return True
        
        logger.error(f"✗ Drone {drone_id}: Failed to arm")
        return False
    
    def takeoff(self, drone_id, altitude=10.0):
        """Command drone to takeoff"""
        logger.info(f"Drone {drone_id}: Taking off to {altitude}m...")
        
        if not self.arm_drone(drone_id):
            return False
        
        if drone_id not in self.drones or not self.drones[drone_id]['master']:
            return False
        
        master = self.drones[drone_id]['master']
        
        # Takeoff command
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0,
            0, 0, 0, 0, 0, 0, altitude
        )
        
        # Wait for takeoff
        for _ in range(30):
            time.sleep(1)
            status = self.get_drone_status(drone_id)
            if status and status['position'] and status['position']['z'] >= altitude * 0.8:
                logger.info(f"✓ Drone {drone_id}: Reached target altitude")
                return True
        
        logger.warning(f"Drone {drone_id}: Takeoff may not have completed")
        return True
    
    def goto_position(self, drone_id, x, y, z):
        """Send drone to local NED position"""
        logger.info(f"Drone {drone_id}: Going to position ({x}, {y}, {z})")
        
        if drone_id not in self.drones or not self.drones[drone_id]['master']:
            return False
        
        master = self.drones[drone_id]['master']
        
        # Set to GUIDED mode
        self.set_flight_mode(drone_id, 'GUIDED')
        time.sleep(1)
        
        # Send position target (NED coordinates)
        master.mav.set_position_target_local_ned_send(
            0,
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            0b110111111000,  # Position only
            x, y, -z,  # Note: z is negative in NED
            0, 0, 0, 0, 0, 0, 0, 0
        )
        
        return True
    
    def land(self, drone_id):
        """Land a specific drone"""
        logger.info(f"Drone {drone_id}: Landing...")
        return self.set_flight_mode(drone_id, 'LAND')
    
    def return_to_launch(self, drone_id):
        """Command drone to return to launch"""
        logger.info(f"Drone {drone_id}: Returning to launch...")
        return self.set_flight_mode(drone_id, 'RTL')
    
    def disarm_drone(self, drone_id):
        """Disarm a specific drone"""
        logger.info(f"Drone {drone_id}: Disarming...")
        
        if drone_id not in self.drones or not self.drones[drone_id]['master']:
            return False
        
        master = self.drones[drone_id]['master']
        
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0, 0, 0, 0, 0, 0, 0
        )
        
        time.sleep(2)
        return True
    
    def emergency_stop_all(self):
        """Emergency stop all drones"""
        logger.warning("EMERGENCY STOP: Landing all drones!")
        
        for drone_id in self.drones.keys():
            try:
                self.return_to_launch(drone_id)
                time.sleep(0.5)
            except:
                pass
        
        time.sleep(10)
        
        for drone_id in self.drones.keys():
            try:
                self.disarm_drone(drone_id)
            except:
                pass
        
        logger.info("Emergency procedure completed")
    
    def get_all_status(self):
        """Get status of all drones"""
        statuses = {}
        for drone_id in self.drones.keys():
            status = self.get_drone_status(drone_id)
            if status:
                statuses[drone_id] = status
        return statuses
    
    def latlon_to_local(self, lat, lon):
        """Convert latitude/longitude to local coordinates (simplified)"""
        # Simplified conversion for simulation
        # In real system, use proper UTM or other projection
        x = (lon - (-0.09)) * 111320  # Approximate conversion
        y = (lat - 51.505) * 111320
        return x, y
