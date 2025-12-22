"""
SQLite database operations for drone trajectories and missions
"""
import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "drones.db"

@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()

def init_db():
    """Initialize database with required tables"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Drones table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS drones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drone_id INTEGER UNIQUE NOT NULL,
            status TEXT DEFAULT 'idle',
            armed BOOLEAN DEFAULT 0,
            mode TEXT DEFAULT '',
            position TEXT DEFAULT '{}',
            battery REAL DEFAULT 100.0,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Missions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS missions (
            mission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            drone_id INTEGER NOT NULL,
            waypoints TEXT NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'scheduled',
            conflict_detected BOOLEAN DEFAULT 0,
            conflict_details TEXT DEFAULT '{}',
            actual_path TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (drone_id) REFERENCES drones(drone_id)
        )
        ''')
        
        # Trajectory points table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS trajectory_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drone_id INTEGER NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            z REAL NOT NULL,
            FOREIGN KEY (drone_id) REFERENCES drones(drone_id)
        )
        ''')
        
        # Conflicts table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS conflicts (
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            drone1_id INTEGER NOT NULL,
            drone2_id INTEGER NOT NULL,
            distance REAL NOT NULL,
            position TEXT NOT NULL,
            resolved BOOLEAN DEFAULT 0,
            resolution_action TEXT DEFAULT ''
        )
        ''')
        
        # Create indexes for performance
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_trajectory_drone_time 
        ON trajectory_points(drone_id, timestamp)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_missions_time 
        ON missions(start_time, end_time)
        ''')
        
        logger.info("Database initialized successfully")

def add_drone(drone_id, initial_position=None):
    """Add a new drone to the database"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        position_json = json.dumps(initial_position or {})
        cursor.execute('''
        INSERT OR REPLACE INTO drones (drone_id, position, last_update)
        VALUES (?, ?, ?)
        ''', (drone_id, position_json, datetime.now()))
        logger.info(f"Drone {drone_id} added/updated in database")

def update_drone_status(drone_id, status=None, armed=None, mode=None, position=None, battery=None):
    """Update drone status in database"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Build update query dynamically
        updates = []
        params = []
        
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        
        if armed is not None:
            updates.append("armed = ?")
            params.append(1 if armed else 0)
        
        if mode is not None:
            updates.append("mode = ?")
            params.append(mode)
        
        if position is not None:
            updates.append("position = ?")
            params.append(json.dumps(position))
        
        if battery is not None:
            updates.append("battery = ?")
            params.append(battery)
        
        updates.append("last_update = ?")
        params.append(datetime.now())
        
        if updates:
            query = f"UPDATE drones SET {', '.join(updates)} WHERE drone_id = ?"
            params.append(drone_id)
            cursor.execute(query, params)
            logger.debug(f"Updated drone {drone_id} status")

def add_trajectory_point(drone_id, x, y, z):
    """Add a trajectory point for a drone"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO trajectory_points (drone_id, timestamp, x, y, z)
        VALUES (?, ?, ?, ?, ?)
        ''', (drone_id, datetime.now(), x, y, z))

def create_mission(drone_id, waypoints, start_time, end_time):
    """Create a new mission"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        waypoints_json = json.dumps(waypoints)
        cursor.execute('''
        INSERT INTO missions (drone_id, waypoints, start_time, end_time, status)
        VALUES (?, ?, ?, ?, 'scheduled')
        ''', (drone_id, waypoints_json, start_time, end_time))
        mission_id = cursor.lastrowid
        logger.info(f"Created mission {mission_id} for drone {drone_id}")
        return mission_id

def get_active_missions():
    """Get all active missions"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT * FROM missions 
        WHERE status IN ('scheduled', 'active', 'executing')
        ORDER BY start_time
        ''')
        return [dict(row) for row in cursor.fetchall()]

