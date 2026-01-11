"""
Heating Services - Home Assistant Pyscript Plugin
==================================================
Provides performance analysis, PID tuning recommendations, health monitoring,
and energy cost tracking for floor heating system.

Sensors created:
- sensor.heating_<zone>_power_m2 - Current power demand (W/m²)
- sensor.heating_<zone>_cycle_time - Average cycle duration (minutes)
- sensor.heating_<zone>_duty_cycle - Heating duty cycle (%)
- sensor.heating_<zone>_current_kp/ki/kd - Current PID from configuration.yaml
- sensor.heating_<zone>_recommended_kp/ki/kd - Calculated recommendations (adaptive)
- sensor.heating_<zone>_overshoot - Learned average overshoot (°C)
- sensor.heating_<zone>_settling_time - Learned settling time (minutes)
- sensor.heating_<zone>_oscillations - Learned oscillation count
- sensor.heating_total_power_m2 - System average W/m²
- sensor.heating_total_cost - Energy cost based on GJ price
- sensor.heating_system_health - Overall health status

Services:
- pyscript.heating_weekly_report - Generate and send weekly report
- pyscript.heating_health_check - Run health check now
- pyscript.heating_pid_recommendations - Calculate PID tuning suggestions
- pyscript.heating_cost_report - Generate cost breakdown
- pyscript.heating_run_learning - Manually trigger adaptive learning
- pyscript.heating_apply_weekly_pid - Apply recommended PID to zones in heat mode

Adaptive Learning:
- Analyzes 7 days of temperature history per zone
- Detects overshoot, settling time, and oscillations
- Adjusts PID recommendations based on observed behavior
- Runs daily at 3:00 AM and stores learned data persistently
"""

import yaml
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

# History access imports - requires allow_all_imports: true in pyscript config
from homeassistant.components.recorder import get_instance, history
from homeassistant.util import dt as dt_util

# =============================================================================
# CONFIGURATION PATHS
# =============================================================================
PYSCRIPT_CONFIG_DIR = Path("/config/pyscript/config")
HA_CONFIG_FILE = Path("/config/configuration.yaml")
LEARNING_DATA_FILE = PYSCRIPT_CONFIG_DIR / "learning_data.json"

# =============================================================================
# FILE I/O HELPER FUNCTIONS
# =============================================================================
# In pyscript, ALL top-level functions are wrapped as async pyscript functions.
# This means we cannot use task.executor() with them - they're not native Python.
#
# Solution: Use @pyscript_executor decorator which:
# 1. Compiles the function as native Python (not wrapped by pyscript)
# 2. Automatically wraps calls with task.executor (runs in thread pool)
#
# This allows blocking I/O (file read/write) without blocking the event loop.
# =============================================================================

@pyscript_executor
def _read_yaml_file(path, handle_includes=False):
    """
    Read YAML file. Runs in executor thread automatically.

    Args:
        path: Path to YAML file
        handle_includes: If True, handle !include and other HA-specific tags
                        by returning placeholder values instead of crashing
    """
    if handle_includes:
        # Create a custom loader that handles HA-specific YAML tags
        class HAYamlLoader(yaml.SafeLoader):
            pass

        # Handler that returns None for !include directives
        def include_constructor(loader, node):
            return None

        # Handler that returns the raw value for !secret
        def secret_constructor(loader, node):
            return f"!secret {node.value}"

        # Register handlers for HA-specific tags
        HAYamlLoader.add_constructor('!include', include_constructor)
        HAYamlLoader.add_constructor('!include_dir_list', include_constructor)
        HAYamlLoader.add_constructor('!include_dir_merge_list', include_constructor)
        HAYamlLoader.add_constructor('!include_dir_named', include_constructor)
        HAYamlLoader.add_constructor('!include_dir_merge_named', include_constructor)
        HAYamlLoader.add_constructor('!secret', secret_constructor)
        HAYamlLoader.add_constructor('!env_var', include_constructor)

        with open(str(path), 'r', encoding='utf-8') as f:
            return yaml.load(f, Loader=HAYamlLoader)
    else:
        with open(str(path), 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

@pyscript_executor
def _read_json_file(path):
    """Read JSON file. Runs in executor thread automatically."""
    with open(str(path), 'r', encoding='utf-8') as f:
        return json.load(f)

@pyscript_executor
def _write_json_file(path, data):
    """Write JSON file. Runs in executor thread automatically."""
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)

@pyscript_executor
def _file_exists(path):
    """Check if file exists. Runs in executor thread automatically."""
    return os.path.exists(str(path))

async def _fetch_state_history(hass_obj, entity_id, start_time, end_time):
    """
    Fetch state history from recorder database.

    Args:
        hass_obj: Home Assistant instance (pass hass from pyscript)
        entity_id: Entity to query (e.g., 'switch.gf_heating')
        start_time: datetime object
        end_time: datetime object

    Returns:
        List of state objects with .state, .last_changed, .attributes
    """
    try:
        # Ensure timezone-aware datetimes
        if start_time.tzinfo is None:
            start_time = dt_util.as_utc(start_time)
        if end_time.tzinfo is None:
            end_time = dt_util.as_utc(end_time)

        log.debug(f"Fetching history for {entity_id} from {start_time} to {end_time}")

        # Get recorder instance
        instance = get_instance(hass_obj)

        # Use the recorder's async_add_executor_job for proper DB access
        states_dict = await instance.async_add_executor_job(
            history.get_significant_states,
            hass_obj,
            start_time,
            end_time,
            [entity_id],  # entity_ids
        )

        result = states_dict.get(entity_id, [])
        log.info(f"History query for {entity_id}: got {len(result)} states")
        return result

    except Exception as e:
        log.error(f"Recorder history fetch failed for {entity_id}: {e}")
        import traceback
        log.error(traceback.format_exc())
        return []

# =============================================================================
# LEARNING DATA PERSISTENCE
# =============================================================================

def load_learning_data():
    """Load learned performance data from persistent storage."""
    try:
        # @pyscript_executor functions are called directly (no task.executor needed)
        if _file_exists(LEARNING_DATA_FILE):
            return _read_json_file(LEARNING_DATA_FILE)
    except Exception as e:
        log.error(f"Failed to load learning data: {e}")
    return {}

def save_learning_data(data):
    """Save learned performance data to persistent storage."""
    try:
        # @pyscript_executor functions are called directly (no task.executor needed)
        _write_json_file(LEARNING_DATA_FILE, data)
        log.info("Learning data saved")
    except Exception as e:
        log.error(f"Failed to save learning data: {e}")

# =============================================================================
# CONFIGURATION LOADERS
# =============================================================================

def load_zones_config():
    """Load zone configuration from zones.yaml."""
    # @pyscript_executor functions are called directly (no task.executor needed)
    return _read_yaml_file(PYSCRIPT_CONFIG_DIR / "zones.yaml")

def load_constants_config():
    """Load system constants from constants.yaml."""
    # @pyscript_executor functions are called directly (no task.executor needed)
    return _read_yaml_file(PYSCRIPT_CONFIG_DIR / "constants.yaml")

def load_current_pid_values():
    """
    Parse /config/configuration.yaml to extract HASmartThermostat PID settings.
    Returns dict mapping zone_id -> {kp, ki, kd, ke, pwm, min_cycle}.
    """
    try:
        # @pyscript_executor functions are called directly (no task.executor needed)
        # Use handle_includes=True to handle HA-specific YAML tags like !include
        config = _read_yaml_file(HA_CONFIG_FILE, handle_includes=True)
    except Exception as e:
        log.error(f"Failed to read configuration.yaml: {e}")
        return {}

    pid_values = {}

    # HASmartThermostat uses 'climate' with platform 'smart_thermostat'
    climate_entries = config.get('climate', [])
    if not isinstance(climate_entries, list):
        climate_entries = [climate_entries] if climate_entries else []

    # Zone name mapping: unique_id -> zone IDs from zones.yaml
    # unique_id examples: thermostat_gf, thermostat_1st_kitchen, thermostat_2nd_bedroom
    unique_id_to_zone = {
        'thermostat_gf': 'gf',
        'thermostat_1st_kitchen': 'kitchen',
        'thermostat_1st_living_room': 'living_room',
        'thermostat_2nd_bedroom': 'bedroom',
        'thermostat_2nd_bathroom': 'bathroom',
        'thermostat_2nd_study': 'study',
        'thermostat_2nd_hallway': 'hallway',
    }

    for climate in climate_entries:
        if not isinstance(climate, dict):
            continue
        if climate.get('platform') != 'smart_thermostat':
            continue

        # Get unique_id and map to zone ID
        unique_id = climate.get('unique_id', '')
        zone_id = unique_id_to_zone.get(unique_id)

        if not zone_id:
            name = climate.get('name', '')
            log.warning(f"Unknown zone in configuration.yaml: {name} (unique_id: {unique_id})")
            continue

        # Get PWM period - can be string or dict
        pwm = climate.get('pwm', '01:30:00')
        if isinstance(pwm, dict):
            # Convert dict like {minutes: 90} to string
            hours = pwm.get('hours', 0)
            minutes = pwm.get('minutes', 0)
            seconds = pwm.get('seconds', 0)
            pwm = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        # Get min_cycle_duration - can be string or dict
        min_cycle = climate.get('min_cycle_duration', '00:15:00')
        if isinstance(min_cycle, dict):
            hours = min_cycle.get('hours', 0)
            minutes = min_cycle.get('minutes', 0)
            seconds = min_cycle.get('seconds', 0)
            min_cycle = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        pid_values[zone_id] = {
            'kp': climate.get('kp', 0.5),
            'ki': climate.get('ki', 0.01),
            'kd': climate.get('kd', 5),
            'ke': climate.get('ke', 0),
            'pwm_period': pwm,
            'min_cycle_duration': min_cycle,
            'hot_tolerance': climate.get('hot_tolerance', 0.3),
            'cold_tolerance': climate.get('cold_tolerance', 0.3),
        }

    return pid_values

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def is_zone_in_heat_mode(zone_config):
    """
    Check if a zone's climate entity is in heat mode.

    Returns True only if the climate is in 'heat' mode.
    Returns False for 'cool', 'off', 'auto', or unavailable states.

    This is important for PID recommendations - cooling water temperature
    is different from heating, which would skew heating PID calculations.
    """
    climate_entity = zone_config.get('climate_entity')
    if not climate_entity:
        return False

    try:
        hvac_mode = state.get(climate_entity)
        return hvac_mode == 'heat'
    except (NameError, AttributeError):
        # Entity not available
        return False

