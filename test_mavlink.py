#!/usr/bin/env python3
"""
Test MAVLink connections to ArduPilot
"""
from pymavlink import mavutil
import time

def test_all_connections():
    """Test connections to all drone ports"""
    ports = {
        1: 'udp:127.0.0.1:14550',
        2: 'udp:127.0.0.1:14560', 
        3: 'udp:127.0.0.1:14570',
        4: 'udp:127.0.0.1:14580',
        5: 'udp:127.0.0.1:14590'
    }
    
    for drone_id, port in ports.items():
        print(f"\n{'='*50}")
        print(f"Testing Drone {drone_id} on {port}")
        print('='*50)
        
        try:
            # Create connection
            print(f"Connecting to {port}...")
            master = mavutil.mavlink_connection(port)
            
            # Wait for heartbeat
            print("Waiting for heartbeat...")
            master.wait_heartbeat(timeout=5)
            
            print(f"✓ Connected to Drone {drone_id}")
            print(f"  System: {master.target_system}, Component: {master.target_component}")
            
            # Get system status
            print("Getting system status...")
            master.mav.request_data_stream_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                1,  # Rate
                1   # Start sending
            )
            
            # Try to read some messages
            for i in range(5):
                msg = master.recv_match(type=['HEARTBEAT', 'SYS_STATUS'], blocking=True, timeout=2)
                if msg:
                    msg_type = msg.get_type()
                    if msg_type == 'HEARTBEAT':
                        armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
                        print(f"  Heartbeat - Armed: {armed}, Mode: {msg.custom_mode}")
                    elif msg_type == 'SYS_STATUS':
                        print(f"  System Status - Battery: {getattr(msg, 'battery_remaining', 'N/A')}%")
                else:
                    print(f"  No message received (attempt {i+1})")
                
                time.sleep(0.5)
            
            # Try to send a command
            print("\nTesting command sending...")
            try:
                # Try to get current mode
                master.mav.command_long_send(
                    master.target_system,
                    master.target_component,
                    mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
                    0, 0, 0, 0, 0, 0, 0, 0
                )
                
                ack = master.recv_match(type='COMMAND_ACK', blocking=True, timeout=2)
                if ack:
                    print(f"  Command ACK received: {ack.result}")
                else:
                    print("  No ACK received (might be normal)")
                    
            except Exception as e:
                print(f"  Command error: {e}")
            
            master.close()
            
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            print("  Make sure:")
            print("  1. ArduPilot SITL is running with correct port")
            print("  2. Gazebo is running with drone models")
            print("  3. No firewall blocking UDP ports")

def test_specific_commands():
    """Test specific arming/disarming commands"""
    print("\n" + "="*60)
    print("TESTING ARMING COMMANDS")
    print("="*60)
    
    port = 'udp:127.0.0.1:14550'
    
    try:
        master = mavutil.mavlink_connection(port)
        master.wait_heartbeat(timeout=5)
        
        print(f"Connected to system {master.target_system}")
        
        # Get current mode
        msg = master.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
        if msg:
            print(f"Current mode: {msg.custom_mode}, Armed: {(msg.base_mode & 128) != 0}")
        
        # Try to set GUIDED mode
        print("\nSetting GUIDED mode...")
        master.set_mode_apm('GUIDED')
        time.sleep(2)
        
        # Check if mode changed
        msg = master.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
        if msg:
            print(f"New mode: {msg.custom_mode}")
        
        # Try to arm
        print("\nTrying to arm...")
        master.arducopter_arm()
        time.sleep(3)
        
        # Check armed status
        msg = master.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
        if msg:
            armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
            print(f"Armed status: {armed}")
        
        # Try simple takeoff
        if armed:
            print("\nTrying takeoff...")
            master.mav.command_long_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0, 0, 0, 0, 0, 0, 0, 10  # Altitude 10m
            )
            time.sleep(2)
            
            ack = master.recv_match(type='COMMAND_ACK', blocking=True, timeout=2)
            if ack:
                print(f"Takeoff ACK: {ack.result}")
        
        master.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("MAVLink Connection Test")
    print("="*60)
    test_all_connections()
    test_specific_commands()
