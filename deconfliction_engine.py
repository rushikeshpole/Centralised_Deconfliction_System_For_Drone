"""
Core deconfliction engine for spatial-temporal conflict detection
"""
import numpy as np
from scipy.spatial import KDTree
from datetime import datetime, timedelta
import json
import logging
from typing import List, Dict, Tuple, Optional
from database import get_drone_trajectory, get_active_missions, log_conflict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DeconflictionEngine:
    def __init__(self, safety_buffer=5.0, time_resolution=0.1, lookahead_time=30.0):
        """
        Initialize deconfliction engine
        
        Args:
            safety_buffer: Minimum safe distance between drones (meters)
            time_resolution: Time step for trajectory interpolation (seconds)
            lookahead_time: How far ahead to check for conflicts (seconds)
        """
        self.safety_buffer = safety_buffer
        self.time_resolution = time_resolution
        self.lookahead_time = lookahead_time
        
    def interpolate_waypoints(self, start_pos: List[float], 
                             waypoints: List[List[float]], 
                             total_time: float) -> List[Dict]:
        """
        Convert waypoints to time-stamped trajectory
        
        Args:
            start_pos: Starting position [x, y, z]
            waypoints: List of waypoints [[x1,y1,z1], [x2,y2,z2], ...]
            total_time: Total mission time in seconds
            
        Returns:
            List of interpolated points with timestamps
        """
        if not waypoints:
            return []
        
        # Add start position to waypoints
        all_points = [start_pos] + waypoints
        
        # Calculate distances between consecutive points
        distances = []
        for i in range(len(all_points) - 1):
            p1 = np.array(all_points[i])
            p2 = np.array(all_points[i + 1])
            distance = np.linalg.norm(p2 - p1)
            distances.append(distance)
        
        total_distance = sum(distances)
        
        if total_distance == 0:
            return []
        
        # Calculate time allocation for each segment
        segment_times = [(d / total_distance) * total_time for d in distances]
        
        # Interpolate along each segment
        trajectory = []
        current_time = 0.0
        
        for i, (p1, p2, seg_time) in enumerate(zip(all_points[:-1], all_points[1:], segment_times)):
            p1_np = np.array(p1)
            p2_np = np.array(p2)
            
            # Number of interpolation steps for this segment
            steps = max(1, int(seg_time / self.time_resolution))
            
            for step in range(steps):
                t = step / steps if steps > 1 else 0.0
                position = p1_np + t * (p2_np - p1_np)
                
                trajectory.append({
                    'time_offset': current_time + t * seg_time,
                    'position': position.tolist(),
                    'segment': i,
                    'is_waypoint': (step == steps - 1)  # Last point in segment is waypoint
                })
            
            current_time += seg_time
        
        # Add final waypoint
        trajectory.append({
            'time_offset': current_time,
            'position': all_points[-1],
            'segment': len(segment_times) - 1,
            'is_waypoint': True
        })
        
        return trajectory
    
    def predict_trajectory(self, drone_id: int, current_pos: List[float],
                          current_time: datetime) -> List[Dict]:
        """
        Predict future trajectory based on current state and planned missions
        
        Args:
            drone_id: ID of the drone
            current_pos: Current position [x, y, z]
            current_time: Current timestamp
            
        Returns:
            Predicted trajectory for lookahead period
        """
        # Get planned missions for this drone
        missions = get_active_missions()
        drone_missions = [m for m in missions if m['drone_id'] == drone_id]
        
        if not drone_missions:
            # No mission planned, assume drone will stay in place
            return [{
                'time': current_time + timedelta(seconds=t),
                'position': current_pos,
                'drone_id': drone_id
            } for t in np.arange(0, self.lookahead_time, self.time_resolution)]
        
        # Use the next mission to predict trajectory
        next_mission = min(drone_missions, key=lambda m: m['start_time'])
        
        if next_mission['start_time'] > current_time + timedelta(seconds=self.lookahead_time):
            # Mission starts beyond lookahead, stay in place
            return [{
                'time': current_time + timedelta(seconds=t),
                'position': current_pos,
                'drone_id': drone_id
            } for t in np.arange(0, self.lookahead_time, self.time_resolution)]
        
        # Mission within lookahead, predict based on mission plan
        waypoints = json.loads(next_mission['waypoints'])
        start_time = datetime.fromisoformat(next_mission['start_time'])
        end_time = datetime.fromisoformat(next_mission['end_time'])
        
        # Calculate trajectory for mission duration
        mission_duration = (end_time - start_time).total_seconds()
        trajectory = self.interpolate_waypoints(current_pos, waypoints, mission_duration)
        
        predicted = []
        for point in trajectory:
            if point['time_offset'] <= self.lookahead_time:
                predicted.append({
                    'time': current_time + timedelta(seconds=point['time_offset']),
                    'position': point['position'],
                    'drone_id': drone_id
                })
        
        return predicted
    
    def check_pairwise_conflicts(self, drone1_id: int, drone1_traj: List[Dict],
                                drone2_id: int, drone2_traj: List[Dict]) -> List[Dict]:
        """
        Check for conflicts between two drone trajectories
        
        Returns:
            List of conflict points with details
        """
        conflicts = []
        
        if not drone1_traj or not drone2_traj:
            return conflicts
        
        # Align trajectories by time
        time_dict1 = {p['time']: p['position'] for p in drone1_traj}
        time_dict2 = {p['time']: p['position'] for p in drone2_traj}
        
        common_times = set(time_dict1.keys()) & set(time_dict2.keys())
        
        for time_point in sorted(common_times):
            pos1 = np.array(time_dict1[time_point])
            pos2 = np.array(time_dict2[time_point])
            
            distance = np.linalg.norm(pos1 - pos2)
            
            if distance < self.safety_buffer:
                conflict = {
                    'time': time_point.isoformat(),
                    'drone1_id': drone1_id,
                    'drone2_id': drone2_id,
                    'position': pos1.tolist(),
                    'distance': float(distance),
                    'safety_buffer': self.safety_buffer
                }
                conflicts.append(conflict)
                
                # Log to database
                log_conflict(drone1_id, drone2_id, distance, pos1.tolist())
        
        return conflicts
    
    def check_mission_conflict(self, new_drone_id: int, waypoints: List[List[float]],
                              start_time: datetime, end_time: datetime) -> Dict:
        """
        Check if a new mission conflicts with existing trajectories
        
        Args:
            new_drone_id: ID of the drone requesting mission
            waypoints: Planned waypoints
            start_time: Mission start time
            end_time: Mission end time
            
        Returns:
            Conflict analysis result
        """
        # Get current position (in real system, this would come from drone)
        current_pos = [0, 0, 10]  # Default starting position
        
        # Generate proposed trajectory
        mission_duration = (end_time - start_time).total_seconds()
        proposed_trajectory = self.interpolate_waypoints(current_pos, waypoints, mission_duration)
        
        # Add timestamps to proposed trajectory
        timed_trajectory = []
        for point in proposed_trajectory:
            timed_trajectory.append({
                'time': start_time + timedelta(seconds=point['time_offset']),
                'position': point['position'],
                'drone_id': new_drone_id
            })
        
        # Get trajectories of other drones in the same time window
        all_conflicts = []
        drone_conflicts = {}
        
        # Check against all other drones
        for other_drone_id in range(1, 5):  # Assuming 4 drones
            if other_drone_id == new_drone_id:
                continue
            
            # Get other drone's predicted trajectory
            other_current_pos = [other_drone_id * 10, 0, 10]  # Simplified
            other_trajectory = self.predict_trajectory(
                other_drone_id, other_current_pos, start_time
            )
            
            # Check for conflicts
            conflicts = self.check_pairwise_conflicts(
                new_drone_id, timed_trajectory,
                other_drone_id, other_trajectory
            )
            
            if conflicts:
                all_conflicts.extend(conflicts)
                drone_conflicts[other_drone_id] = conflicts
        
        # Analyze conflict severity
        if all_conflicts:
            min_distance = min(c['distance'] for c in all_conflicts)
            conflict_times = [c['time'] for c in all_conflicts]
            
            result = {
                'safe': False,
                'conflict_count': len(all_conflicts),
                'min_distance': min_distance,
                'conflict_times': conflict_times,
                'conflicting_drones': list(drone_conflicts.keys()),
                'conflicts': all_conflicts,
                'suggestions': self.generate_suggestions(all_conflicts, waypoints, start_time, end_time)
            }
        else:
            result = {
                'safe': True,
                'conflict_count': 0,
                'suggestions': []
            }
        
        return result
    
    def generate_suggestions(self, conflicts: List[Dict], waypoints: List[List[float]],
                            start_time: datetime, end_time: datetime) -> List[Dict]:
        """Generate alternative suggestions to avoid conflicts"""
        suggestions = []
        
        if not conflicts:
            return suggestions
        
        # Find the time of first conflict
        conflict_times = [datetime.fromisoformat(c['time']) for c in conflicts]
        first_conflict = min(conflict_times)
        last_conflict = max(conflict_times)
        
        # Suggestion 1: Delay start time
        delay_time = (last_conflict - start_time).total_seconds() + 5.0  # Add 5 second buffer
        if delay_time > 0:
            suggestions.append({
                'type': 'time_shift',
                'description': f'Delay mission start by {delay_time:.1f} seconds',
                'new_start_time': (start_time + timedelta(seconds=delay_time)).isoformat(),
                'new_end_time': (end_time + timedelta(seconds=delay_time)).isoformat()
            })
        
        # Suggestion 2: Altitude change
        suggestions.append({
            'type': 'altitude_adjustment',
            'description': 'Increase altitude by 5-10 meters',
            'action': 'Add 10 meters to all waypoint altitudes'
        })
        
        # Suggestion 3: Path deviation
        if len(waypoints) > 1:
            suggestions.append({
                'type': 'path_deviation',
                'description': 'Add intermediate waypoint to avoid conflict zone',
                'action': 'Insert additional waypoint at midpoint'
            })
        
        # Suggestion 4: Speed adjustment
        mission_duration = (end_time - start_time).total_seconds()
        if mission_duration > 10:  # Only suggest if mission is long enough
            suggestions.append({
                'type': 'speed_adjustment',
                'description': 'Increase speed to pass through conflict zone quickly',
                'action': f'Reduce mission time by {mission_duration * 0.2:.1f} seconds'
            })
        
        return suggestions
    
    def realtime_conflict_monitor(self):
        """Monitor for conflicts in real-time (background task)"""
        # This would run in a separate thread
        while True:
            try:
                # Get current positions of all drones
                # Check pairwise distances
                # If conflict detected, trigger alerts
                
                # For now, just sleep
                import time
                time.sleep(1.0)
                
            except Exception as e:
                logger.error(f"Error in conflict monitor: {e}")