# =============================================================================
# THERMAL CALCULATIONS
# =============================================================================

def calculate_thermal_time_constant(zone_config, constants):
    """
    Estimate thermal time constant (tau) for a zone.
    tau = thermal_mass / heat_loss_rate
    """
    volume = zone_config['volume_m3']
    cool_rate = zone_config['cool_rate_c_per_hour']
    area = zone_config['area_m2']

    # Material properties
    rho_air = constants['materials']['rho_air']
    cp_air = constants['materials']['cp_air']

    # Estimate tau in hours
    # Simplified: tau ≈ 1 / cool_rate (time to cool by 1/e)
    if cool_rate > 0:
        tau_hours = 1 / cool_rate
    else:
        tau_hours = 20  # Default

    return tau_hours

def calculate_recommended_pid(zone_id, zone_config, constants):
    """
    Calculate recommended PID parameters for a zone based on thermal properties.
    Uses modified Ziegler-Nichols approach constrained for A+++ houses.
    """
    pid_limits = constants['pid_tuning']
    tau = calculate_thermal_time_constant(zone_config, constants)
    cool_rate = zone_config['cool_rate_c_per_hour']

    # Base calculations
    kp_min, kp_max = pid_limits['kp_min'], pid_limits['kp_max']
    ki_min, ki_max = pid_limits['ki_min'], pid_limits['ki_max']

    # Kp: Scale based on cool_rate (faster cooling = higher Kp)
    # Normalize cool_rate: typical range 0.05-0.08
    kp_factor = (cool_rate - 0.05) / 0.03  # 0 to 1 for typical range
    kp_factor = max(0, min(1, kp_factor))
    recommended_kp = kp_min + (kp_max - kp_min) * kp_factor

    # Ki: Scale based on thermal time constant (slower = lower Ki)
    # Slower systems need less integral to avoid overshoot
    ki_factor = 1 - ((tau - 15) / 10)  # Higher tau = lower factor
    ki_factor = max(0, min(1, ki_factor))
    recommended_ki = ki_min + (ki_max - ki_min) * ki_factor

    # Kd: Based on thermal time constant
    if tau > 20:
        recommended_kd = pid_limits['kd_slow']
    elif tau > 15:
        recommended_kd = pid_limits['kd_medium']
    else:
        recommended_kd = pid_limits['kd_fast']

    # Zone-specific adjustments based on notes
    notes = zone_config.get('notes', '').lower()

    # Disturbance zones: lower Ki to avoid overshoot from transients
    if 'oven' in notes or 'door' in notes or 'terrace' in notes:
        recommended_ki *= 0.8
        reason = "High disturbance zone - reduced Ki to prevent overshoot from door/oven events"
    elif 'skylight' in notes:
        recommended_kp = min(recommended_kp * 1.2, kp_max)
        reason = "Skylight zone - increased Kp for faster response to heat loss"
    elif 'window open' in notes or 'ventilation' in notes:
        recommended_ki *= 0.9
        reason = "Ventilation zone - slightly reduced Ki to avoid integral wind-up"
    elif 'open space' in notes or 'thermally influenced' in notes:
        reason = "Open space zone - tuned to match connected zone"
    else:
        reason = "Standard zone - baseline tuning"

    return {
        'kp': round(recommended_kp, 3),
        'ki': round(recommended_ki, 4),
        'kd': round(recommended_kd, 1),
        'tau_hours': round(tau, 1),
        'reason': reason
    }

# =============================================================================
# HISTORY ANALYSIS
# =============================================================================

async def get_heater_history(entity_id, hours=168):
    """
    Get heater switch state history for cycle analysis.

    Queries Home Assistant recorder database. Requires recorder
    integration enabled and allow_all_imports: true in pyscript config.

    Args:
        entity_id: Heater switch (e.g., 'switch.gf_heating')
        hours: Hours of history (default 168 = 7 days)

    Returns:
        List of State objects with .state ('on'/'off'), .last_changed
    """
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        state_list = await _fetch_state_history(hass, entity_id, start_time, end_time)

        if not state_list:
            log.warning(
                f"No heater history found for {entity_id} "
                f"({hours}h window). Check recorder configuration."
            )
            return []

        log.debug(f"Retrieved {len(state_list)} heater states for {entity_id}")
        return state_list

    except Exception as e:
        log.error(f"Failed to get heater history for {entity_id}: {e}")
        return []

async def analyze_cycles(zone_id, zone_config, hours=168):
    """
    Analyze heating cycles for a zone.
    Returns cycle times, duty cycle, and power estimate.
    """
    heater_entity = zone_config['heater_switch']
    history = await get_heater_history(heater_entity, hours)

    if not history:
        return None

    on_periods = []
    total_on_time = timedelta()
    total_time = timedelta(hours=hours)

    last_state = None
    last_time = None

    for entry in history:
        state_val = entry.state
        timestamp = entry.last_changed

        if last_state == 'on' and state_val == 'off':
            # End of ON period
            if last_time:
                duration = timestamp - last_time
                on_periods.append(duration)
                total_on_time += duration

        last_state = state_val
        last_time = timestamp

    if not on_periods:
        return {
            'avg_cycle_minutes': 0,
            'duty_cycle_percent': 0,
            'power_w_m2': 0,
            'cycle_count': 0
        }

    total_seconds = sum([p.total_seconds() for p in on_periods])
    avg_cycle = total_seconds / len(on_periods) / 60
    duty_cycle = (total_on_time.total_seconds() / total_time.total_seconds()) * 100

    # Estimate power: duty_cycle * typical_power / area
    # Assuming 50W/m2 at 100% duty cycle (typical floor heating)
    area = zone_config['area_m2']
    power_w_m2 = (duty_cycle / 100) * 50

    return {
        'avg_cycle_minutes': round(avg_cycle, 1),
        'duty_cycle_percent': round(duty_cycle, 1),
        'power_w_m2': round(power_w_m2, 1),
        'cycle_count': len(on_periods)
    }

# =============================================================================
# ADAPTIVE LEARNING - TEMPERATURE RESPONSE ANALYSIS
# =============================================================================

async def get_temperature_history(entity_id, hours=168):
    """
    Get temperature sensor history for response analysis.

    Queries Home Assistant recorder database. Filters out invalid readings.

    Args:
        entity_id: Temperature sensor (e.g., 'sensor.gf_temperature')
        hours: Hours of history (default 168 = 7 days)

    Returns:
        List of State objects with numeric .state, .last_changed
    """
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        state_list = await _fetch_state_history(hass, entity_id, start_time, end_time)

        if not state_list:
            log.warning(
                f"No temperature history found for {entity_id} "
                f"({hours}h window). Check recorder configuration."
            )
            return []

        # Filter invalid states (unavailable, unknown, None)
        valid_states = []
        for state_obj in state_list:
            try:
                float(state_obj.state)
                valid_states.append(state_obj)
            except (ValueError, TypeError, AttributeError):
                continue

        if len(valid_states) < len(state_list):
            log.debug(
                f"Filtered {len(state_list) - len(valid_states)} invalid "
                f"temperature states for {entity_id}"
            )

        log.debug(f"Retrieved {len(valid_states)} temperature states for {entity_id}")
        return valid_states

    except Exception as e:
        log.error(f"Failed to get temperature history for {entity_id}: {e}")
        return []

async def get_setpoint_history(climate_entity, hours=168):
    """
    Get historical setpoint values for a climate entity.

    Queries Home Assistant recorder for the climate entity's temperature attribute.
    Returns list of (timestamp, setpoint) tuples sorted chronologically.
    """
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        state_list = await _fetch_state_history(hass, climate_entity, start_time, end_time)

        if not state_list:
            log.debug(f"No setpoint history found for {climate_entity}")
            return []

        setpoints = []
        last_setpoint = None

        for state_obj in state_list:
            try:
                # Get temperature attribute (setpoint) from climate entity
                attrs = getattr(state_obj, 'attributes', {})
                if attrs:
                    sp = attrs.get('temperature')
                    if sp is not None:
                        sp = float(sp)
                        # Only record when setpoint changes
                        if sp != last_setpoint:
                            setpoints.append((state_obj.last_changed, sp))
                            last_setpoint = sp
            except (ValueError, TypeError, AttributeError):
                continue

        log.debug(f"Retrieved {len(setpoints)} setpoint changes for {climate_entity}")
        return setpoints

    except Exception as e:
        log.debug(f"Could not get setpoint history for {climate_entity}: {e}")
        return []


async def detect_setpoint_changes(climate_entity, hours=168):
    """
    Analyze if setpoint has varied over the analysis period.

    Returns:
        dict with:
        - has_changes: bool - True if setpoint varied
        - setpoints: list of (datetime, setpoint) tuples
        - min_setpoint: float (if has_changes)
        - max_setpoint: float (if has_changes)
        - setpoint_range: float (if has_changes)
    """
    setpoints = await get_setpoint_history(climate_entity, hours)

    if len(setpoints) <= 1:
        return {
            'has_changes': False,
            'setpoints': setpoints,
            'history_available': len(setpoints) > 0,
            'constant_setpoint': setpoints[0][1] if setpoints else None
        }

    temps = [sp[1] for sp in setpoints]
    return {
        'has_changes': True,
        'setpoints': setpoints,
        'history_available': True,
        'min_setpoint': min(temps),
        'max_setpoint': max(temps),
        'setpoint_range': max(temps) - min(temps)
    }


