"""
SQLite database operations for drone trajectories and missions
"""
import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
import logging
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
DB_PATH = "drones.db"
DB_LOCK = threading.Lock()

@contextmanager
def get_db_connection():
    """
    Context manager for database connections.

    - Uses a longer timeout so concurrent writers wait instead of failing.
    - Enables WAL mode which allows concurrent readers and writers.
    - Sets busy_timeout so SQLite will retry for a short period.
    - Commits once on successful exit, rolls back on exception.
    """
    # timeout in seconds: allow writers to wait
    conn = sqlite3.connect(DB_PATH, timeout=30, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row

    try:
        # Recommended pragmas for concurrent reader/writer workloads
        # These are lightweight to run per-connection; journal_mode=WAL is persistent for the DB file.
        try:
            conn.execute('PRAGMA journal_mode=WAL;')
        except Exception as e:
            logger.debug(f"PRAGMA journal_mode=WAL failed: {e}")

        # Instruct SQLite to wait up to 30 seconds if the DB is locked
        try:
            conn.execute('PRAGMA busy_timeout = 30000;')  # milliseconds
        except Exception as e:
            logger.debug(f"PRAGMA busy_timeout failed: {e}")

        # Use normal synchronous to balance durability and performance
        try:
            conn.execute('PRAGMA synchronous = NORMAL;')
        except Exception as e:
            logger.debug(f"PRAGMA synchronous failed: {e}")

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
            current_x REAL DEFAULT 0.0,
            current_y REAL DEFAULT 0.0,
            current_z REAL DEFAULT 0.0,
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
        
        # Trajectory points table (historical/actual trajectory)
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
        
        # FUTURE TRAJECTORY TABLE - NEW
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS future_trajectory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drone_id INTEGER NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            z REAL NOT NULL,
            segment INTEGER DEFAULT 0,
            waypoint_index INTEGER,
            is_waypoint BOOLEAN DEFAULT 0,
            mission_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (drone_id) REFERENCES drones(drone_id),
            FOREIGN KEY (mission_id) REFERENCES missions(mission_id)
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
            conflict_time TIMESTAMP,  -- Added: time when conflict occurs
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
        CREATE INDEX IF NOT EXISTS idx_future_trajectory_drone_time 
        ON future_trajectory(drone_id, timestamp)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_future_trajectory_time 
        ON future_trajectory(timestamp)
        ''')
        
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_missions_time 
        ON missions(start_time, end_time)
        ''')
        
        logger.info("Database initialized successfully")

def add_drone(drone_id, current_x=0.0, current_y=0.0, current_z=0.0):
    """Add a new drone to the database"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT OR REPLACE INTO drones (drone_id, current_x, current_y, current_z, last_update)
        VALUES (?, ?, ?, ?, ?)
        ''', (drone_id, current_x, current_y, current_z, datetime.now()))
        logger.info(f"Drone {drone_id} added/updated in database")

def update_drone_status(drone_id, status=None, armed=None, mode=None, 
                       current_x=None, current_y=None, current_z=None, battery=None):
    """Update drone status in database"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Ensure drone exists
        cursor.execute(
            "INSERT OR IGNORE INTO drones (drone_id) VALUES (?)",
            (drone_id,)
        )

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
        
        if current_x is not None:
            updates.append("current_x = ?")
            params.append(current_x)
        
        if current_y is not None:
            updates.append("current_y = ?")
            params.append(current_y)
        
        if current_z is not None:
            updates.append("current_z = ?")
            params.append(current_z)
        
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

