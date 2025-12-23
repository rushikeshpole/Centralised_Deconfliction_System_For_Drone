"""
Core deconfliction engine for spatial-temporal conflict detection
"""
import numpy as np
from scipy.spatial import KDTree
from datetime import datetime, timedelta
import json
import logging
from typing import List, Dict, Tuple, Optional, Any
from database import get_drone_trajectory, get_active_missions, log_conflict, \
                    get_future_trajectories, store_future_trajectory, \
                    delete_future_trajectory, get_drone_current_position

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
        self.TIME_TOLERANCE = 0.5
        
    def compute_segment_speed(self, start_pos: List[float], end_pos: List[float], 
                            segment_time: float, prep_time: float = 3.0) -> float:
        """
        Compute required speed for a segment considering preparation time
        
        Args:
            start_pos: Starting position [x, y, z]
            end_pos: Ending position [x, y, z]
            segment_time: Time allocated for this segment (seconds)
            prep_time: Time needed for arming and takeoff (seconds)
            
        Returns:
            Required speed in m/s
        """
        distance = np.linalg.norm(np.array(end_pos) - np.array(start_pos))
        if segment_time <= prep_time:
            return 0  # Not enough time for movement
        
        # Subtract preparation time from available time
        effective_time = max(segment_time - prep_time, 0.1)
        return distance / effective_time
    
    def generate_4d_trajectory(self, drone_id: int, start_pos: List[float],
                             waypoints: List[List[float]], start_time: datetime,
                             end_time: datetime) -> List[Dict]:
        """
        Generate 4D trajectory (x, y, z, timestamp) for a drone mission
        
        Args:
            drone_id: ID of the drone
            start_pos: Current position of the drone [x, y, z]
            waypoints: List of waypoints [[x1,y1,z1], [x2,y2,z2], ...]
            start_time: Mission start time
            end_time: Mission end time
            
        Returns:
            List of 4D trajectory points with timestamps
        """
        if not waypoints:
            return []
        
        # Add start position to waypoints
        all_points = [start_pos] + waypoints
        
        # Calculate total mission duration
        mission_duration = (end_time - start_time).total_seconds()
        if mission_duration <= 0:
            logger.error(f"Invalid mission duration: start_time={start_time}, end_time={end_time}")
            return []
        
        # Calculate distances between consecutive points
        distances = []
        for i in range(len(all_points) - 1):
            p1 = np.array(all_points[i])
            p2 = np.array(all_points[i + 1])
            distance = np.linalg.norm(p2 - p1)
            distances.append(distance)
        
        total_distance = sum(distances)
        
        if total_distance == 0:
            # Drone stays in place
            return [{
                'drone_id': drone_id,
                'timestamp': start_time,
                'position': start_pos,
                'waypoint_index': 0
            }]
        
        # Allocate time to segments proportional to distance
        segment_times = []
        for i, distance in enumerate(distances):
            if i == 0:
                # First segment includes preparation time (arming, takeoff)
                prep_time = min(3.0, mission_duration * 0.1)  # 10% of mission or 3s max
                seg_time = (distance / total_distance) * mission_duration
                segment_times.append(seg_time)
            else:
                seg_time = (distance / total_distance) * mission_duration
                segment_times.append(seg_time)
        
        # Generate trajectory points
        trajectory = []
        current_time = start_time
        
        for segment_idx, (p1, p2, seg_time) in enumerate(zip(all_points[:-1], all_points[1:], segment_times)):
            p1_np = np.array(p1)
            p2_np = np.array(p2)
            
            # Number of interpolation steps for this segment
            steps = max(1, int(seg_time / self.time_resolution))
            
            for step in range(steps + 1):  # +1 to include endpoint
                t = step / steps if steps > 0 else 0.0
                position = p1_np + t * (p2_np - p1_np)
                
                # Calculate time offset for this point
                time_offset = seg_time * t
                point_time = current_time + timedelta(seconds=time_offset)
                
                trajectory.append({
                    'drone_id': drone_id,
                    'timestamp': point_time,
                    'position': position.tolist(),
                    'segment': segment_idx,
                    'waypoint_index': segment_idx if t == 0 else None,
                    'is_waypoint': (step == steps)
                })
            
            current_time += timedelta(seconds=seg_time)
        
        # Add final waypoint explicitly
        if trajectory and not trajectory[-1]['is_waypoint']:
            trajectory.append({
                'drone_id': drone_id,
                'timestamp': end_time,
                'position': all_points[-1],
                'segment': len(segment_times) - 1,
                'waypoint_index': len(waypoints),
                'is_waypoint': True
            })
        
        return trajectory
    
    # In the align_trajectories_by_time function, around line 194
    def align_trajectories_by_time(self, traj1, traj2):
        """Align two trajectories by time for comparison"""
        aligned_pairs = []
        
        if not traj1 or not traj2:
            return aligned_pairs
        
        for point1 in traj1:
            time1 = point1['timestamp']
            if isinstance(time1, str):
                try:
                    time1 = datetime.fromisoformat(time1.replace('Z', '+00:00'))
                except ValueError:
                    # Try other formats
                    try:
                        time1 = datetime.strptime(time1, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        # Skip this point if we can't parse the time
                        continue
            
            # Find closest point in traj2 within tolerance
            closest_point = None
            min_diff = float('inf')
            
            for point2 in traj2:
                time2 = point2['timestamp']
                if isinstance(time2, str):
                    try:
                        time2 = datetime.fromisoformat(time2.replace('Z', '+00:00'))
                    except ValueError:
                        try:
                            time2 = datetime.strptime(time2, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            continue
                
                time_diff = abs((time1 - time2).total_seconds())
                
                if time_diff < min_diff and time_diff <= self.TIME_TOLERANCE:
                    min_diff = time_diff
                    closest_point = point2
            
            if closest_point and min_diff <= self.TIME_TOLERANCE:
                aligned_pairs.append((point1, closest_point))
        
        return aligned_pairs

    def check_trajectory_conflicts(self, new_trajectory: List[Dict], 
                                existing_trajectories: Dict[int, List[Dict]]) -> List[Dict]:
        """
        Check new trajectory against existing trajectories
        
        Args:
            new_trajectory: New drone's trajectory
            existing_trajectories: Dict of {drone_id: trajectory} for existing drones
            
        Returns:
            List of conflict points
        """
        conflicts = []
        
        if not new_trajectory:
            return conflicts
        
        new_drone_id = new_trajectory[0]['drone_id'] if new_trajectory else 0
        
        for other_drone_id, other_trajectory in existing_trajectories.items():
            if not other_trajectory:
                continue
            
            # Align trajectories by time
            aligned_pairs = self.align_trajectories_by_time(new_trajectory, other_trajectory)
            
            for point1, point2 in aligned_pairs:
                pos1 = np.array(point1['position'])
                pos2 = np.array(point2['position'])
                
                distance = np.linalg.norm(pos1 - pos2)
                
                if distance < self.safety_buffer:
                    conflict_time = min(point1['timestamp'], point2['timestamp'])
                    
                    conflict = {
                        'time': conflict_time.isoformat(),
                        'drone1_id': new_drone_id,
                        'drone2_id': other_drone_id,
                        'position': pos1.tolist(),
                        'distance': float(distance),
                        'safety_buffer': self.safety_buffer,
                        'timestamp1': point1['timestamp'].isoformat(),
                        'timestamp2': point2['timestamp'].isoformat()
                    }
                    conflicts.append(conflict)
                    
                    # Log to database
                    log_conflict(new_drone_id, other_drone_id, 
                                distance, pos1.tolist(), conflict_time)
        
        return conflicts

    def check_mission_conflict(self, new_drone_id: int, waypoints: List[List[float]],
                             start_time: datetime, end_time: datetime) -> Dict:
        """
        Check if a new mission conflicts with existing trajectories
        
        Returns:
            Conflict analysis result
        """
        # Get current position from database
        current_pos = get_drone_current_position(new_drone_id)
        if not current_pos:
            # Default starting position if not available
            current_pos = [0, 0, 10]
        
        # Generate 4D trajectory for the new mission
        new_trajectory = self.generate_4d_trajectory(
            new_drone_id, current_pos, waypoints, start_time, end_time
        )
        
        if not new_trajectory:
            return {
                'safe': False,
                'error': 'Failed to generate trajectory',
                'conflict_count': 0,
                'suggestions': []
            }
        
        # Get existing trajectories in the same time window
        existing_trajectories = get_future_trajectories(start_time, end_time)
        
        # Remove any existing trajectory for this drone (in case of mission update)
        if new_drone_id in existing_trajectories:
            del existing_trajectories[new_drone_id]
        
        # Check for conflicts
        conflicts = self.check_trajectory_conflicts(new_trajectory, existing_trajectories)
        
        # Analyze conflict severity
        if conflicts:
            min_distance = min(c['distance'] for c in conflicts)
            conflict_times = [c['time'] for c in conflicts]
            conflicting_drones = list(set(c['drone2_id'] for c in conflicts))
            
            result = {
                'safe': False,
                'conflict_count': len(conflicts),
                'min_distance': min_distance,
                'conflict_times': conflict_times,
                'conflicting_drones': conflicting_drones,
                'conflicts': conflicts,
                'suggestions': self.generate_suggestions(conflicts, waypoints, start_time, end_time),
                'trajectory_generated': True,
                'trajectory_points': len(new_trajectory)
            }
        else:
            # Mission is safe - store the trajectory
            store_future_trajectory(new_drone_id, new_trajectory)
            
            result = {
                'safe': True,
                'conflict_count': 0,
                'suggestions': [],
                'trajectory_generated': True,
                'trajectory_points': len(new_trajectory),
                'mission_duration': (end_time - start_time).total_seconds(),
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat()
            }
        
        return result
    
    def generate_suggestions(self, conflicts: List[Dict], waypoints: List[List[float]],
                            start_time: datetime, end_time: datetime) -> List[Dict]:
        """Generate alternative suggestions to avoid conflicts"""
        suggestions = []
        
        if not conflicts:
            return suggestions
        
        # Find time range of conflicts
        conflict_times = [datetime.fromisoformat(c['time']) for c in conflicts]
        first_conflict = min(conflict_times)
        last_conflict = max(conflict_times)
        
        mission_duration = (end_time - start_time).total_seconds()
        
        # Suggestion 1: Time shift
        conflict_duration = (last_conflict - first_conflict).total_seconds()
        delay_needed = conflict_duration + 5.0  # Add buffer
        
        if delay_needed > 0:
            suggestions.append({
                'type': 'time_shift',
                'description': f'Delay mission start by {delay_needed:.1f} seconds',
                'new_start_time': (start_time + timedelta(seconds=delay_needed)).isoformat(),
                'new_end_time': (end_time + timedelta(seconds=delay_needed)).isoformat(),
                'priority': 1
            })
        
        # Suggestion 2: Altitude adjustment
        suggestions.append({
            'type': 'altitude_adjustment',
            'description': 'Increase altitude by 10 meters in conflict zone',
            'action': 'Add 10 meters to altitudes in conflict segments',
            'priority': 2
        })
        
        # Suggestion 3: Path deviation (if multiple waypoints)
        if len(waypoints) > 1:
            # Find the conflict segment
            conflict_positions = [c['position'] for c in conflicts]
            avg_conflict_pos = np.mean(conflict_positions, axis=0).tolist()
            
            suggestions.append({
                'type': 'path_deviation',
                'description': 'Add intermediate waypoint to avoid conflict zone',
                'action': f'Insert waypoint at {[round(x, 1) for x in avg_conflict_pos]}',
                'priority': 3
            })
        
        # Suggestion 4: Speed adjustment
        if mission_duration > 10:
            speed_increase = 1.2  # 20% faster
            new_duration = mission_duration / speed_increase
            
            suggestions.append({
                'type': 'speed_adjustment',
                'description': f'Increase speed by {int((speed_increase-1)*100)}%',
                'action': f'Reduce mission time from {mission_duration:.1f}s to {new_duration:.1f}s',
                'new_end_time': (start_time + timedelta(seconds=new_duration)).isoformat(),
                'priority': 4
            })
        
        # Sort by priority
        suggestions.sort(key=lambda x: x['priority'])
        
        return suggestions
    
    def simulate_mission(self, drone_id: int, waypoints: List[List[float]],
                        start_time: datetime, end_time: datetime) -> Dict:
        """
        Simulate a mission without checking conflicts (for testing)
        
        Returns:
            Trajectory and statistics
        """
        current_pos = get_drone_current_position(drone_id)
        if not current_pos:
            current_pos = [0, 0, 10]
        
        trajectory = self.generate_4d_trajectory(
            drone_id, current_pos, waypoints, start_time, end_time
        )
        
        if not trajectory:
            return {'error': 'Failed to generate trajectory'}
        
        # Calculate statistics
        total_distance = 0
        for i in range(len(trajectory) - 1):
            p1 = np.array(trajectory[i]['position'])
            p2 = np.array(trajectory[i + 1]['position'])
            total_distance += np.linalg.norm(p2 - p1)
        
        mission_duration = (end_time - start_time).total_seconds()
        avg_speed = total_distance / mission_duration if mission_duration > 0 else 0
        
        return {
            'trajectory': trajectory,
            'statistics': {
                'total_distance': total_distance,
                'mission_duration': mission_duration,
                'avg_speed': avg_speed,
                'num_points': len(trajectory),
                'num_segments': max(p['segment'] for p in trajectory) + 1 if trajectory else 0
            }
        }
    
    def cleanup_old_trajectories(self, cutoff_time: datetime = None):
        """Remove trajectories that are in the past"""
        if cutoff_time is None:
            cutoff_time = datetime.now() - timedelta(minutes=5)
        
        # This would call a database function to delete old trajectories
        # For now, we'll just log
        logger.info(f"Cleaning up trajectories older than {cutoff_time}")
    
    def realtime_conflict_monitor(self):
        """Monitor for conflicts in real-time (background task)"""
        while True:
            try:
                # Get all active trajectories
                current_time = datetime.now()
                future_time = current_time + timedelta(seconds=self.lookahead_time)
                
                # Get trajectories in the lookahead window
                trajectories = get_future_trajectories(current_time, future_time)
                
                # Check all pairwise combinations
                drone_ids = list(trajectories.keys())
                for i in range(len(drone_ids)):
                    for j in range(i + 1, len(drone_ids)):
                        drone1_id = drone_ids[i]
                        drone2_id = drone_ids[j]
                        
                        conflicts = self.check_trajectory_conflicts(
                            trajectories[drone1_id],
                            {drone2_id: trajectories[drone2_id]}
                        )
                        
                        if conflicts:
                            logger.warning(f"Real-time conflict detected: "
                                         f"Drone {drone1_id} vs Drone {drone2_id}")
                
                # Sleep for time resolution
                import time
                time.sleep(self.time_resolution)
                
            except Exception as e:
                logger.error(f"Error in conflict monitor: {e}")
                import time
                time.sleep(1.0)