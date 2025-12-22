"""
Mission execution and scheduling system
"""
import threading
import time
import json
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional
from database import create_mission, update_mission_status
from drone_controller import EnhancedDroneController

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MissionExecutor:
    def __init__(self, drone_controller: EnhancedDroneController):
        self.drone_controller = drone_controller
        self.active_missions: Dict[int, threading.Thread] = {}
        self.mission_queue = []
        
    def schedule_mission(self, drone_id: int, waypoints: List[List[float]],
                        start_time: datetime, end_time: datetime) -> Optional[int]:
        """
        Schedule a new mission for a drone
        
        Returns:
            Mission ID if scheduled successfully, None otherwise
        """
        try:
            # Create mission in database
            mission_id = create_mission(drone_id, waypoints, start_time, end_time)
            
            # Calculate wait time until mission start
            current_time = datetime.now()
            if start_time > current_time:
                wait_seconds = (start_time - current_time).total_seconds()
                
                # Create thread that will wait and execute
                thread = threading.Thread(
                    target=self._execute_mission_delayed,
                    args=(mission_id, drone_id, waypoints, start_time, end_time, wait_seconds),
                    daemon=True
                )
                
                self.active_missions[mission_id] = thread
                thread.start()
                
                logger.info(f"Scheduled mission {mission_id} for drone {drone_id}, "
                          f"starting in {wait_seconds:.1f} seconds")
            else:
                # Start immediately
                self._execute_mission(mission_id, drone_id, waypoints, start_time, end_time)
            
            return mission_id
            
        except Exception as e:
            logger.error(f"Failed to schedule mission: {e}")
            return None
    
    def _execute_mission_delayed(self, mission_id: int, drone_id: int,
                                waypoints: List[List[float]], start_time: datetime,
                                end_time: datetime, wait_seconds: float):
        """Wait for mission start time and then execute"""
        logger.info(f"Mission {mission_id}: Waiting {wait_seconds:.1f} seconds to start")
        time.sleep(wait_seconds)
        self._execute_mission(mission_id, drone_id, waypoints, start_time, end_time)
    
    def _execute_mission(self, mission_id: int, drone_id: int,
                        waypoints: List[List[float]], start_time: datetime,
                        end_time: datetime):
        """Execute a mission"""
        try:
            logger.info(f"Mission {mission_id}: Starting execution for drone {drone_id}")
            update_mission_status(mission_id, 'executing')
            
            # Calculate mission parameters
            mission_duration = (end_time - start_time).total_seconds()
            
            if mission_duration <= 0:
                logger.error(f"Mission {mission_id}: Invalid duration")
                update_mission_status(mission_id, 'failed')
                return
            
            # Arm and takeoff
            if not self.drone_controller.arm_drone(drone_id):
                logger.error(f"Mission {mission_id}: Failed to arm drone")
                update_mission_status(mission_id, 'failed')
                return
            
            takeoff_altitude = 5.0
            if not self.drone_controller.takeoff(drone_id, takeoff_altitude):
                logger.error(f"Mission {mission_id}: Failed to takeoff")
                update_mission_status(mission_id, 'failed')
                return
            
            # Execute waypoints
            waypoints_with_start = [[0, 0, takeoff_altitude]] + waypoints
            
            total_distance = 0
            segment_distances = []
            
            # Calculate distances
            for i in range(len(waypoints_with_start) - 1):
                p1 = waypoints_with_start[i]
                p2 = waypoints_with_start[i + 1]
                distance = ((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2 + (p2[2]-p1[2])**2)**0.5
                segment_distances.append(distance)
                total_distance += distance
            
            if total_distance == 0:
                logger.warning(f"Mission {mission_id}: No distance to travel")
                self._complete_mission(mission_id, drone_id)
                return
            
            # Calculate time per segment
            current_time = datetime.now()
            elapsed = (current_time - start_time).total_seconds()
            remaining_time = mission_duration - elapsed
            
            if remaining_time <= 0:
                logger.error(f"Mission {mission_id}: No time remaining")
                self._complete_mission(mission_id, drone_id)
                return
            
            # Execute each segment
            for i, (waypoint, segment_distance) in enumerate(zip(waypoints, segment_distances[1:])):
                x, y, z = waypoint
                
                # Calculate segment time based on proportional distance
                segment_time = (segment_distance / total_distance) * remaining_time
                
                logger.info(f"Mission {mission_id}: Going to waypoint {i+1} "
                          f"at ({x}, {y}, {z}) in {segment_time:.1f} seconds")
                
                # Send command
                if not self.drone_controller.goto_position(drone_id, x, y, z):
                    logger.error(f"Mission {mission_id}: Failed to go to waypoint {i+1}")
                    self._complete_mission(mission_id, drone_id, failed=True)
                    return
                
                # Wait for segment completion
                time_to_wait = min(segment_time, remaining_time)
                if time_to_wait > 0:
                    time.sleep(time_to_wait)
                
                remaining_time -= time_to_wait
                
                # Check for emergency stop
                if self._check_emergency_stop(mission_id):
                    logger.warning(f"Mission {mission_id}: Emergency stop triggered")
                    self._complete_mission(mission_id, drone_id, emergency=True)
                    return
            
            # Mission completed successfully
            self._complete_mission(mission_id, drone_id)
            
        except Exception as e:
            logger.error(f"Mission {mission_id}: Execution error: {e}")
            update_mission_status(mission_id, 'failed')
            self._safe_land(drone_id)
    
    def _complete_mission(self, mission_id: int, drone_id: int, 
                         failed: bool = False, emergency: bool = False):
        """Complete mission and return to home"""
        if emergency:
            status = 'emergency_stop'
        elif failed:
            status = 'failed'
        else:
            status = 'completed'
        
        update_mission_status(mission_id, status)
        
        # Return to launch and land
        logger.info(f"Mission {mission_id}: Returning to launch")
        self.drone_controller.return_to_launch(drone_id)
        time.sleep(10)  # Wait for RTL
        
        logger.info(f"Mission {mission_id}: Landing")
        self.drone_controller.land(drone_id)
        time.sleep(10)  # Wait for landing
        
        logger.info(f"Mission {mission_id}: Disarming")
        self.drone_controller.disarm_drone(drone_id)
        
        logger.info(f"Mission {mission_id}: {status}")
    
    def _safe_land(self, drone_id: int):
        """Safely land a drone"""
        try:
            self.drone_controller.land(drone_id)
            time.sleep(10)
            self.drone_controller.disarm_drone(drone_id)
        except Exception as e:
            logger.error(f"Error in safe landing for drone {drone_id}: {e}")
    
    def _check_emergency_stop(self, mission_id: int) -> bool:
        """Check if emergency stop is requested"""
        # In a real system, this would check for user commands or critical conflicts
        return False
    
    def cancel_mission(self, mission_id: int):
        """Cancel an active mission"""
        if mission_id in self.active_missions:
            # Signal thread to stop (in real system, use proper threading controls)
            logger.info(f"Cancelling mission {mission_id}")
            update_mission_status(mission_id, 'cancelled')
    
    def get_mission_status(self, mission_id: int) -> Optional[Dict]:
        """Get status of a mission"""
        # This would query the database
        return None
