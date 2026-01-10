# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant PyScript module for advanced heating system management and optimization. It manages a multi-zone floor heating system in a high-efficiency (A+++) house with 7 zones across 3 floors (136 m²), providing real-time performance monitoring, adaptive PID tuning recommendations, energy cost tracking, and health monitoring.

## Development Commands

**No formal build system.** This is a pyscript module for Home Assistant:

- **Install**: Copy `/pyscript/` folder to Home Assistant `/config/` directory
- **Restart**: Home Assistant restart or reload pyscript integration
- **Test manually**: Call services via Home Assistant Developer Tools > Services
- **Trigger learning**: `service: pyscript.heating_run_learning`
- **View logs**: Home Assistant logs at `/config/home-assistant.log` or via UI

**Configuration files:**
- `config/zones.yaml` - Zone definitions (7 zones: gf, kitchen, living_room, bedroom, bathroom, study, hallway)
- `config/constants.yaml` - System constants (update `notification_service` before first use)
- `config/learning_data.json` - Auto-generated adaptive learning data
- `/config/configuration.yaml` - Source of truth for current PID values (HASmartThermostat entries)

**Dashboard installation:**
- UI method: Settings > Dashboards > Add Dashboard > Raw configuration editor > paste `dashboard/heating.yaml`
- YAML mode: Add dashboard reference to `/config/configuration.yaml` lovelace section

## Architecture Overview

### Core Components

**heating_services.py** (1,267 lines) - Main module with all logic:

1. **File I/O Layer** (`@pyscript_executor` decorators)
   - Async file operations without blocking event loop
   - YAML parsing with Home Assistant tag handlers (`!include`, `!secret`)
   - JSON persistence for learning data

2. **Thermal Calculations**
   - `calculate_thermal_time_constant()` - Estimates system responsiveness from zone volume and cooling rate
   - `calculate_recommended_pid()` - Physics-based PID tuning using modified Ziegler-Nichols method

3. **History Analysis**
   - `analyze_cycles()` - Heating on/off cycles (duty cycle, average power)
   - `analyze_heating_response()` - Temperature response (overshoot, settling time, oscillations)
   - Note: History access is limited in pyscript environment (documented limitation)

4. **Adaptive Learning Engine**
   - `calculate_adaptive_pid_adjustments()` - Rule-based PID adjustments from learned metrics
   - `run_adaptive_learning()` - Daily analysis (3:00 AM) of last 20 cycles within 7-day window
   - Requires ≥3 analyzed events before making adaptive recommendations
   - Metrics: overshoot, undershoot, settling time, oscillation count, rise time

5. **Sensor Management** (~25+ Home Assistant sensors)
   - Performance sensors (power_m2, cycle_time, duty_cycle per zone)
   - Current PID sensors (reads from `/config/configuration.yaml`)
   - Recommended PID sensors (adaptive + physics-based)
   - Learning metrics sensors
   - System health sensor

6. **Services** (`@service` decorators)
   - `heating_run_learning` - Manual learning trigger
   - `heating_weekly_report` - Weekly performance report
   - `heating_health_check` - Health monitoring
   - `heating_pid_recommendations` - PID tuning suggestions
   - `heating_cost_report` - Energy cost breakdown

7. **Scheduled Tasks** (`@time_trigger` decorators)
   - Every 5 min: Performance sensor updates
   - Hourly: Cost sensor updates
   - Every 6 hours: Health monitoring
   - Daily 3:00 AM: Adaptive learning
   - Sunday 9:00 AM: Weekly report
   - Startup: Initialize all sensors

### Architecture Patterns

**Configuration as Code (YAML)**
- Single source of truth: `zones.yaml` (zone definitions) + `constants.yaml` (system parameters)
- Centralized settings for thresholds, PID limits, notification service

**Async Event-Driven Architecture**
- `@time_trigger`: Scheduled tasks with cron expressions
- `@state_trigger`: Event-based triggers (GJ cost changes)
- `@service`: Callable services from HA UI
- `@pyscript_executor`: Blocking I/O without event loop blocking