def get_setpoint_at_time(setpoints, target_time):
    """
    Find the setpoint that was active at a specific time.

    Args:
        setpoints: List of (datetime, setpoint) tuples from get_setpoint_history()
        target_time: datetime to look up

    Returns:
        float: Setpoint at that time, or None if unavailable
    """
    if not setpoints:
        return None

    # Find the last setpoint change before target_time
    active_setpoint = None
    for change_time, setpoint in setpoints:
        if change_time <= target_time:
            active_setpoint = setpoint
        else:
            break

    return active_setpoint


async def analyze_heating_response(zone_id, zone_config, current_pid, hours=168):
    """
    Analyze temperature response to heating events for a zone.

    Returns metrics:
    - overshoot: Average overshoot above setpoint (°C)
    - undershoot: Average undershoot below setpoint (°C)
    - settling_time: Average time to stabilize after heating (minutes)
    - oscillation_count: Average number of setpoint crossings before settling
    - rise_time: Average time from heating start to reaching setpoint (minutes)
    - response_events: Number of heating response events analyzed
    """
    temp_sensor = zone_config['temperature_sensor']
    heater_switch = zone_config['heater_switch']
    climate_entity = zone_config['climate_entity']

    # Get history data
    temp_history = await get_temperature_history(temp_sensor, hours)
    heater_history = await get_heater_history(heater_switch, hours)

    if not temp_history or not heater_history:
        return None

    # Convert history to time-indexed data
    temp_data = []
    for entry in temp_history:
        try:
            temp = float(entry.state)
            temp_data.append({
                'time': entry.last_changed,
                'temp': temp
            })
        except (ValueError, TypeError):
            continue

    if not temp_data:
        return None

    # Find heating ON->OFF transitions (end of heating cycles)
    heating_events = []
    last_state = None
    last_on_time = None

    for entry in heater_history:
        state_val = entry.state
        timestamp = entry.last_changed

        if last_state == 'off' and state_val == 'on':
            last_on_time = timestamp
        elif last_state == 'on' and state_val == 'off' and last_on_time:
            heating_events.append({
                'start': last_on_time,
                'end': timestamp
            })

        last_state = state_val

    if not heating_events:
        return None

    # Detect setpoint changes over the analysis period
    setpoint_info = await detect_setpoint_changes(climate_entity, hours)
    use_dynamic_setpoint = setpoint_info.get('has_changes', False)
    setpoint_history = setpoint_info.get('setpoints', [])

    if use_dynamic_setpoint:
        log.info(f"Zone {zone_id}: Detected setpoint changes "
                f"({setpoint_info['min_setpoint']:.1f}-{setpoint_info['max_setpoint']:.1f}°C, "
                f"{len(setpoint_history)} changes)")

    # Fallback: Get current setpoint if no history available
    try:
        current_setpoint = float(state.get(f"{climate_entity}.temperature") or
                        state.getattr(climate_entity).get('temperature', 21))
    except:
        current_setpoint = 21.0

    # Get tolerance from current PID config
    hot_tolerance = current_pid.get('hot_tolerance', 0.3)
    cold_tolerance = current_pid.get('cold_tolerance', 0.3)

    # Load adaptive learning config for buffer hours
    try:
        constants = load_constants_config()
        adaptive_config = constants.get('adaptive_learning', {})
        setpoint_change_buffer_hours = adaptive_config.get('setpoint_change_buffer_hours', 2)
    except:
        setpoint_change_buffer_hours = 2

    # Analyze each heating event
    overshoots = []
    undershoots = []
    settling_times = []
    oscillation_counts = []
    rise_times = []
    excluded_events = 0

    for event in heating_events[-20:]:  # Analyze last 20 events max
        event_start = event['start']
        event_end = event['end']
        analysis_window_end = event_end + timedelta(hours=2)  # Look 2 hours after heating stops

        # Determine setpoint for this event
        if use_dynamic_setpoint and setpoint_history:
            # Get setpoint that was active when heating started
            event_setpoint = get_setpoint_at_time(setpoint_history, event_start)

            if event_setpoint is None:
                # Fallback to current setpoint
                event_setpoint = current_setpoint

            # Check if this event is near a setpoint change (within buffer)
            near_change = False
            for change_time, _ in setpoint_history:
                time_diff_hours = abs((event_start - change_time).total_seconds() / 3600)
                if time_diff_hours < setpoint_change_buffer_hours:
                    near_change = True
                    break

            if near_change:
                excluded_events += 1
                continue  # Skip this event - too close to setpoint change
        else:
            event_setpoint = current_setpoint

        # Get temperature data for this event
        event_temps = [
            t for t in temp_data
            if event_start <= t['time'] <= analysis_window_end
        ]

        if len(event_temps) < 5:
            continue

        # Find max temperature (potential overshoot) - using event-specific setpoint
        temps_after_heating = [t for t in event_temps if t['time'] >= event_end]
        if temps_after_heating:
            max_temp = max([t['temp'] for t in temps_after_heating])
            overshoot = max(0, max_temp - event_setpoint - hot_tolerance)
            if overshoot > 0:
                overshoots.append(overshoot)

        # Find min temperature before heating kicked in
        temps_before_heating = [t for t in event_temps if t['time'] <= event_start]
        if temps_before_heating:
            min_temp = min([t['temp'] for t in temps_before_heating])
            undershoot = max(0, event_setpoint - cold_tolerance - min_temp)
            if undershoot > 0:
                undershoots.append(undershoot)

        # Calculate rise time (time to reach setpoint from heating start)
        reached_setpoint = False
        for t in event_temps:
            if t['time'] >= event_start and t['temp'] >= event_setpoint - cold_tolerance:
                rise_time = (t['time'] - event_start).total_seconds() / 60
                rise_times.append(rise_time)
                reached_setpoint = True
                break

        # Count oscillations (setpoint crossings after heating stops)
        if temps_after_heating and len(temps_after_heating) > 3:
            crossings = 0
            above_setpoint = temps_after_heating[0]['temp'] > event_setpoint
            for t in temps_after_heating[1:]:
                currently_above = t['temp'] > event_setpoint
                if currently_above != above_setpoint:
                    crossings += 1
                    above_setpoint = currently_above
            oscillation_counts.append(crossings)

        # Calculate settling time (time until temp stays within tolerance)
        if temps_after_heating:
            settled_time = None
            for i, t in enumerate(temps_after_heating):
                in_tolerance = (event_setpoint - cold_tolerance) <= t['temp'] <= (event_setpoint + hot_tolerance)
                if in_tolerance:
                    # Check if it stays in tolerance for remaining samples
                    remaining = temps_after_heating[i:]
                    settled_checks = [
                        (event_setpoint - cold_tolerance) <= rt['temp'] <= (event_setpoint + hot_tolerance)
                        for rt in remaining[:10]  # Check next 10 samples
                    ]
                    all_settled = all(settled_checks)
                    if all_settled or len(remaining) < 3:
                        settled_time = t['time']
                        break

            if settled_time:
                settling_time = (settled_time - event_end).total_seconds() / 60
                if settling_time > 0:
                    settling_times.append(settling_time)

    # Calculate averages
    analyzed_count = max(len(overshoots), len(settling_times))

    # Log if events were excluded due to setpoint changes
    if excluded_events > 0:
        log.info(f"Zone {zone_id}: Excluded {excluded_events} events near setpoint changes")

    result = {
        'overshoot': round(sum(overshoots) / len(overshoots), 2) if overshoots else 0,
        'undershoot': round(sum(undershoots) / len(undershoots), 2) if undershoots else 0,
        'settling_time': round(sum(settling_times) / len(settling_times), 1) if settling_times else 0,
        'oscillation_count': round(sum(oscillation_counts) / len(oscillation_counts), 1) if oscillation_counts else 0,
        'rise_time': round(sum(rise_times) / len(rise_times), 1) if rise_times else 0,
        'response_events': len(heating_events),
        'analyzed_events': analyzed_count,
        'excluded_events': excluded_events,
        'dynamic_setpoint_detected': use_dynamic_setpoint,
        'setpoint_range': setpoint_info.get('setpoint_range', 0) if use_dynamic_setpoint else 0,
        'setpoint': current_setpoint if not use_dynamic_setpoint else None,
        'timestamp': datetime.now().isoformat()
    }

    return result

