# HASmartThermostat Fork: Adaptive Heating System

## Overview

Fork HASmartThermostat to create an integrated adaptive heating controller that combines:
- HASmartThermostat's proven PID/PWM control
- Your PyScript system's analytics and adaptive learning
- New features: pre-heating, heating curves, zone linking, vacation mode

**Repository name suggestion:** `ha-adaptive-thermostat` or `HASmartThermostat-Adaptive`

---

## Feature Summary

### Keep from HASmartThermostat
- [x] PID control (Kp, Ki, Kd)
- [x] PWM control with configurable period
- [x] Weather compensation (ke parameter)
- [x] Preset modes (Away, Eco, Boost, Comfort, Home, Sleep, Activity)
- [x] Hot/cold tolerance bands
- [x] Sensor stall detection
- [x] Min cycle duration
- [x] `set_pid_gain`, `set_preset_temp`, `clear_integral` services

### Remove from HASmartThermostat
- [ ] Autotune (PIDAutotune class) - replaced by adaptive learning

### Migrate from PyScript
- [ ] Adaptive PID learning (7-day window, overshoot/settling/oscillation analysis)
- [ ] Physics-based PID baseline (thermal time constant + Ziegler-Nichols)
- [ ] Zone-specific adjustments (kitchen/bathroom/bedroom rules)
- [ ] Health monitoring (short cycles, high power, sensor checks)
- [ ] Performance sensors (duty_cycle, power_m2, cycle_time)
- [ ] Energy/cost tracking (GJ meter + duty-cycle estimation)
- [ ] Heat output calculation (supply/return delta-T)
- [ ] Weekly reports via notification

### New Features
- [ ] Pre-heating algorithm
- [ ] Heating curves (outdoor temp -> output adjustment)
- [ ] Zone linking (coordinate thermally connected zones)
- [ ] Vacation mode
- [ ] Auto-learn thermal rates (cooling/heating C/hour from observed data)
- [ ] Output entity (single demand switch per zone for valve control)
- [ ] Central heat source controller (aggregates zone demand, controls main heater/cooler with optional delay)
- [ ] Mode synchronization (switching one zone to heat/cool syncs all zones, OFF is independent)

---

## Architecture

```
custom_components/adaptive_thermostat/
├── __init__.py                 # Integration setup, coordinator
├── climate.py                  # AdaptiveThermostat entity (extends SmartThermostat)
├── const.py                    # Constants + new config options
├── coordinator.py              # NEW: DataUpdateCoordinator for cross-zone logic
├── pid_controller/
│   └── __init__.py             # PID class (keep), remove PIDAutotune
├── adaptive/
│   ├── __init__.py
│   ├── learning.py             # Adaptive learning engine (from PyScript)
│   ├── physics.py              # Thermal time constant, Ziegler-Nichols
│   ├── zone_rules.py           # Zone-specific adjustment rules
│   └── preheating.py           # Pre-heating algorithm
├── analytics/
│   ├── __init__.py
│   ├── performance.py          # Duty cycle, power, cycle time
│   ├── energy.py               # GJ meter, cost tracking
│   ├── heat_output.py          # Supply/return delta-T calculation
│   └── health.py               # Health monitoring
├── services.yaml               # Extended services
├── sensor.py                   # NEW: Sensor platform for analytics
├── switch.py                   # NEW: Zone demand switches + central pump controller
├── manifest.json
└── translations/
    └── en.json
```

---

## Implementation Plan

### Phase 1: Fork and Restructure

1. **Fork HASmartThermostat repository**
   - Clone from `ScratMan/HASmartThermostat`
   - Rename to `adaptive_thermostat`
   - Update manifest.json (name, domain, version)

2. **Remove Autotune**
   - Delete `PIDAutotune` class from `pid_controller/__init__.py`
   - Remove autotune configuration options from `const.py`
   - Remove autotune service registration from `__init__.py`
   - Keep `PID` class intact

3. **Add DataUpdateCoordinator**
   - Create `coordinator.py` for cross-zone state management
   - Required for zone linking and system-wide analytics
   - Polls all climate entities on configurable interval

### Phase 2: Migrate Adaptive Learning

4. **Create `adaptive/learning.py`**
   - Port `analyze_cycles()` from PyScript
   - Port `analyze_heating_response()`
   - Port `calculate_adaptive_pid_adjustments()`
   - Port `run_adaptive_learning()`
   - Use HA recorder for history instead of PyScript history access

5. **Create `adaptive/physics.py`**
   - Port `calculate_thermal_time_constant()`
   - Port `calculate_recommended_pid()` (Ziegler-Nichols)
   - Zone configuration: area_m2, volume_m3 (static inputs)
   - **Auto-learn thermal rates:**
     - `cool_rate_c_per_hour`: measure temp drop when heating OFF
     - `heat_rate_c_per_hour`: measure temp rise when heating ON
     - Average over multiple cycles, store in learning data
     - Use learned rates for pre-heating timing and physics-based PID

