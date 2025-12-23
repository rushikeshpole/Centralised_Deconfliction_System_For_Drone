#!/usr/bin/env python3
"""
Main launcher for UAV Deconfliction System
"""
import sys
import os
import signal
import time
from datetime import datetime, timezone
from app import app, socketio, init_system, start_update_thread, system_running
import pytz
utc = pytz.UTC
def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    print("\nShutting down UAV Deconfliction System...")
    global system_running
    system_running = False
    time.sleep(2)
    sys.exit(0)

def main():
    """Main entry point"""
    print("="*60)
    print("UAV Strategic Deconfliction System")
    print("FlytBase Robotics Assignment 2025")
    print("="*60)
    
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    # Initialize system
    try:
        init_system()
    except Exception as e:
        print(f"Failed to initialize system: {e}")
        sys.exit(1)
    
    # Start update thread
    start_update_thread()
    
    # Print startup information
    print("\n" + "="*60)
    print("SYSTEM READY")
    print("="*60)
    print("Access the web interface at: http://localhost:5000")
    print("")
    print("Available endpoints:")
    print("  /                 - Main dashboard")
    print("  /visualization    - 2D/3D visualization")
    print("  /history/<id>     - Drone history")
    print("")
    print("API endpoints:")
    print("  GET  /api/drones            - Get all drone status")
    print("  GET  /api/missions          - Get all missions")
    print("  POST /api/schedule          - Schedule new mission")
    print("  POST /api/control/<id>      - Control drone directly")
    print("  GET  /api/trajectory/<id>   - Get drone trajectory")
    print("  POST /api/emergency         - Emergency stop all drones")
    print("="*60)
    print("\nPress Ctrl+C to shutdown")
    print("="*60)
    
    # Run the Flask app
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, log_output=False)
    except KeyboardInterrupt:
        print("\nShutdown complete.")
    except Exception as e:
        print(f"Error running server: {e}")
        import traceback
        traceback.print_exc()        
        sys.exit(1)

if __name__ == "__main__":
    main()