def calculate_adaptive_pid_adjustments(zone_id, zone_config, constants, current_pid, learned_metrics):
    """
    Calculate PID adjustments based on learned performance metrics.

    Adjustment rules:
    - High overshoot → reduce Kp, reduce Ki
    - Low/no overshoot with slow response → increase Kp
    - Steady-state error (undershoot) → increase Ki
    - Oscillations → reduce Kp, increase Kd
    - Long settling time → adjust Kd
    """
    if not learned_metrics:
        return None

    pid_limits = constants['pid_tuning']
    kp_min, kp_max = pid_limits['kp_min'], pid_limits['kp_max']
    ki_min, ki_max = pid_limits['ki_min'], pid_limits['ki_max']
    kd_slow, kd_fast = pid_limits['kd_slow'], pid_limits['kd_fast']

    # Start with current values
    current_kp = current_pid.get('kp', 0.5)
    current_ki = current_pid.get('ki', 0.01)
    current_kd = current_pid.get('kd', 5)

    new_kp = current_kp
    new_ki = current_ki
    new_kd = current_kd

    adjustments = []

    overshoot = learned_metrics.get('overshoot', 0)
    undershoot = learned_metrics.get('undershoot', 0)
    settling_time = learned_metrics.get('settling_time', 0)
    oscillations = learned_metrics.get('oscillation_count', 0)
    rise_time = learned_metrics.get('rise_time', 0)

    # Overshoot adjustments
    if overshoot > 0.5:  # More than 0.5°C overshoot
        # Significant overshoot - reduce Kp and Ki
        reduction = min(0.15, overshoot * 0.1)  # Up to 15% reduction
        new_kp = max(kp_min, current_kp * (1 - reduction))
        new_ki = max(ki_min, current_ki * (1 - reduction * 0.5))
        adjustments.append(f"Overshoot {overshoot}°C: reduce Kp by {reduction*100:.0f}%")
    elif overshoot > 0.2:
        # Moderate overshoot - slight Kp reduction
        new_kp = max(kp_min, current_kp * 0.95)
        adjustments.append(f"Moderate overshoot {overshoot}°C: slight Kp reduction")
    elif overshoot == 0 and rise_time > 60:
        # No overshoot but slow response - can increase Kp
        new_kp = min(kp_max, current_kp * 1.1)
        adjustments.append(f"Slow response ({rise_time}min): increase Kp")

    # Undershoot/steady-state error adjustments
    if undershoot > 0.3:
        # Room not reaching setpoint - increase Ki
        increase = min(0.2, undershoot * 0.15)
        new_ki = min(ki_max, current_ki * (1 + increase))
        adjustments.append(f"Undershoot {undershoot}°C: increase Ki by {increase*100:.0f}%")

    # Oscillation adjustments
    if oscillations > 3:
        # Many oscillations - reduce Kp, increase Kd
        new_kp = max(kp_min, new_kp * 0.9)
        new_kd = min(kd_fast, current_kd * 1.2)
        adjustments.append(f"Oscillations ({oscillations}): reduce Kp, increase Kd")
    elif oscillations > 1:
        # Some oscillations - slight Kd increase
        new_kd = min(kd_fast, current_kd * 1.1)
        adjustments.append(f"Some oscillations ({oscillations}): slight Kd increase")

    # Settling time adjustments
    if settling_time > 90:  # More than 90 minutes to settle
        # Very slow settling - increase Kd
        new_kd = min(kd_fast, current_kd * 1.15)
        adjustments.append(f"Slow settling ({settling_time}min): increase Kd")
    elif settling_time > 0 and settling_time < 20 and overshoot == 0:
        # Fast settling without overshoot - system is well tuned or could be more aggressive
        adjustments.append(f"Good settling ({settling_time}min): well tuned")

    # Apply limits
    new_kp = round(max(kp_min, min(kp_max, new_kp)), 3)
    new_ki = round(max(ki_min, min(ki_max, new_ki)), 4)
    new_kd = round(max(kd_slow, min(kd_fast, new_kd)), 1)

    return {
        'kp': new_kp,
        'ki': new_ki,
        'kd': new_kd,
        'adjustments': adjustments,
        'based_on_events': learned_metrics.get('analyzed_events', 0)
    }

# =============================================================================
# SENSOR DEFINITIONS
# =============================================================================

# Load configs at module level for sensor definitions
_zones_config = None
_constants_config = None
_current_pid = None
_learning_data = None

def _get_configs():
    global _zones_config, _constants_config, _current_pid, _learning_data
    if _zones_config is None:
        _zones_config = load_zones_config()
        _constants_config = load_constants_config()
        _current_pid = load_current_pid_values()
        _learning_data = load_learning_data()
    return _zones_config, _constants_config, _current_pid, _learning_data

def _reload_learning_data():
    """Reload learning data from file."""
    global _learning_data
    _learning_data = load_learning_data()
    return _learning_data

# Per-zone performance sensors
@time_trigger("cron(*/5 * * * *)")  # Update every 5 minutes
async def update_performance_sensors():
    """Update per-zone performance sensors."""
    zones_config, constants, current_pid, learning_data = _get_configs()

    total_power = 0
    total_area = 0

    for zone_id, zone_config in zones_config['zones'].items():
        analysis = await analyze_cycles(zone_id, zone_config, hours=24)

        if analysis:
            # Set per-zone sensors
            state.set(
                f"sensor.heating_{zone_id}_power_m2",
                analysis['power_w_m2'],
                {
                    "unit_of_measurement": "W/m²",
                    "friendly_name": f"{zone_config['display_name']} Power",
                    "icon": "mdi:lightning-bolt"
                }
            )
            state.set(
                f"sensor.heating_{zone_id}_cycle_time",
                analysis['avg_cycle_minutes'],
                {
                    "unit_of_measurement": "min",
                    "friendly_name": f"{zone_config['display_name']} Cycle Time",
                    "icon": "mdi:timer"
                }
            )
            state.set(
                f"sensor.heating_{zone_id}_duty_cycle",
                analysis['duty_cycle_percent'],
                {
                    "unit_of_measurement": "%",
                    "friendly_name": f"{zone_config['display_name']} Duty Cycle",
                    "icon": "mdi:percent"
                }
            )

            total_power += analysis['power_w_m2'] * zone_config['area_m2']
            total_area += zone_config['area_m2']

    # System-wide sensor
    if total_area > 0:
        avg_power = total_power / total_area
        state.set(
            "sensor.heating_total_power_m2",
            round(avg_power, 1),
            {
                "unit_of_measurement": "W/m²",
                "friendly_name": "Heating System Power",
                "icon": "mdi:home-lightning-bolt"
            }
        )

# System-wide heat output sensors (supply/return temps)
@time_trigger("cron(*/5 * * * *)")  # Update every 5 minutes
async def update_system_heat_sensors():
    """Update system-wide heat output sensors based on supply/return temps."""
    _, constants, _, _ = _get_configs()

    system_config = constants.get('system', {})
    supply_sensor = system_config.get('supply_temp_sensor')
    return_sensor = system_config.get('return_temp_sensor')
    outdoor_sensor = system_config.get('outdoor_temp_sensor')
    flow_sensor = system_config.get('flow_sensor')
    flow_unit = system_config.get('flow_unit', 'l_h')
    fallback_flow = system_config.get('fallback_flow_m3_h', 0.5)

    # Get supply and return temperatures
    supply_temp = None
    return_temp = None
    outdoor_temp = None
    flow_rate = None
    flow_source = "fallback"

    if supply_sensor:
        try:
            supply_temp = float(state.get(supply_sensor))
        except (NameError, ValueError, TypeError):
            pass

    if return_sensor:
        try:
            return_temp = float(state.get(return_sensor))
        except (NameError, ValueError, TypeError):
            pass

    if outdoor_sensor:
        try:
            outdoor_temp = float(state.get(outdoor_sensor))
        except (NameError, ValueError, TypeError):
            pass

    # Get flow rate from sensor or use fallback
    if flow_sensor:
        try:
            raw_flow = float(state.get(flow_sensor))
            if raw_flow > 0:
                # Convert to m³/h based on unit
                if flow_unit == 'l_h':
                    flow_rate = raw_flow / 1000  # L/h to m³/h
                elif flow_unit == 'l_min':
                    flow_rate = raw_flow * 60 / 1000  # L/min to m³/h
                elif flow_unit == 'm3_h':
                    flow_rate = raw_flow  # Already in m³/h
                else:
                    flow_rate = raw_flow / 1000  # Default: assume L/h
                flow_source = "sensor"
        except (NameError, ValueError, TypeError):
            pass

    # Use fallback if sensor not available or zero flow
    if flow_rate is None or flow_rate <= 0:
        flow_rate = fallback_flow
        flow_source = "fallback"

    # Calculate delta T and heat output
    if supply_temp is not None and return_temp is not None:
        delta_t = supply_temp - return_temp

        # Heat output: Q (kW) = flow_rate (m³/h) × 1.163 × ΔT
        # Formula: Q = m × cp × ΔT where water cp = 4.186 kJ/(kg·K), density = 1000 kg/m³
        # Simplified: Q (kW) = flow_rate (m³/h) × 4186 / 3600 × ΔT = flow_rate × 1.163 × ΔT
        heat_output_kw = flow_rate * 1.163 * delta_t

        # Create sensors
        state.set(
            "sensor.heating_supply_temp",
            round(supply_temp, 1),
            {
                "unit_of_measurement": "°C",
                "friendly_name": "Heating Supply Temperature",
                "icon": "mdi:thermometer-chevron-up"
            }
        )

        state.set(
            "sensor.heating_return_temp",
            round(return_temp, 1),
            {
                "unit_of_measurement": "°C",
                "friendly_name": "Heating Return Temperature",
                "icon": "mdi:thermometer-chevron-down"
            }
        )

        state.set(
            "sensor.heating_delta_t",
            round(delta_t, 1),
            {
                "unit_of_measurement": "°C",
                "friendly_name": "Heating ΔT (Supply-Return)",
                "icon": "mdi:thermometer-lines",
                "supply_temp": round(supply_temp, 1),
                "return_temp": round(return_temp, 1)
            }
        )

        state.set(
            "sensor.heating_heat_output",
            round(heat_output_kw, 2),
            {
                "unit_of_measurement": "kW",
                "friendly_name": "Heating Heat Output",
                "icon": "mdi:fire",
                "flow_rate_m3_h": round(flow_rate, 4),
                "flow_source": flow_source,
                "delta_t": round(delta_t, 1)
            }
        )

        # Also create a flow rate sensor for visibility
        state.set(
            "sensor.heating_flow_rate",
            round(flow_rate * 1000, 1),  # Display in L/h for readability
            {
                "unit_of_measurement": "L/h",
                "friendly_name": "Heating Flow Rate",
                "icon": "mdi:water-pump",
                "source": flow_source,
                "m3_h": round(flow_rate, 4)
            }
        )

        # Log for debugging
        log.debug(f"Heat output: {heat_output_kw:.2f} kW (ΔT={delta_t:.1f}°C, flow={flow_rate:.4f} m³/h [{flow_source}])")

    # Outdoor temperature sensor (for reference/weather compensation)
    if outdoor_temp is not None:
        state.set(
            "sensor.heating_outdoor_temp",
            round(outdoor_temp, 1),
            {
                "unit_of_measurement": "°C",
                "friendly_name": "Outdoor Temperature",
                "icon": "mdi:thermometer"
            }
        )

