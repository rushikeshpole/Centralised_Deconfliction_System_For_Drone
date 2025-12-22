"""
Simplified database operations for quick setup
"""
import sqlite3
import json
from datetime import datetime

def init_db():
    """Initialize database"""
    conn = sqlite3.connect('drones.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drone_id INTEGER UNIQUE,
        status TEXT,
        position TEXT,
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
        status TEXT
    )
    ''')
    
    conn.commit()
    conn.close()

def update_mission_status(mission_id, status):
    """Update mission status"""
    conn = sqlite3.connect('drones.db')
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE missions SET status = ? WHERE mission_id = ?',
        (status, mission_id)
    )
    conn.commit()
    conn.close()

def create_mission(drone_id, waypoints, start_time, end_time):
    """Create a new mission"""
    conn = sqlite3.connect('drones.db')
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO missions (drone_id, waypoints, start_time, end_time, status)
           VALUES (?, ?, ?, ?, 'scheduled')''',
        (drone_id, json.dumps(waypoints), start_time, end_time)
    )
    mission_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return mission_id

def get_active_missions():
    """Get active missions"""
    conn = sqlite3.connect('drones.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM missions WHERE status IN ('scheduled', 'active', 'executing')"
    )
    missions = cursor.fetchall()
    conn.close()
    return missions
