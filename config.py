"""
Configuration for UAV Deconfliction System
"""

class Config:
    # Drone configuration
    DRONE_COUNT = 5
    SAFETY_BUFFER = 5.0  # meters
    
    # Connection ports
    DRONE_PORTS = {
        1: 'udp:127.0.0.1:14550',
        2: 'udp:127.0.0.1:14560',
        3: 'udp:127.0.0.1:14570',
        4: 'udp:127.0.0.1:14580',
        5: 'udp:127.0.0.1:14590'
    }
    
    # GPS Origin (from your GPS_GLOBAL_ORIGIN message)
    GPS_ORIGIN = {
        'lat': -35.3632621,   # -353632621 / 1e7
        'lon': 149.1652264,   # 1491652264 / 1e7
        'alt': 584.19         # 584190 / 1000.0
    }
    
    # Deconfliction parameters
    TIME_RESOLUTION = 0.5  # seconds
    LOOKAHEAD_TIME = 30.0  # seconds
    
    # Server configuration
    SERVER_HOST = '0.0.0.0'
    SERVER_PORT = 5000
    DEBUG = True
    
    # Database configuration
    DATABASE_PATH = 'drones.db'
    
    # Visualization
    UPDATE_INTERVAL = 1.0  # seconds
    TRAJECTORY_HISTORY = 50  # points to keep