def get_drone_current_position(drone_id):
    """Get current position of a drone as a list [x, y, z]"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT current_x, current_y, current_z FROM drones WHERE drone_id = ?',
            (drone_id,)
        )
        result = cursor.fetchone()
        if result:
            return [result[0], result[1], result[2]]
        return [0, 0, 10]  # Default position if not found

def add_trajectory_point(drone_id, x, y, z, timestamp=None):
    """Add a trajectory point for a drone (actual trajectory)"""
    if timestamp is None:
        timestamp = datetime.now()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO trajectory_points (drone_id, timestamp, x, y, z)
        VALUES (?, ?, ?, ?, ?)
        ''', (drone_id, timestamp, x, y, z))

def store_future_trajectory(drone_id, trajectory, mission_id=None):
    """Store future trajectory points in database"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # First, delete any existing future trajectory for this drone
        cursor.execute(
            'DELETE FROM future_trajectory WHERE drone_id = ?',
            (drone_id,)
        )
        
        # Insert new trajectory points
        for point in trajectory:
            # Ensure timestamp is a string for storage
            timestamp = point['timestamp']
            if isinstance(timestamp, datetime):
                timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            else:
                timestamp_str = str(timestamp)
            
            cursor.execute('''
            INSERT INTO future_trajectory 
            (drone_id, timestamp, x, y, z, segment, waypoint_index, is_waypoint, mission_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                point['drone_id'],
                timestamp_str,
                point['position'][0],
                point['position'][1],
                point['position'][2],
                point.get('segment', 0),
                point.get('waypoint_index'),
                1 if point.get('is_waypoint', False) else 0,
                mission_id
            ))
        
        logger.info(f"Stored future trajectory for drone {drone_id} with {len(trajectory)} points")
def get_future_trajectories(start_time, end_time):
    """Get all future trajectories within a time window"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Convert datetime objects to strings for SQL query if needed
        if isinstance(start_time, datetime):
            start_time_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
        else:
            start_time_str = start_time
            
        if isinstance(end_time, datetime):
            end_time_str = end_time.strftime('%Y-%m-%d %H:%M:%S')
        else:
            end_time_str = end_time
        
        cursor.execute('''
        SELECT drone_id, timestamp, x, y, z, segment, waypoint_index, is_waypoint
        FROM future_trajectory
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY drone_id, timestamp
        ''', (start_time_str, end_time_str))
        
        trajectories = {}
        for row in cursor.fetchall():
            drone_id = row[0]
            if drone_id not in trajectories:
                trajectories[drone_id] = []
            
            # Convert timestamp string to datetime
            timestamp = row[1]
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except ValueError:
                    timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            
            trajectories[drone_id].append({
                'drone_id': drone_id,
                'timestamp': timestamp,
                'position': [row[2], row[3], row[4]],
                'segment': row[5],
                'waypoint_index': row[6],
                'is_waypoint': bool(row[7])
            })
        
        return trajectories

def delete_future_trajectory(drone_id=None, mission_id=None):
    """Delete future trajectory for a drone or mission"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        if drone_id is not None and mission_id is not None:
            cursor.execute(
                'DELETE FROM future_trajectory WHERE drone_id = ? AND mission_id = ?',
                (drone_id, mission_id)
            )
        elif drone_id is not None:
            cursor.execute(
                'DELETE FROM future_trajectory WHERE drone_id = ?',
                (drone_id,)
            )
        elif mission_id is not None:
            cursor.execute(
                'DELETE FROM future_trajectory WHERE mission_id = ?',
                (mission_id,)
            )
        
        deleted = cursor.rowcount
        logger.info(f"Deleted {deleted} future trajectory points")

def create_mission(drone_id, waypoints, start_time, end_time):
    """Create a new mission - Store as LOCAL TIME strings"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Store as formatted string in local time
        start_time_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
        end_time_str = end_time.strftime('%Y-%m-%d %H:%M:%S')
        waypoints_json = json.dumps(waypoints)
        
        cursor.execute('''
        INSERT INTO missions (drone_id, waypoints, start_time, end_time, status)
        VALUES (?, ?, ?, ?, 'scheduled')
        ''', (drone_id, waypoints_json, start_time_str, end_time_str))
        
        mission_id = cursor.lastrowid
        logger.info(f"Created mission {mission_id} for drone {drone_id}")
        logger.info(f"Stored times - Start: {start_time_str}, End: {end_time_str}")
        return mission_id

def get_active_missions():
    """Get all active missions - Return consistent types"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT mission_id, drone_id, waypoints, start_time, end_time, status, 
               conflict_detected, conflict_details, actual_path, created_at
        FROM missions 
        WHERE status IN ('scheduled', 'active', 'executing')
        ORDER BY start_time
        ''')
        
        missions = []
        for row in cursor.fetchall():
            mission = {
                'mission_id': row[0],
                'drone_id': row[1],
                'waypoints': json.loads(row[2]) if row[2] else [],
                'start_time': row[3],  # Keep as string initially
                'end_time': row[4],    # Keep as string initially
                'status': row[5],
                'conflict_detected': bool(row[6]),
                'conflict_details': json.loads(row[7]) if row[7] else {},
                'actual_path': json.loads(row[8]) if row[8] else [],
                'created_at': row[9]
            }
            
            missions.append(mission)
        
        return missions

def parse_datetime(dt_str):
    """Parse datetime string with multiple format support"""
    if isinstance(dt_str, datetime):
        return dt_str
    
    if not isinstance(dt_str, str):
        return datetime.now()
    
    # Try ISO format
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        pass
    
    # Try common formats
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y/%m/%d %H:%M:%S',
        '%d-%m-%Y %H:%M:%S',
        '%d/%m/%Y %H:%M:%S'
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    
    # If all else fails, return current time
    logger.warning(f"Could not parse datetime: {dt_str}")
    return datetime.now()