6. **Create `adaptive/zone_rules.py`**
   - Port zone-specific adjustment logic
   - Kitchen: lower Ki (oven/door disturbances)
   - Bathroom: higher Kp (skylight heat loss)
   - Bedroom: lower Ki (night ventilation)
   - Ground floor: higher Ki (exterior doors)
   - Make rules configurable per-zone in YAML

7. **Integrate learning into climate entity**
   - Add `async_run_learning()` method to climate.py
   - Schedule daily learning (3:00 AM) via `async_track_time_change`
   - Store learning data in entity extra_state_attributes
   - Optionally persist to `.storage/` JSON

### Phase 3: Migrate Analytics

8. **Create sensor platform (`sensor.py`)**
   - Performance sensors per zone:
     - `sensor.{zone}_duty_cycle`
     - `sensor.{zone}_power_m2`
     - `sensor.{zone}_cycle_time`
   - Learning metrics per zone:
     - `sensor.{zone}_overshoot`
     - `sensor.{zone}_settling_time`
     - `sensor.{zone}_oscillations`
   - System sensors:
     - `sensor.heating_system_health`
     - `sensor.heating_total_power`
     - `sensor.heating_weekly_cost`

9. **Create `analytics/performance.py`**
   - Port duty cycle calculation
   - Port power per m2 estimation
   - Port cycle time analysis

10. **Create `analytics/energy.py`**
    - Port GJ meter integration
    - Port cost calculation
    - Port weekly cost aggregation

11. **Create `analytics/heat_output.py`**
    - Port supply/return temperature tracking
    - Port flow rate handling
    - Port kW calculation: `flow_rate * 1.163 * delta_T`

12. **Create `analytics/health.py`**
    - Port health check logic
    - Short cycle detection (<10 min critical, <15 min warning)
    - High power detection (>20 W/m2)
    - Sensor availability checks
    - Exception zones (bathroom high power OK)

13. **Create switch platform (`switch.py`)**

    **Zone demand switches:**
    - `switch.{zone}_demand` - ON when zone needs conditioning (heat OR cool)
    - Driven by PID output: demand > 0 = ON, demand = 0 = OFF
    - Controls zone valve/actuator - opens when zone is active

    **Central heat source controller:**
    - Controls main heater switch (boiler, heat pump, etc.) based on aggregate demand
    - Controls main cooler switch (if applicable) based on aggregate cooling demand
    - `main_heater_switch`: ON when ANY zone demands heat
    - `main_cooler_switch`: ON when ANY zone demands cooling
    - **Startup delay**: wait X seconds after first zone demands before firing heat source (allows valves to open)
    - **Immediate off**: when no zones have demand, turn off heat source immediately
    - Logic: `heater_on = any(zone.heating_demand for zone in zones)`

    **Mode synchronization:**
    - When one zone switches to HEAT mode -> all zones switch to HEAT
    - When one zone switches to COOL mode -> all zones switch to COOL
    - Switching a zone to OFF does NOT affect other zones
    - Optional: can be disabled per-zone if needed
    - Prevents conflicting modes (some zones heating while others cooling)

### Phase 4: New Features

14. **Create `adaptive/preheating.py`**
    - Calculate time-to-target based on thermal time constant
    - Monitor schedule entities for upcoming setpoint changes
    - Trigger early heating: `preheat_hours = (target - current) / heat_rate`
    - Integrate with HA scheduler or input_datetime helpers
    - Add `preheat_enabled` config option per zone

15. **Add heating curves to PID controller**
    - Modify `pid_controller/__init__.py`
    - Add `heating_curve` parameter: outdoor temp -> output multiplier
    - Example: at 10C outdoor, multiply output by 0.7
    - Configurable curve points in YAML

16. **Add zone linking to coordinator**
    - Track thermally connected zones (e.g., kitchen + living room)
    - Coordinate heating cycles to prevent oscillation
    - If zone A is heating, delay zone B heating by X minutes
    - Configuration: `linked_zones: [climate.kitchen, climate.living_room]`

17. **Add vacation mode**
    - New preset mode or separate toggle
    - Sets all zones to frost protection (configurable, default 12C)
    - Pauses adaptive learning
    - Optionally notify on temperature anomalies
    - Service: `adaptive_thermostat.set_vacation_mode`

### Phase 5: Services and Notifications

18. **Extend services.yaml**
    ```yaml
    adaptive_thermostat.run_learning:
      description: Trigger adaptive learning analysis

    adaptive_thermostat.apply_recommended_pid:
      description: Apply learned PID values to zone

    adaptive_thermostat.health_check:
      description: Run health check and send alerts

    adaptive_thermostat.weekly_report:
      description: Generate and send weekly report

    adaptive_thermostat.cost_report:
      description: Generate energy cost report

    adaptive_thermostat.set_vacation_mode:
      description: Enable/disable vacation mode
      fields:
        enabled: boolean
        target_temp: float (default 12)
    ```