# PID sensors - current values from configuration.yaml
@time_trigger("startup")
async def update_current_pid_sensors():
    """Update sensors showing current PID values from configuration.yaml."""
    zones_config, constants, current_pid, _ = _get_configs()

    for zone_id, zone_config in zones_config['zones'].items():
        pid = current_pid.get(zone_id, {})

        state.set(
            f"sensor.heating_{zone_id}_current_kp",
            pid.get('kp', 'unknown'),
            {
                "friendly_name": f"{zone_config['display_name']} Current Kp",
                "icon": "mdi:tune"
            }
        )
        state.set(
            f"sensor.heating_{zone_id}_current_ki",
            pid.get('ki', 'unknown'),
            {
                "friendly_name": f"{zone_config['display_name']} Current Ki",
                "icon": "mdi:tune"
            }
        )
        state.set(
            f"sensor.heating_{zone_id}_current_kd",
            pid.get('kd', 'unknown'),
            {
                "friendly_name": f"{zone_config['display_name']} Current Kd",
                "icon": "mdi:tune"
            }
        )

# PID sensors - recommended values (adaptive learning)
@time_trigger("startup")
async def update_recommended_pid_sensors():
    """Update sensors showing recommended PID values based on adaptive learning."""
    zones_config, constants, current_pid, learning_data = _get_configs()

    for zone_id, zone_config in zones_config['zones'].items():
        # Start with physics-based calculation
        base_recommended = calculate_recommended_pid(zone_id, zone_config, constants)

        # Check if zone is in heat mode - only apply adaptive learning when heating
        in_heat_mode = is_zone_in_heat_mode(zone_config)

        # Apply adaptive adjustments only if in heat mode and learning data exists
        zone_learning = learning_data.get(zone_id, {})
        zone_pid = current_pid.get(zone_id, {})

        if not in_heat_mode:
            # Zone is cooling - use physics-based only, mark as disabled
            recommended = base_recommended
            source = "physics (not heating)"
            adjustments = []
        elif zone_learning:
            adaptive = calculate_adaptive_pid_adjustments(
                zone_id, zone_config, constants, zone_pid, zone_learning
            )
            if adaptive:
                recommended = adaptive
                source = "adaptive"
                adjustments = adaptive.get('adjustments', [])
            else:
                recommended = base_recommended
                source = "physics"
                adjustments = []
        else:
            recommended = base_recommended
            source = "physics"
            adjustments = []

        state.set(
            f"sensor.heating_{zone_id}_recommended_kp",
            recommended['kp'],
            {
                "friendly_name": f"{zone_config['display_name']} Recommended Kp",
                "icon": "mdi:tune-variant",
                "source": source
            }
        )
        state.set(
            f"sensor.heating_{zone_id}_recommended_ki",
            recommended['ki'],
            {
                "friendly_name": f"{zone_config['display_name']} Recommended Ki",
                "icon": "mdi:tune-variant",
                "source": source
            }
        )
        state.set(
            f"sensor.heating_{zone_id}_recommended_kd",
            recommended['kd'],
            {
                "friendly_name": f"{zone_config['display_name']} Recommended Kd",
                "icon": "mdi:tune-variant",
                "source": source,
                "adjustments": adjustments
            }
        )

# Learning metrics sensors
async def update_learning_sensors():
    """Update sensors showing learned performance metrics per zone."""
    zones_config, constants, _, learning_data = _get_configs()

    for zone_id, zone_config in zones_config['zones'].items():
        zone_learning = learning_data.get(zone_id, {})

        state.set(
            f"sensor.heating_{zone_id}_overshoot",
            zone_learning.get('overshoot', 0),
            {
                "unit_of_measurement": "°C",
                "friendly_name": f"{zone_config['display_name']} Overshoot",
                "icon": "mdi:thermometer-chevron-up",
                "last_updated": zone_learning.get('timestamp', 'never')
            }
        )
        state.set(
            f"sensor.heating_{zone_id}_settling_time",
            zone_learning.get('settling_time', 0),
            {
                "unit_of_measurement": "min",
                "friendly_name": f"{zone_config['display_name']} Settling Time",
                "icon": "mdi:timer-sand",
                "last_updated": zone_learning.get('timestamp', 'never')
            }
        )
        state.set(
            f"sensor.heating_{zone_id}_oscillations",
            zone_learning.get('oscillation_count', 0),
            {
                "friendly_name": f"{zone_config['display_name']} Oscillations",
                "icon": "mdi:sine-wave",
                "last_updated": zone_learning.get('timestamp', 'never')
            }
        )

# =============================================================================
# ENERGY COST TRACKING
# =============================================================================

@state_trigger("input_number.heating_gj_cost")
@time_trigger("cron(0 * * * *)")  # Update hourly
async def update_cost_sensor():
    """Update energy cost sensor based on GJ price and consumption."""
    zones_config, constants, _, _ = _get_configs()

    # Get GJ cost - handle missing helper gracefully
    try:
        gj_cost = float(state.get("input_number.heating_gj_cost") or 0)
    except NameError:
        # Helper not created yet - use default from constants
        gj_cost = constants['energy'].get('default_gj_cost', 35)

    # Get GJ meter reading if available
    gj_sensor = constants['energy'].get('gj_meter_sensor')
    gj_to_kwh = constants['energy']['gj_to_kwh_factor']
    meter_type = constants['energy'].get('meter_type', 'cumulative')

    gj_used = 0
    kwh_used = 0
    cost = 0

    if gj_sensor:
        try:
            current_gj = float(state.get(gj_sensor) or 0)

            if meter_type == 'cumulative':
                # For cumulative meters, calculate weekly delta using history
                gj_history = await _fetch_state_history(hass, gj_sensor,
                    datetime.now() - timedelta(days=7), datetime.now())

                if gj_history and len(gj_history) > 0:
                    # Get the oldest reading from 7 days ago
                    try:
                        oldest_gj = float(gj_history[0].state)
                        gj_used = max(0, current_gj - oldest_gj)
                        log.debug(f"GJ meter: current={current_gj}, 7d ago={oldest_gj}, weekly={gj_used}")
                    except (ValueError, TypeError):
                        log.warning(f"Could not parse GJ history, using estimate")
                        gj_used = 0
                else:
                    log.warning(f"No GJ history available for weekly calculation")
                    gj_used = 0
            else:
                # For delta/utility meters that reset weekly, use current value directly
                gj_used = current_gj

            if gj_used > 0:
                kwh_used = gj_used * gj_to_kwh
                cost = gj_used * gj_cost
        except (NameError, ValueError, TypeError) as e:
            log.warning(f"Error reading GJ sensor: {e}")
            gj_used = 0

    # Fallback to duty cycle estimation if no GJ data
    if gj_used == 0:
        total_kwh = 0
        for zone_id, zone_config in zones_config['zones'].items():
            try:
                duty = float(state.get(f"sensor.heating_{zone_id}_duty_cycle") or 0)
            except NameError:
                # Sensor not created yet - skip this zone
                duty = 0
            area = zone_config['area_m2']
            # Estimate: duty% * 50W/m2 * area * 24h / 1000
            zone_kwh = (duty / 100) * 50 * area * 24 / 1000
            total_kwh += zone_kwh

        kwh_used = total_kwh * 7  # Weekly estimate
        gj_used = kwh_used / gj_to_kwh
        cost = gj_used * gj_cost

    state.set(
        "sensor.heating_total_cost",
        round(cost, 2),
        {
            "unit_of_measurement": "EUR",
            "friendly_name": "Heating Weekly Cost",
            "icon": "mdi:currency-eur",
            "gj_used": round(gj_used, 3),
            "kwh_used": round(kwh_used, 1),
            "source": "gj_meter" if gj_used > 0 else "estimated"
        }
    )

# =============================================================================
# HEALTH MONITORING
# =============================================================================