def parse_mission_datetimes(mission):
    """Parse datetime strings in a mission dict"""
    if isinstance(mission.get('start_time'), str):
        try:
            mission['start_time'] = datetime.strptime(mission['start_time'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                mission['start_time'] = datetime.fromisoformat(mission['start_time'])
            except ValueError:
                pass
    
    if isinstance(mission.get('end_time'), str):
        try:
            mission['end_time'] = datetime.strptime(mission['end_time'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                mission['end_time'] = datetime.fromisoformat(mission['end_time'])
            except ValueError:
                pass
    
    return mission

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

def log_conflict(drone1_id, drone2_id, distance, position, conflict_time=None):
    """Log a conflict between drones"""
    if conflict_time is None:
        conflict_time = datetime.now()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        position_json = json.dumps(position)
        cursor.execute('''
        INSERT INTO conflicts (drone1_id, drone2_id, distance, position, conflict_time)
        VALUES (?, ?, ?, ?, ?)
        ''', (drone1_id, drone2_id, distance, position_json, conflict_time))
        logger.warning(f"Conflict logged between drone {drone1_id} and {drone2_id} at {conflict_time}")

def get_all_drones_status():
    """Get status of all drones"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT drone_id, status, armed, mode, current_x, current_y, current_z, battery, last_update
        FROM drones ORDER BY drone_id
        ''')
        return [dict(row) for row in cursor.fetchall()]

def cleanup_old_data(days_to_keep=7):
    """Clean up old trajectory data"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cutoff_date = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp() - (days_to_keep * 86400)
        
        # Clean up old trajectory points
        cursor.execute('''
        DELETE FROM trajectory_points 
        WHERE timestamp < datetime(?, 'unixepoch')
        ''', (cutoff_date,))
        
        # Clean up old future trajectories that have passed
        cursor.execute('''
        DELETE FROM future_trajectory 
        WHERE timestamp < datetime('now', '-1 hour')
        ''')
        
        deleted_traj = cursor.rowcount
        
        cursor.execute('''
        DELETE FROM conflicts 
        WHERE timestamp < datetime(?, 'unixepoch')
        ''', (cutoff_date,))
        
        deleted_conflicts = cursor.rowcount
        logger.info(f"Cleaned up {deleted_traj} old trajectory points and {deleted_conflicts} conflicts")
        return deleted_traj + deleted_conflicts

def update_mission_status(mission_id, status, conflict_detected=False, 
                         conflict_details=None, actual_path=None):
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

def get_future_trajectory_by_drone(drone_id):
    """Get future trajectory for a specific drone"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT timestamp, x, y, z, segment, waypoint_index, is_waypoint, mission_id
        FROM future_trajectory
        WHERE drone_id = ?
        ORDER BY timestamp
        ''', (drone_id,))
        
        trajectory = []
        for row in cursor.fetchall():
            # Convert timestamp string to datetime
            timestamp = row[0]
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp)
                except ValueError:
                    timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            
            trajectory.append({
                'drone_id': drone_id,
                'timestamp': timestamp,
                'position': [row[1], row[2], row[3]],
                'segment': row[4],
                'waypoint_index': row[5],
                'is_waypoint': bool(row[6]),
                'mission_id': row[7]
            })
        
        return trajectory


def delete_old_future_trajectories(cutoff_time=None):
    """Delete future trajectories older than cutoff_time"""
    if cutoff_time is None:
        cutoff_time = datetime.now()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM future_trajectory WHERE timestamp < ?',
            (cutoff_time,)
        )
        deleted = cursor.rowcount
        logger.info(f"Deleted {deleted} old future trajectory points")
        return deleted
    
def get_future_trajectories_for_drone(drone_id, start_time=None, end_time=None):
    """Get future trajectory for a specific drone within time range"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        query = '''
        SELECT timestamp, x, y, z, segment, waypoint_index, is_waypoint, mission_id
        FROM future_trajectory
        WHERE drone_id = ?
        '''
        params = [drone_id]
        
        if start_time and end_time:
            query += ' AND timestamp BETWEEN ? AND ?'
            params.extend([start_time, end_time])
        
        query += ' ORDER BY timestamp'
        cursor.execute(query, params)
        
        trajectory = []
        for row in cursor.fetchall():
            timestamp = row[0]
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp)
                except ValueError:
                    timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            
            trajectory.append({
                'drone_id': drone_id,
                'timestamp': timestamp,
                'position': [row[1], row[2], row[3]],
                'segment': row[4],
                'waypoint_index': row[5],
                'is_waypoint': bool(row[6]),
                'mission_id': row[7]
            })
        
        return trajectory    