def get_drone_trajectory(drone_id, start_time=None, end_time=None):
    """Get trajectory points for a drone within time range"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        if start_time and end_time:
            cursor.execute('''
            SELECT * FROM trajectory_points 
            WHERE drone_id = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            ''', (drone_id, start_time, end_time))
        else:
            # Get last 1000 points
            cursor.execute('''
            SELECT * FROM trajectory_points 
            WHERE drone_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 1000
            ''', (drone_id,))
        
        return [dict(row) for row in cursor.fetchall()]

def log_conflict(drone1_id, drone2_id, distance, position):
    """Log a conflict between drones"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        position_json = json.dumps(position)
        cursor.execute('''
        INSERT INTO conflicts (drone1_id, drone2_id, distance, position)
        VALUES (?, ?, ?, ?)
        ''', (drone1_id, drone2_id, distance, position_json))
        logger.warning(f"Conflict logged between drone {drone1_id} and {drone2_id}")

def get_all_drones_status():
    """Get status of all drones"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM drones ORDER BY drone_id')
        return [dict(row) for row in cursor.fetchall()]

def cleanup_old_data(days_to_keep=7):
    """Clean up old trajectory data"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cutoff_date = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp() - (days_to_keep * 86400)
        
        cursor.execute('''
        DELETE FROM trajectory_points 
        WHERE timestamp < datetime(?, 'unixepoch')
        ''', (cutoff_date,))
        
        deleted = cursor.rowcount
        logger.info(f"Cleaned up {deleted} old trajectory points")
        return deleted
# Add these functions to database.py (after the existing functions)

def update_mission_status(mission_id, status, conflict_detected=False, conflict_details=None, actual_path=None):
    """Update mission status in database"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        updates.append("status = ?")
        params.append(status)
        
        if conflict_detected:
            updates.append("conflict_detected = ?")
            params.append(1)
        
        if conflict_details:
            updates.append("conflict_details = ?")
            params.append(json.dumps(conflict_details))
        
        if actual_path:
            updates.append("actual_path = ?")
            params.append(json.dumps(actual_path))
        
        params.append(mission_id)
        
        query = f"UPDATE missions SET {', '.join(updates)} WHERE mission_id = ?"
        cursor.execute(query, params)
        logger.info(f"Updated mission {mission_id} status to {status}")

def get_drone_position(drone_id):
    """Get current position of a drone"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT position FROM drones WHERE drone_id = ?',
            (drone_id,)
        )
        result = cursor.fetchone()
        if result and result[0]:
            return json.loads(result[0])
        return None

def get_other_trajectories(drone_id, start_time, end_time):
    """Get trajectories of other drones in time window"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Get all drone IDs except the specified one
        cursor.execute(
            'SELECT DISTINCT drone_id FROM trajectory_points WHERE drone_id != ?',
            (drone_id,)
        )
        other_drone_ids = [row[0] for row in cursor.fetchall()]
        
        trajectories = {}
        for other_id in other_drone_ids:
            cursor.execute(
                '''SELECT timestamp, x, y, z FROM trajectory_points 
                   WHERE drone_id = ? AND timestamp BETWEEN ? AND ?
                   ORDER BY timestamp''',
                (other_id, start_time, end_time)
            )
            
            points = []
            for row in cursor.fetchall():
                points.append({
                    'time': row[0],
                    'position': [row[1], row[2], row[3]]
                })
            
            if points:
                trajectories[other_id] = points
        
        return trajectories

def generate_mission_id():
    """Generate a new mission ID"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(mission_id) FROM missions')
        result = cursor.fetchone()
        return (result[0] or 0) + 1

def calculate_total_distance(waypoints):
    """Calculate total distance of waypoints"""
    if not waypoints or len(waypoints) < 2:
        return 0
    
    total = 0
    for i in range(len(waypoints) - 1):
        p1 = waypoints[i]
        p2 = waypoints[i + 1]
        distance = ((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2 + (p2[2]-p1[2])**2)**0.5
        total += distance
    
    return total

def distance_between(pos1, pos2):
    """Calculate distance between two positions"""
    return ((pos2[0]-pos1[0])**2 + (pos2[1]-pos1[1])**2 + (pos2[2]-pos1[2])**2)**0.5