@time_trigger("cron(0 */6 * * *)")  # Every 6 hours
async def health_check():
    """Run health check and alert on issues."""
    zones_config, constants, _, _ = _get_configs()
    health_config = constants['health']
    thresholds = constants['thresholds']
    notification_service = constants['notification']['service']

    issues = []
    overall_health = "healthy"

    for zone_id, zone_config in zones_config['zones'].items():
        # Check cycle time
        cycle_time = float(state.get(f"sensor.heating_{zone_id}_cycle_time") or 0)

        if cycle_time > 0 and cycle_time < health_config['very_short_cycle_min']:
            issues.append(f"{zone_config['display_name']}: Very short cycles ({cycle_time} min) - check actuator or increase min_cycle_duration")
            overall_health = "critical"
        elif cycle_time > 0 and cycle_time < health_config['short_cycle_min']:
            issues.append(f"{zone_config['display_name']}: Short cycles ({cycle_time} min) - consider increasing min_cycle_duration")
            if overall_health != "critical":
                overall_health = "warning"

        # Check power (skip exceptions)
        power = float(state.get(f"sensor.heating_{zone_id}_power_m2") or 0)
        if zone_id not in health_config['high_power_exception_zones']:
            if power > health_config['high_power_threshold_w_m2']:
                issues.append(f"{zone_config['display_name']}: High power ({power} W/m²) - check insulation or setpoint")
                if overall_health != "critical":
                    overall_health = "warning"

        # Check sensor availability
        temp_sensor = zone_config['temperature_sensor']
        try:
            temp_state = state.get(temp_sensor)
            if temp_state in [None, 'unavailable', 'unknown']:
                issues.append(f"{zone_config['display_name']}: Temperature sensor unavailable")
                overall_health = "critical"
        except NameError:
            issues.append(f"{zone_config['display_name']}: Temperature sensor not found ({temp_sensor})")
            overall_health = "critical"

    # Update health sensor
    state.set(
        "sensor.heating_system_health",
        overall_health,
        {
            "friendly_name": "Heating System Health",
            "icon": "mdi:heart-pulse" if overall_health == "healthy" else "mdi:alert",
            "issues": issues
        }
    )

    # Send notification only if issues found
    if issues:
        issue_lines = [f"- {i}" for i in issues]
        full_message = "Heating System Issues:\n" + "\n".join(issue_lines)

        # Create persistent notification with full details
        service.call("persistent_notification", "create",
                     message=full_message,
                     title="Heating Alert",
                     notification_id="heating_health_alert")

        # Send short summary to mobile
        mobile_summary = f"Status: {overall_health.upper()}\n{len(issues)} issue(s) detected"
        service.call("notify", notification_service,
                     message=mobile_summary,
                     title="Heating Alert",
                     data={"push": {"interruption-level": "time-sensitive"}})

# =============================================================================
# SERVICES
# =============================================================================

# =============================================================================
# ADAPTIVE LEARNING
# =============================================================================

@time_trigger("cron(0 3 * * *)")  # Daily at 3:00 AM
async def run_adaptive_learning():
    """
    Run adaptive learning analysis for all zones.
    Analyzes 7 days of temperature/heating history and updates recommendations.
    """
    global _learning_data
    zones_config, constants, current_pid, learning_data = _get_configs()
    notification_service = constants['notification']['service']

    log.info("Starting adaptive learning analysis...")

    updated_zones = []
    skipped_cool_zones = []
    for zone_id, zone_config in zones_config['zones'].items():
        # Skip zones not in heat mode - cooling data would skew heating PID
        if not is_zone_in_heat_mode(zone_config):
            skipped_cool_zones.append(zone_id)
            log.info(f"Zone {zone_id}: skipped (not in heat mode)")
            continue

        zone_pid = current_pid.get(zone_id, {})

        # Analyze temperature response for this zone
        metrics = await analyze_heating_response(zone_id, zone_config, zone_pid, hours=168)

        if metrics and metrics.get('analyzed_events', 0) >= 3:
            # Store learned metrics
            learning_data[zone_id] = metrics
            updated_zones.append(zone_id)
            log.info(f"Zone {zone_id}: overshoot={metrics['overshoot']}°C, "
                    f"settling={metrics['settling_time']}min, "
                    f"oscillations={metrics['oscillation_count']}")
        else:
            log.info(f"Zone {zone_id}: insufficient data for learning "
                    f"({metrics.get('analyzed_events', 0) if metrics else 0} events)")

    # Save learning data
    if updated_zones:
        save_learning_data(learning_data)
        _learning_data = learning_data

        # Update sensors with new learned data
        await update_learning_sensors()
        await update_recommended_pid_sensors()

        log.info(f"Adaptive learning complete. Updated zones: {', '.join(updated_zones)}")
        if skipped_cool_zones:
            log.info(f"Skipped zones (not in heat mode): {', '.join(skipped_cool_zones)}")
    else:
        log.info("Adaptive learning complete. No zones updated.")
        if skipped_cool_zones:
            log.info(f"Skipped zones (not in heat mode): {', '.join(skipped_cool_zones)}")

@service
async def heating_run_learning():
    """Manually trigger adaptive learning for all zones."""
    await run_adaptive_learning()
    return "Adaptive learning completed"

@service
async def heating_apply_weekly_pid():
    """Manually trigger weekly PID auto-update for zones in heat mode."""
    return await scheduled_weekly_pid_update()

@service
async def heating_weekly_report():
    """Generate and send weekly performance report."""
    zones_config, constants, current_pid, _ = _get_configs()
    thresholds = constants['thresholds']
    notification_service = constants['notification']['service']

    report_lines = ["Weekly Heating Report", "=" * 30, ""]

    total_power = 0
    total_area = 0
    issues = []

    for zone_id, zone_config in zones_config['zones'].items():
        analysis = await analyze_cycles(zone_id, zone_config, hours=168)
        area = zone_config['area_m2']

        if analysis:
            power = analysis['power_w_m2']
            cycle = analysis['avg_cycle_minutes']
            duty = analysis['duty_cycle_percent']

            # Performance rating
            if power <= thresholds['excellent_w_m2']:
                rating = "Excellent"
            elif power <= thresholds['target_w_m2']:
                rating = "Good"
            else:
                rating = "High"

            report_lines.append(f"{zone_config['display_name']}:")
            report_lines.append(f"  Power: {power} W/m² ({rating})")
            report_lines.append(f"  Cycle: {cycle} min, Duty: {duty}%")
            report_lines.append("")

            total_power += power * area
            total_area += area

            # Flag issues
            if cycle > 0 and cycle < 15:
                issues.append(f"{zone_config['display_name']}: short cycles")

    # System summary
    if total_area > 0:
        avg_power = round(total_power / total_area, 1)
        report_lines.append(f"System Average: {avg_power} W/m²")

        # Cost
        cost = float(state.get("sensor.heating_total_cost") or 0)
        if cost > 0:
            report_lines.append(f"Estimated Weekly Cost: €{cost:.2f}")

    # Issues summary
    if issues:
        report_lines.append("")
        report_lines.append("Issues: " + ", ".join(issues))

    full_message = "\n".join(report_lines)

    # Create persistent notification with full report (readable in HA)
    service.call("persistent_notification", "create",
                 message=full_message,
                 title="Weekly Heating Report",
                 notification_id="heating_weekly_report")

    # Send short summary to mobile
    if total_area > 0:
        mobile_summary = f"Avg: {avg_power} W/m² | Cost: €{cost:.2f}/week"
        if issues:
            mobile_summary += f"\n⚠️ {len(issues)} issue(s)"
    else:
        mobile_summary = "No data available"

    service.call("notify", notification_service,
                 message=mobile_summary,
                 title="Weekly Heating Report",
                 data={"push": {"interruption-level": "passive"}})

    log.info("Weekly heating report sent")
    return full_message

@service
async def heating_health_check():
    """Manually trigger health check."""
    await health_check()
    return "Health check completed"

@service
async def heating_pid_recommendations():
    """Generate PID tuning recommendations for all zones (with adaptive learning)."""
    zones_config, constants, current_pid, learning_data = _get_configs()
    notification_service = constants['notification']['service']

    report_lines = ["PID Tuning Recommendations", "=" * 30, ""]

    skipped_cool_zones = []
    for zone_id, zone_config in zones_config['zones'].items():
        # Skip zones not in heat mode - cooling data would skew heating PID
        if not is_zone_in_heat_mode(zone_config):
            skipped_cool_zones.append(zone_config['display_name'])
            continue

        current = current_pid.get(zone_id, {})
        base_recommended = calculate_recommended_pid(zone_id, zone_config, constants)

        # Check for adaptive learning data
        zone_learning = learning_data.get(zone_id, {})
        if zone_learning:
            adaptive = calculate_adaptive_pid_adjustments(
                zone_id, zone_config, constants, current, zone_learning
            )
            if adaptive:
                recommended = adaptive
                source = "Adaptive (7-day analysis)"
                adjustments = adaptive.get('adjustments', [])
            else:
                recommended = base_recommended
                source = "Physics-based"
                adjustments = []
        else:
            recommended = base_recommended
            source = "Physics-based (no learning data)"
            adjustments = []

        report_lines.append(f"{zone_config['display_name']}:")
        report_lines.append(f"  Current:     Kp={current.get('kp', '?')}, Ki={current.get('ki', '?')}, Kd={current.get('kd', '?')}")
        report_lines.append(f"  Recommended: Kp={recommended['kp']}, Ki={recommended['ki']}, Kd={recommended['kd']}")
        report_lines.append(f"  Source: {source}")

        if zone_learning:
            report_lines.append(f"  Learned: overshoot={zone_learning.get('overshoot', 0)}°C, "
                              f"settling={zone_learning.get('settling_time', 0)}min")

        if adjustments:
            for adj in adjustments:
                report_lines.append(f"    - {adj}")

        report_lines.append(f"  Thermal τ: {base_recommended['tau_hours']} hours")
        report_lines.append("")

    # Add note about skipped zones
    if skipped_cool_zones:
        report_lines.append("Skipped (not in heat mode):")
        report_lines.append(f"  {', '.join(skipped_cool_zones)}")
        report_lines.append("")

    full_message = "\n".join(report_lines)

    # Create persistent notification with full report
    service.call("persistent_notification", "create",
                 message=full_message,
                 title="PID Recommendations",
                 notification_id="heating_pid_recommendations")

    # Count zones needing adjustment (only zones in heat mode)
    zones_needing_change = 0
    zones_analyzed = 0
    for zone_id, zone_config in zones_config['zones'].items():
        if not is_zone_in_heat_mode(zone_config):
            continue
        zones_analyzed += 1
        current = current_pid.get(zone_id, {})
        zone_learning = learning_data.get(zone_id, {})
        if zone_learning:
            adaptive = calculate_adaptive_pid_adjustments(
                zone_id, zone_config, constants, current, zone_learning
            )
            if adaptive and adaptive.get('adjustments'):
                zones_needing_change += 1

    # Send short summary to mobile
    mobile_summary = f"{zones_analyzed} zones analyzed"
    if skipped_cool_zones:
        mobile_summary += f" ({len(skipped_cool_zones)} skipped - not heating)"
    if zones_needing_change > 0:
        mobile_summary += f"\n{zones_needing_change} zone(s) have tuning suggestions"
    elif zones_analyzed > 0:
        mobile_summary += "\nAll zones well tuned"

    service.call("notify", notification_service,
                 message=mobile_summary,
                 title="PID Recommendations",
                 data={"push": {"interruption-level": "passive"}})

    log.info("PID recommendations report sent")
    return full_message

