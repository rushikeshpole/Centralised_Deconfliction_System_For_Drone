#!/usr/bin/env python3
"""
Test script to verify drone connections
"""
from pymavlink import mavutil
import time

def test_drone_connections():
    """Test connection to all drones"""
    ports = [
        ('udp:127.0.0.1:14550', 'Drone 1'),
        ('udp:127.0.0.1:14560', 'Drone 2'),
        ('udp:127.0.0.1:14570', 'Drone 3'),
        ('udp:127.0.0.1:14580', 'Drone 4'),
    ]
    
    for port, name in ports:
        try:
            print(f"Testing connection to {name} on {port}...")
            connection = mavutil.mavlink_connection(port)
            connection.wait_heartbeat(timeout=3)
            print(f"✓ {name} connected successfully!")
            
            # Try to get some data
            msg = connection.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
            if msg:
                print(f"  Mode: {msg.custom_mode}, Armed: {(msg.base_mode & 128) != 0}")
            
            connection.close()
            
        except Exception as e:
            print(f"✗ Failed to connect to {name}: {e}")

if __name__ == "__main__":
    print("Testing drone connections...")
    print("=" * 50)
    test_drone_connections()
    print("=" * 50)
    print("Test complete!")