**Multi-Tier Analytics Pipeline**
1. Data Collection: Analyze heating cycles and temperature history
2. Feature Extraction: Compute overshoot, settling time, oscillations
3. Adaptive Tuning: Apply rule-based PID adjustments
4. Persistence: Store learned data in JSON
5. Reporting: Generate weekly/cost reports

**Physics-Based + Data-Driven Hybrid Tuning**
- Physics layer: Thermal time constant → baseline PID (Ziegler-Nichols)
- Adaptive layer: Learned metrics → adjust recommended PID
- Graceful degradation: Falls back to physics-based when insufficient data

**Health Monitoring with Alerts**
- Thresholds: cycle time (<10 min critical, <15 min warning), power (>20 W/m²), sensor availability
- Notification service integration via Home Assistant mobile app
- Health status badge (healthy/warning/critical)

### Data Flow

```
Home Assistant State (climate, switches, sensors)
    ↓
heating_services.py (async event loop)
    ↓
Analyze Cycles → Calculate Power/Duty Cycle
    ↓
Analyze Heating Response → Extract Metrics
    ↓
Calculate Adaptive PID Adjustments
    ↓
Create/Update Sensors + Send Notifications
    ↓
Persist Learning Data (learning_data.json)
    ↓
Dashboard Visualization + Weekly Reports
```

### Key Technical Details

**PID Learning Rules:**
- High overshoot (>0.5°C): Reduce Kp by up to 15%, reduce Ki
- Moderate overshoot (>0.2°C): Reduce Kp by 5%
- Slow response (>60 min): Increase Kp by 10%
- Undershoot (>0.3°C): Increase Ki by up to 20%
- Many oscillations (>3): Reduce Kp by 10%, increase Kd by 20%
- Some oscillations (>1): Increase Kd by 10%
- Slow settling (>90 min): Increase Kd by 15%

**Zone-Specific Adjustments:**
- Kitchen: Lower Ki (oven/door disturbances)
- Bathroom: Higher Kp (skylight heat loss)
- Bedroom: Lower Ki (night ventilation)
- Ground Floor: Higher Ki (exterior doors)

**Cost Calculation:**
- Primary: GJ meter sensor (cumulative consumption)
- Fallback: Duty-cycle estimation with configurable conversion factors

**Defensive Configuration:**
- Graceful handling of missing sensors/entities
- Fallback values for exception zones (`high_power_exception_zones`)
- Error logging without crashes
- HA-specific YAML tag handlers

## Important Constraints

**PyScript Environment Limitations:**
- History access is limited (documented in code comments)
- File I/O must use `@pyscript_executor` decorator to avoid blocking event loop
- Cannot directly modify `/config/configuration.yaml` (PID values must be manually updated)
- Notification service name must match HA configuration

**A+++ House Constraints:**
- Conservative PID tuning for high-efficiency homes
- Lower power thresholds than typical heating systems
- Slower response characteristics due to high insulation

## Making Changes

**When modifying PID logic:**
- Update both `calculate_recommended_pid()` (physics-based) and `calculate_adaptive_pid_adjustments()` (adaptive)
- Ensure PID limits from `constants.yaml` are respected
- Test with multiple zones (different thermal characteristics)

**When adding new sensors:**
- Follow naming convention: `sensor.heating_<zone>_<metric>`
- Update `create_sensors()` function
- Add to dashboard if user-facing

**When modifying learning logic:**
- Update `learning_data.json` schema documentation
- Ensure backward compatibility with existing learning data
- Minimum 3 analyzed events required before adaptive recommendations

**When changing scheduled tasks:**
- Use cron syntax for `@time_trigger` decorators
- Avoid overlapping resource-intensive tasks
- Consider HA restart time (sensors initialized on startup)

**YAML Configuration:**
- Use snake_case for keys
- Include units in key names (e.g., `area_m2`, `cool_rate_c_per_hour`)
- Validate YAML syntax before reloading pyscript