@service
async def heating_cost_report():
    """Generate cost breakdown report."""
    zones_config, constants, _, _ = _get_configs()
    notification_service = constants['notification']['service']

    # Get GJ cost - handle missing helper gracefully
    try:
        gj_cost = float(state.get("input_number.heating_gj_cost") or 0)
    except NameError:
        gj_cost = constants['energy'].get('default_gj_cost', 35)
    gj_to_kwh = constants['energy']['gj_to_kwh_factor']
    gj_sensor = constants['energy'].get('gj_meter_sensor')
    meter_type = constants['energy'].get('meter_type', 'cumulative')

    report_lines = ["Heating Cost Report", "=" * 30, ""]
    report_lines.append(f"GJ Price: €{gj_cost}/GJ")
    report_lines.append("")

    # Get actual GJ meter reading if available
    actual_gj_weekly = None
    if gj_sensor:
        try:
            current_gj = float(state.get(gj_sensor) or 0)
            if meter_type == 'cumulative':
                gj_history = await _fetch_state_history(hass, gj_sensor,
                    datetime.now() - timedelta(days=7), datetime.now())
                if gj_history and len(gj_history) > 0:
                    oldest_gj = float(gj_history[0].state)
                    actual_gj_weekly = max(0, current_gj - oldest_gj)
            else:
                actual_gj_weekly = current_gj
        except (NameError, ValueError, TypeError):
            pass

    # Per-zone estimates
    report_lines.append("Per-Zone Estimates (duty cycle):")
    total_kwh = 0
    for zone_id, zone_config in zones_config['zones'].items():
        try:
            duty = float(state.get(f"sensor.heating_{zone_id}_duty_cycle") or 0)
        except NameError:
            duty = 0
        area = zone_config['area_m2']
        zone_kwh_day = (duty / 100) * 50 * area * 24 / 1000
        zone_kwh_week = zone_kwh_day * 7

        report_lines.append(f"  {zone_config['display_name']}: {zone_kwh_week:.1f} kWh/week")
        total_kwh += zone_kwh_week

    estimated_gj = total_kwh / gj_to_kwh
    estimated_cost = estimated_gj * gj_cost

    report_lines.append("")
    report_lines.append(f"Estimated Total: {total_kwh:.1f} kWh ({estimated_gj:.3f} GJ)")
    report_lines.append(f"Estimated Cost: €{estimated_cost:.2f}")

    # Show actual meter reading if available
    if actual_gj_weekly is not None:
        actual_kwh = actual_gj_weekly * gj_to_kwh
        actual_cost = actual_gj_weekly * gj_cost
        report_lines.append("")
        report_lines.append("Actual (GJ meter):")
        report_lines.append(f"  Weekly: {actual_kwh:.1f} kWh ({actual_gj_weekly:.3f} GJ)")
        report_lines.append(f"  Cost: €{actual_cost:.2f}")
        # Use actual values for summary
        gj_used = actual_gj_weekly
        cost = actual_cost
    else:
        gj_used = estimated_gj
        cost = estimated_cost

    full_message = "\n".join(report_lines)

    # Create persistent notification with full report
    service.call("persistent_notification", "create",
                 message=full_message,
                 title="Heating Cost Report",
                 notification_id="heating_cost_report")

    # Send short summary to mobile
    kwh_for_summary = gj_used * gj_to_kwh
    source_label = " (actual)" if actual_gj_weekly is not None else " (est)"
    mobile_summary = f"Weekly: {kwh_for_summary:.1f} kWh ({gj_used:.3f} GJ){source_label}\nCost: €{cost:.2f}"

    service.call("notify", notification_service,
                 message=mobile_summary,
                 title="Heating Cost Report",
                 data={"push": {"interruption-level": "passive"}})

    log.info("Cost report sent")
    return full_message

@service
async def heating_apply_recommended_pid(zone_id=None, clear_integral=True):
    """
    Apply recommended PID values to thermostats.

    Args:
        zone_id: Optional zone to update. If None, updates all zones.
        clear_integral: Whether to clear the integral after applying (default: True)

    This service:
    1. Applies PID values immediately via smart_thermostat.set_pid_gain
    2. Optionally clears the integral accumulator
    3. Updates configuration.yaml for persistence across restarts
    """
    zones_config, constants, current_pid, learning_data = _get_configs()
    notification_service = constants['notification']['service']

    # Determine which zones to update
    if zone_id:
        if zone_id not in zones_config['zones']:
            log.error(f"Unknown zone: {zone_id}")
            return f"Error: Unknown zone '{zone_id}'"
        zones_to_update = {zone_id: zones_config['zones'][zone_id]}
    else:
        zones_to_update = zones_config['zones']

    updated_zones = []
    failed_zones = []
    skipped_cool_zones = []
    changes = []

    for zid, zone_config in zones_to_update.items():
        # Skip zones not in heat mode - applying heating PID during cooling would be incorrect
        if not is_zone_in_heat_mode(zone_config):
            skipped_cool_zones.append(zid)
            log.info(f"Zone {zid}: skipped (not in heat mode)")
            continue

        climate_entity = zone_config['climate_entity']
        current = current_pid.get(zid, {})

        # Get recommended PID (adaptive if available, otherwise physics-based)
        zone_learning = learning_data.get(zid, {})
        if zone_learning and zone_learning.get('analyzed_events', 0) >= 3:
            recommended = calculate_adaptive_pid_adjustments(
                zid, zone_config, constants, current, zone_learning
            )
            source = "adaptive"
        else:
            recommended = calculate_recommended_pid(zid, zone_config, constants)
            source = "physics"

        if not recommended:
            log.warning(f"No recommendations available for {zid}")
            continue

        new_kp = recommended['kp']
        new_ki = recommended['ki']
        new_kd = recommended['kd']

        # Check if values are different
        if (current.get('kp') == new_kp and
            current.get('ki') == new_ki and
            current.get('kd') == new_kd):
            log.info(f"Zone {zid}: PID values already match recommendations")
            continue

        try:
            # Apply PID immediately via smart_thermostat service
            service.call("smart_thermostat", "set_pid_gain",
                         entity_id=climate_entity,
                         kp=new_kp,
                         ki=new_ki,
                         kd=new_kd)

            # Clear integral if requested
            if clear_integral:
                service.call("smart_thermostat", "clear_integral",
                             entity_id=climate_entity)

            changes.append({
                'zone': zid,
                'climate_entity': climate_entity,
                'old': {'kp': current.get('kp'), 'ki': current.get('ki'), 'kd': current.get('kd')},
                'new': {'kp': new_kp, 'ki': new_ki, 'kd': new_kd},
                'source': source
            })
            updated_zones.append(zid)
            log.info(f"Zone {zid}: Applied PID Kp={new_kp}, Ki={new_ki}, Kd={new_kd} ({source})")

        except Exception as e:
            log.error(f"Failed to apply PID for {zid}: {e}")
            failed_zones.append(zid)

    # Update configuration.yaml for persistence
    if changes:
        try:
            _update_configuration_yaml_pid(changes)
            log.info("Updated configuration.yaml with new PID values")
        except Exception as e:
            log.error(f"Failed to update configuration.yaml: {e}")
            # Still consider it a success since runtime values are applied

    # Send notification
    if updated_zones:
        summary = f"Applied PID to {len(updated_zones)} zone(s): {', '.join(updated_zones)}"
        if clear_integral:
            summary += "\nIntegral cleared"
        if skipped_cool_zones:
            summary += f"\nSkipped (not heating): {', '.join(skipped_cool_zones)}"

        service.call("persistent_notification", "create",
                     message=summary,
                     title="PID Applied",
                     notification_id="heating_pid_applied")

        service.call("notify", notification_service,
                     message=f"PID updated for {len(updated_zones)} zone(s)",
                     title="PID Applied",
                     data={"push": {"interruption-level": "passive"}})
    elif skipped_cool_zones and not failed_zones:
        # All zones were skipped due to cooling mode
        summary = f"No zones updated - all requested zones are not in heat mode:\n{', '.join(skipped_cool_zones)}"
        service.call("persistent_notification", "create",
                     message=summary,
                     title="PID Apply Skipped",
                     notification_id="heating_pid_applied")

    # Reload current PID values
    global _current_pid
    _current_pid = load_current_pid_values()
    await update_current_pid_sensors()

    result = f"Updated: {', '.join(updated_zones) if updated_zones else 'none'}"
    if skipped_cool_zones:
        result += f"\nSkipped (not heating): {', '.join(skipped_cool_zones)}"
    if failed_zones:
        result += f"\nFailed: {', '.join(failed_zones)}"
    return result


@pyscript_executor
def _read_file_raw(path):
    """Read file as raw text. Runs in executor thread."""
    with open(str(path), 'r', encoding='utf-8') as f:
        return f.read()

@pyscript_executor
def _write_file_raw(path, content):
    """Write raw text to file. Runs in executor thread."""
    with open(str(path), 'w', encoding='utf-8') as f:
        f.write(content)