19. **Add notification integration**
    - Configure notify service in integration options
    - Health alerts: time-sensitive interruption
    - Reports: passive interruption
    - Use persistent_notification as fallback

### Phase 6: Configuration Schema

20. **Extend configuration options**
    ```yaml
    climate:
      - platform: adaptive_thermostat
        name: Ground Floor
        heater: switch.heating_gf
        target_sensor: sensor.temp_gf
        outdoor_sensor: sensor.outdoor_temp

        # Existing HST options
        kp: 0.5
        ki: 0.01
        kd: 5
        ke: 0.6

        # Zone properties (for physics-based tuning)
        area_m2: 28
        volume_m3: 70  # or auto-calculate from area + ceiling_height
        zone_type: ground_floor  # kitchen, bathroom, bedroom, etc.
        # NOTE: cool_rate and heat_rate are AUTO-LEARNED, not configured

        # Output switch (for zone valve/actuator)
        demand_switch: switch.valve_gf  # optional - ON when zone needs heat/cool

        # Adaptive learning
        learning_enabled: true
        learning_window_days: 7
        min_learning_events: 3

        # Pre-heating
        preheat_enabled: true
        schedule_entity: schedule.heating_gf  # or input_datetime

        # Health monitoring
        health_alerts_enabled: true
        min_cycle_time_warning: 15
        min_cycle_time_critical: 10
        max_power_m2: 20
        high_power_exception: false  # true for bathroom

        # Zone linking
        linked_zones:
          - climate.kitchen
          - climate.living_room
        link_delay_minutes: 10

        # Energy tracking
        gj_meter_entity: sensor.heating_gj
        gj_cost_entity: input_number.gj_price

        # Heat output
        supply_temp_sensor: sensor.heating_supply
        return_temp_sensor: sensor.heating_return
        flow_rate_sensor: sensor.heating_flow  # optional
        fallback_flow_rate: 0.5  # m3/h

    # System-level configuration (separate from per-zone)
    adaptive_thermostat:
      # Central heat source control
      main_heater_switch: switch.boiler  # or heat pump - ON when any zone needs heat
      main_cooler_switch: switch.ac_compressor  # optional - ON when any zone needs cooling
      source_startup_delay: 30  # seconds to wait before firing after first zone demands (allows valves to open)

      # Mode synchronization
      sync_modes: true  # when one zone switches to heat/cool, all zones follow

      # Notifications
      notify_service: notify.mobile_app  # for alerts and reports
    ```

---

## File Modifications Summary

| File | Action | Description |
|------|--------|-------------|
| `manifest.json` | Modify | Rename, update version |
| `const.py` | Modify | Add new config constants, remove autotune |
| `__init__.py` | Modify | Add coordinator, new services, remove autotune |
| `climate.py` | Modify | Add learning integration, preheat hooks |
| `pid_controller/__init__.py` | Modify | Remove PIDAutotune, add heating curve |
| `coordinator.py` | Create | Cross-zone coordination |
| `sensor.py` | Create | Analytics sensors |
| `switch.py` | Create | Heating/cooling demand output switches |
| `adaptive/*.py` | Create | Learning, physics, zone rules, preheating |
| `analytics/*.py` | Create | Performance, energy, heat output, health |
| `services.yaml` | Modify | Add new services |

---

## Testing Plan

1. **Unit tests**
   - PID controller with heating curves
   - Thermal time constant calculations
   - Adaptive adjustment rules
   - Pre-heating timing calculations

2. **Integration tests**
   - Zone linking coordination
   - Learning data persistence
   - Sensor creation and updates
   - Service calls

3. **Manual testing in HA**
   - Install fork in test HA instance
   - Configure 2-3 zones
   - Verify sensors appear
   - Trigger learning manually
   - Test vacation mode
   - Verify notifications

4. **Migration testing**
   - Install alongside existing PyScript
   - Compare recommended PID values
   - Gradually migrate zones

---

## Migration Path

1. Install fork as new integration (different domain)
2. Run parallel with existing HASmartThermostat + PyScript
3. Compare outputs for 1-2 weeks
4. Migrate zones one at a time
5. Remove PyScript module after full migration
6. Remove original HASmartThermostat

---

## Estimated Scope

- **~16 new/modified files**
- **~2500-3500 lines of new code** (mostly ported from PyScript)
- **Phases 1-3**: Core functionality + output switches (MVP)
- **Phases 4-6**: New features (pre-heating, curves, linking, vacation) and polish

## Key Design Decisions

1. **Thermal rates auto-learned** - No manual cool_rate/heat_rate config; system observes and learns
2. **Demand switch separate from PWM** - Single demand switch per zone (valve control) is distinct from PWM heater cycling
3. **Central heat source control** - Aggregates zone demands, controls main heater/cooler with startup delay (valves open first)
4. **Mode synchronization** - Switching one zone to heat/cool mode syncs all zones; OFF is independent
5. **Learning data in HA storage** - Uses `.storage/` for persistence, not external JSON files
6. **Single integration** - Replaces both HASmartThermostat and PyScript module