def _update_configuration_yaml_pid(changes):
    """Update configuration.yaml with new PID values."""
    import re

    # Read raw file content for text manipulation
    raw_content = _read_file_raw(HA_CONFIG_FILE)

    for change in changes:
        climate_entity = change['climate_entity']
        # Extract unique_id from climate entity (e.g., climate.thermostat_gf -> thermostat_gf)
        unique_id = climate_entity.replace('climate.', '')

        new_kp = change['new']['kp']
        new_ki = change['new']['ki']
        new_kd = change['new']['kd']

        # Find the thermostat block by unique_id and update kp, ki, kd
        # Pattern to find the thermostat section
        pattern = rf'(unique_id:\s*{unique_id}\s*\n(?:.*\n)*?)'
        match = re.search(pattern, raw_content)

        if match:
            block_start = match.start()
            # Find the end of this thermostat block (next "- platform:" or end of climate section)
            remaining = raw_content[block_start:]
            next_platform = re.search(r'\n  - platform:', remaining[1:])
            if next_platform:
                block_end = block_start + 1 + next_platform.start()
            else:
                block_end = len(raw_content)

            block = raw_content[block_start:block_end]

            # Update kp, ki, kd in this block
            block = re.sub(r'(kp:\s*)[\d.]+', rf'\g<1>{new_kp}', block)
            block = re.sub(r'(ki:\s*)[\d.]+', rf'\g<1>{new_ki}', block)
            block = re.sub(r'(kd:\s*)[\d.]+', rf'\g<1>{new_kd}', block)

            raw_content = raw_content[:block_start] + block + raw_content[block_end:]

    # Write updated content
    _write_file_raw(HA_CONFIG_FILE, raw_content)

    log.info("configuration.yaml updated with new PID values")


@service
async def heating_test_history():
    """Test service to verify history retrieval works."""
    zones_config, _, _, _ = _get_configs()

    report = ["History Retrieval Test", "=" * 40, ""]

    # Test first zone only
    zone_id = list(zones_config['zones'].keys())[0]
    zone_config = zones_config['zones'][zone_id]
    heater = zone_config['heater_switch']
    temp_sensor = zone_config['temperature_sensor']

    # Test 24 hours
    heater_history = await get_heater_history(heater, hours=24)
    temp_history = await get_temperature_history(temp_sensor, hours=24)

    report.append(f"Zone: {zone_config['display_name']}")
    report.append(f"  Heater ({heater}): {len(heater_history)} states")
    report.append(f"  Temperature ({temp_sensor}): {len(temp_history)} states")

    if heater_history:
        report.append(f"  Latest heater: {heater_history[-1].state} at {heater_history[-1].last_changed}")
    if temp_history:
        report.append(f"  Latest temp: {temp_history[-1].state}°C at {temp_history[-1].last_changed}")

    message = "\n".join(report)
    log.info(message)
    return message

# =============================================================================
# SCHEDULED TRIGGERS
# =============================================================================

@time_trigger("cron(0 9 * * 0)")  # Sunday 9:00 AM
async def scheduled_weekly_report():
    """Send weekly report on schedule."""
    await heating_weekly_report()

@time_trigger("cron(0 4 * * 0)")  # Sunday 4:00 AM (after daily learning at 3 AM)
async def scheduled_weekly_pid_update():
    """
    Automatically apply recommended PID values weekly for zones in heat mode.
    Runs after the daily adaptive learning to use the latest recommendations.
    """
    zones_config, constants, current_pid, learning_data = _get_configs()
    notification_service = constants['notification']['service']

    log.info("Starting weekly PID auto-update...")

    # Build detailed report
    report_lines = ["Weekly PID Auto-Update", "=" * 30, ""]

    updated_zones = []
    skipped_cool_zones = []
    unchanged_zones = []
    failed_zones = []
    changes_detail = []

    for zone_id, zone_config in zones_config['zones'].items():
        # Skip zones not in heat mode
        if not is_zone_in_heat_mode(zone_config):
            skipped_cool_zones.append(zone_config['display_name'])
            continue

        current = current_pid.get(zone_id, {})
        climate_entity = zone_config['climate_entity']

        # Get recommended PID (adaptive if available, otherwise physics-based)
        zone_learning = learning_data.get(zone_id, {})
        if zone_learning and zone_learning.get('analyzed_events', 0) >= 3:
            recommended = calculate_adaptive_pid_adjustments(
                zone_id, zone_config, constants, current, zone_learning
            )
            source = "adaptive"
        else:
            recommended = calculate_recommended_pid(zone_id, zone_config, constants)
            source = "physics"

        if not recommended:
            continue

        new_kp = recommended['kp']
        new_ki = recommended['ki']
        new_kd = recommended['kd']

        # Check if values are different
        old_kp = current.get('kp')
        old_ki = current.get('ki')
        old_kd = current.get('kd')

        if old_kp == new_kp and old_ki == new_ki and old_kd == new_kd:
            unchanged_zones.append(zone_config['display_name'])
            continue

        try:
            # Apply PID immediately via smart_thermostat service
            service.call("smart_thermostat", "set_pid_gain",
                         entity_id=climate_entity,
                         kp=new_kp,
                         ki=new_ki,
                         kd=new_kd)

            # Clear integral after PID change
            service.call("smart_thermostat", "clear_integral",
                         entity_id=climate_entity)

            updated_zones.append(zone_config['display_name'])
            changes_detail.append({
                'zone': zone_id,
                'display_name': zone_config['display_name'],
                'climate_entity': climate_entity,
                'old': {'kp': old_kp, 'ki': old_ki, 'kd': old_kd},
                'new': {'kp': new_kp, 'ki': new_ki, 'kd': new_kd},
                'source': source
            })

            log.info(f"Zone {zone_id}: Updated PID Kp={old_kp}->{new_kp}, "
                    f"Ki={old_ki}->{new_ki}, Kd={old_kd}->{new_kd} ({source})")

        except Exception as e:
            log.error(f"Failed to apply PID for {zone_id}: {e}")
            failed_zones.append(zone_config['display_name'])

    # Update configuration.yaml for persistence
    if changes_detail:
        try:
            changes_for_yaml = [{
                'zone': c['zone'],
                'climate_entity': c['climate_entity'],
                'old': c['old'],
                'new': c['new']
            } for c in changes_detail]
            _update_configuration_yaml_pid(changes_for_yaml)
        except Exception as e:
            log.error(f"Failed to update configuration.yaml: {e}")

    # Build report
    if updated_zones:
        report_lines.append("Updated zones:")
        for change in changes_detail:
            report_lines.append(f"  {change['display_name']} ({change['source']}):")
            report_lines.append(f"    Kp: {change['old']['kp']} → {change['new']['kp']}")
            report_lines.append(f"    Ki: {change['old']['ki']} → {change['new']['ki']}")
            report_lines.append(f"    Kd: {change['old']['kd']} → {change['new']['kd']}")
        report_lines.append("")

    if unchanged_zones:
        report_lines.append(f"Unchanged (already optimal): {', '.join(unchanged_zones)}")
        report_lines.append("")

    if skipped_cool_zones:
        report_lines.append(f"Skipped (not in heat mode): {', '.join(skipped_cool_zones)}")
        report_lines.append("")

    if failed_zones:
        report_lines.append(f"Failed: {', '.join(failed_zones)}")
        report_lines.append("")

    if not updated_zones and not skipped_cool_zones and not failed_zones:
        report_lines.append("All zones already have optimal PID values.")

    full_message = "\n".join(report_lines)

    # Create persistent notification with full report
    service.call("persistent_notification", "create",
                 message=full_message,
                 title="Weekly PID Update",
                 notification_id="heating_weekly_pid_update")

    # Send mobile notification
    if updated_zones:
        mobile_summary = f"PID updated for {len(updated_zones)} zone(s):\n{', '.join(updated_zones)}"
        if skipped_cool_zones:
            mobile_summary += f"\n({len(skipped_cool_zones)} skipped - not heating)"
    elif skipped_cool_zones and len(skipped_cool_zones) == len(zones_config['zones']):
        mobile_summary = "No PID updates - all zones in cooling mode"
    else:
        mobile_summary = "No PID changes needed - all zones optimal"

    service.call("notify", notification_service,
                 message=mobile_summary,
                 title="Weekly PID Update",
                 data={"push": {"interruption-level": "passive"}})

    # Reload current PID values and update sensors
    global _current_pid
    _current_pid = load_current_pid_values()
    await update_current_pid_sensors()

    log.info(f"Weekly PID update complete. Updated: {len(updated_zones)}, "
            f"Unchanged: {len(unchanged_zones)}, Skipped: {len(skipped_cool_zones)}")

    return full_message

# =============================================================================
# STARTUP
# =============================================================================

@time_trigger("startup")
async def startup():
    """Initialize sensors on startup."""
    log.info("Heating Services starting...")

    # Load configs
    global _zones_config, _constants_config, _current_pid, _learning_data
    _zones_config = load_zones_config()
    _constants_config = load_constants_config()
    _current_pid = load_current_pid_values()
    _learning_data = load_learning_data()

    log.info(f"Loaded {len(_zones_config['zones'])} zones")
    log.info(f"Loaded PID values for {len(_current_pid)} zones from configuration.yaml")
    log.info(f"Loaded learning data for {len(_learning_data)} zones")

    # Verify recorder is available for history access
    try:
        instance = get_instance(hass)
        if instance is None:
            log.warning(
                "Recorder component not available. "
                "Adaptive learning will not work until recorder is enabled."
            )
        else:
            log.info("Recorder component detected - history access enabled")
    except Exception as e:
        log.warning(f"Could not verify recorder availability: {e}")

    # Initialize sensors
    await update_current_pid_sensors()
    await update_recommended_pid_sensors()
    await update_learning_sensors()
    await update_performance_sensors()
    await update_cost_sensor()

    log.info("Heating Services initialized (adaptive learning enabled)")
