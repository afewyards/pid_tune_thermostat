# HASmartThermostat Fork: Adaptive Heating System

## Overview

Fork HASmartThermostat to create an integrated adaptive heating controller that combines:
- HASmartThermostat's proven PID/PWM control
- Your PyScript system's analytics and adaptive learning
- New features: pre-heating, heating curves, zone linking, vacation mode

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

### Remove from HASmartThermostat
- [ ] Autotune (PIDAutotune class) - replaced by adaptive learning

### Migrate from PyScript
- [ ] Adaptive PID learning (7-day window, overshoot/settling/oscillation analysis)
- [ ] Physics-based PID baseline (thermal time constant + Ziegler-Nichols)
- [ ] Health monitoring (short cycles, high power, sensor checks)
- [ ] Performance sensors (duty_cycle, power_m2, cycle_time)
- [ ] Energy/cost tracking (GJ meter + duty-cycle estimation)
- [ ] Heat output calculation (supply/return delta-T)
- [ ] Weekly reports via notification

### New Features
- [ ] Built-in scheduling with presets (wake, away, home, sleep)
- [ ] Pre-heating algorithm (uses built-in schedule to know upcoming changes)
- [ ] Heating curves (outdoor temp -> output adjustment)
- [ ] Zone linking (coordinate thermally connected zones)
- [ ] Vacation mode
- [ ] Auto-learn thermal rates (cooling/heating C/hour from observed data)
- [ ] Output entity (single demand switch per zone for valve control)
- [ ] Central heat source controller (aggregates zone demand, controls main heater/cooler with optional delay)
- [ ] Mode synchronization (switching one zone to heat/cool syncs all zones, OFF is independent)
- [ ] Heating type per zone (floor_hydronic, radiator, convector, etc.) for response characteristics
- [ ] PWM period auto-tuning based on thermal response and heating type
- [ ] Solar gain compensation (auto-learn sun impact per zone, season-aware, uses weather forecast)

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
│   └── preheating.py           # Pre-heating algorithm
├── scheduling/
│   ├── __init__.py
│   └── scheduler.py            # Built-in schedule with presets
├── solar/
│   ├── __init__.py
│   └── solar_gain.py           # Solar gain learning and compensation
├── analytics/
│   ├── __init__.py
│   ├── performance.py          # Duty cycle, power, cycle time
│   ├── energy.py               # GJ meter, cost tracking
│   ├── heat_output.py          # Supply/return delta-T calculation
│   └── health.py               # Health monitoring
├── services.yaml               # Extended services
├── sensor.py                   # NEW: Sensor platform for analytics
├── switch.py                   # NEW: Zone demand switches + central controller
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
   - **Zone inputs:**
     - `area_m2`: floor area
     - `ceiling_height`: auto-calculate volume (area × height)
     - `window_area_m2`: affects heat loss estimate
     - `window_orientation`: solar gain factor (south > west > east > north)
     - `heating_type`: response characteristics lookup
       - floor_hydronic: tau=2-3h, PWM=30-60min, conservative PID
       - floor_electric: tau=30-60min, PWM=15-30min, conservative PID
       - radiator: tau=15-30min, PWM=10-20min, moderate PID
       - convector: tau=5-15min, PWM=5-10min, aggressive PID
       - ceiling: tau=1-2h, PWM=20-40min, conservative PID
   - **System inputs:**
     - `house_energy_rating`: baseline heat loss coefficient (A+++ = low, G = high)
   - **Estimated heat loss model:**
     - `base_loss = f(energy_rating, area, volume, window_area)`
     - Used for initial PID values before learning
   - **Auto-learn thermal rates:**
     - `cool_rate_c_per_hour`: measure temp drop when heating OFF
     - `heat_rate_c_per_hour`: measure temp rise when heating ON
     - Average over multiple cycles, store in learning data
     - Validate learned rates against physics estimate
     - Use learned rates for pre-heating timing and physics-based PID
   - **PWM auto-tuning:**
     - Initial PWM = f(heating_type, volume, energy_rating)
     - Refine based on observed behavior:
       - Short cycling detected → increase PWM period
       - Excessive oscillations → decrease PWM period
     - Track valve cycle count for wear optimization

6. **Integrate learning into climate entity**
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

14. **Create `scheduling/` module**
    - Built-in schedule per zone with presets (wake, away, home, sleep)
    - Weekday/weekend schedules + per-day overrides
    - Query "next transition" for pre-heating
    - Apply setpoint changes automatically at scheduled times
    - Expose schedule via HA calendar entity (optional)

15. **Create `adaptive/preheating.py`**
    - Query built-in schedule for next setpoint change
    - Calculate time-to-target based on learned heat rate
    - Trigger early heating: `preheat_hours = (target - current) / heat_rate`
    - Cap at `preheat_max_hours` to avoid excessive early starts
    - Skip if zone is already at or above target

16. **Add heating curves to PID controller**
    - Modify `pid_controller/__init__.py`
    - Add `heating_curve` parameter: outdoor temp -> output multiplier
    - Example: at 10C outdoor, multiply output by 0.7
    - Configurable curve points in YAML

17. **Add zone linking to coordinator**
    - Track thermally connected zones (e.g., kitchen + living room)
    - Coordinate heating cycles to prevent oscillation
    - If zone A is heating, delay zone B heating by X minutes
    - Configuration: `linked_zones: [climate.kitchen, climate.living_room]`

18. **Create `solar/solar_gain.py`**
    - Auto-learn solar gain per zone based on:
      - `window_orientation`: when sun hits (east=morning, south=midday, west=afternoon)
      - `window_area_m2`: relative impact
      - `sun.sun` entity: elevation and azimuth
      - Season/month: sun angle changes throughout year
    - Learn by comparing sunny vs cloudy days at same time
    - Measure unexpected temp rise not from heating = solar gain
    - Store learned data segmented by:
      - Window orientation
      - Season (or sun elevation range)
      - Time of day
    - Use weather forecast to predict solar gain
    - Reduce heating output or setpoint during expected sunny periods
    - Delay pre-heating when sun will help

19. **Add vacation mode**
    - New preset mode or separate toggle
    - Sets all zones to frost protection (configurable, default 12C)
    - Pauses adaptive learning
    - Optionally notify on temperature anomalies
    - Service: `adaptive_thermostat.set_vacation_mode`

### Phase 5: Services and Notifications

20. **Extend services.yaml**
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

21. **Add notification integration**
    - Configure notify service in integration options
    - Health alerts: time-sensitive interruption
    - Reports: passive interruption
    - Use persistent_notification as fallback

### Phase 6: Configuration Schema

22. **Extend configuration options**
    ```yaml
    climate:
      - platform: adaptive_thermostat
        name: Ground Floor
        heater: switch.heating_gf
        target_sensor: sensor.temp_gf
        outdoor_sensor: sensor.outdoor_temp

        # Zone properties (for physics-based tuning)
        area_m2: 28
        ceiling_height: 2.5  # meters - volume auto-calculated (area × height)
        window_area_m2: 4.0  # optional - affects heat loss calculations
        window_orientation: south  # optional - north/east/south/west for solar gain
        zone_type: ground_floor  # kitchen, bathroom, bedroom, etc.
        heating_type: floor_hydronic  # floor_hydronic, floor_electric, radiator, convector, ceiling
        # NOTE: cool_rate and heat_rate are AUTO-LEARNED, not configured

        # PWM settings
        pwm_period: auto  # auto-tuned, or fixed value in seconds
        min_pwm_period: 300  # 5 min minimum (protects actuators)
        max_pwm_period: 3600  # 60 min maximum

        # Output switch (for zone valve/actuator)
        demand_switch: switch.valve_gf  # optional - ON when zone needs heat/cool

        # Adaptive learning
        learning_enabled: true
        learning_window_days: 7
        min_learning_events: 3

        # Built-in schedule with presets
        presets:
          wake: 21
          away: 17
          home: 21
          sleep: 19
        schedule:
          weekday:  # mon-fri
            - time: "06:30"
              preset: wake
            - time: "08:30"
              preset: away
            - time: "17:00"
              preset: home
            - time: "22:30"
              preset: sleep
          weekend:  # sat-sun
            - time: "08:00"
              preset: wake
            - time: "23:00"
              preset: sleep
          # Can also specify per day: monday, tuesday, etc.

        # Pre-heating (uses built-in schedule)
        preheat_enabled: true
        preheat_max_hours: 3  # don't start more than 3h early

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

        # Optional: manual PID override (disables auto-tuning for this zone)
        # pid_override:
        #   kp: 0.5
        #   ki: 0.01
        #   kd: 5
        #   ke: 0.6

    # System-level configuration (separate from per-zone)
    adaptive_thermostat:
      # House properties (for physics-based tuning baseline)
      house_energy_rating: A+++  # A+++, A++, A+, A, B, C, D, E, F, G
      # Used for initial PID estimates before learning data available

      # Solar gain compensation
      weather_entity: weather.home  # for forecast (sunny/cloudy)
      # sun.sun entity used automatically for elevation/azimuth

      # System heat output sensors - all optional
      supply_temp_sensor: sensor.heating_supply  # optional - enables heat output (kW) calc
      return_temp_sensor: sensor.heating_return  # optional - enables delta-T monitoring
      flow_rate_sensor: sensor.heating_flow  # optional - real-time flow (L/h or m3/h)
      volume_meter_entity: sensor.heating_volume_m3  # optional - cumulative m3 from city heating
      fallback_flow_rate: 0.5  # m3/h when neither flow sensor nor volume meter available
      # If both flow + volume present: flow for real-time, volume for accurate totals
      # Can cross-validate sensors (volume delta vs integrated flow)

      # Energy metering - all optional
      gj_meter_entity: sensor.heating_gj  # optional - enables actual energy tracking
      gj_cost_entity: input_number.gj_price  # optional - enables cost reports (EUR per GJ)

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
| `adaptive/*.py` | Create | Learning, physics, preheating |
| `analytics/*.py` | Create | Performance, energy, heat output, health |
| `services.yaml` | Modify | Add new services |

---

## Testing Plan

### 1. Unit Tests

**Physics module:**
- Thermal time constant calculation from volume + energy rating
- Initial PID estimates from heating type (floor vs radiator vs convector)
- Heat loss estimation from area + windows + energy rating
- PWM period calculation from heating type + thermal mass

**Adaptive learning:**
- Overshoot/undershoot detection from temperature history
- Settling time and oscillation counting
- PID adjustment rules (high overshoot → reduce Kp, etc.)
- Thermal rate learning (cooling/heating C/hour)
- Setpoint change exclusion (events near schedule changes filtered)

**Scheduling:**
- Parse weekday/weekend/per-day schedules
- Get current preset for given time
- Get next transition time and target preset
- Handle midnight rollover
- Handle schedule with no transitions (constant setpoint)

**Pre-heating:**
- Time-to-target calculation from learned heat rate
- Query scheduler for next setpoint change
- Calculate preheat start time
- Respect preheat_max_hours cap
- Skip if already at target

**Solar gain:**
- Calculate sun window per orientation (east=6-11, south=10-15, west=14-19)
- Adjust for season (sun elevation affects gain)
- Learn gain from sunny vs cloudy comparison
- Predict gain from weather forecast
- Reduce heating during expected solar gain
- Delay pre-heating when sun will help

**Analytics:**
- Duty cycle calculation
- Power per m2 estimation
- Heat output (kW) from flow + delta-T
- Energy from volume meter delta
- Flow rate derivation from volume changes
- Cross-validation between flow sensor and volume meter

**Central heat source controller:**
- No zones demanding → heater OFF
- One zone demands → startup delay timer starts
- Startup delay expires → heater ON
- Second zone demands during delay → no effect (timer continues)
- All zones stop demanding → heater OFF immediately (no delay)
- Zone demands during heater ON → no state change
- Startup delay configurable (0 = immediate on)
- Heater and cooler independent (can have different delays)

**Mode synchronization:**
- Zone switches HEAT → all other zones switch to HEAT
- Zone switches COOL → all other zones switch to COOL
- Zone switches OFF → only that zone turns off, others unchanged
- Multiple zones OFF, one switches HEAT → all ON zones switch to HEAT
- Sync disabled per-zone respects setting
- Mode change during startup delay handled correctly

**Demand switches:**
- PID output > 0 → demand switch ON
- PID output = 0 → demand switch OFF
- Demand switch state change triggers central controller update
- Multiple zones demanding simultaneously handled

### 2. Integration Tests

**Zone coordination:**
- Mode synchronization (one zone to HEAT → all zones HEAT)
- Zone linking delays (kitchen heating → delay living room)
- Central heat source aggregation (any demand → heater ON)
- Startup delay (valves open before heat source fires)

**Sensor/switch creation:**
- Demand switch per zone created and responds to PID output
- All analytics sensors appear with correct values
- Health sensor updates status correctly

**Learning persistence:**
- Learning data survives HA restart
- Learned thermal rates stored and retrieved
- PWM period adjustments persisted

**Service calls:**
- `run_learning` triggers analysis
- `apply_recommended_pid` updates values
- `set_vacation_mode` sets all zones to frost protection
- `health_check` sends correct alerts

### 3. Manual Testing in HA

**Initial setup:**
- Install fork in test HA instance
- Configure 2-3 zones with different heating types
- Verify all sensors appear in entity registry
- Check initial PID/PWM values based on config

**Cold start behavior:**
- New zone with no history uses physics-based PID
- PWM period matches heating type defaults
- Pre-heating disabled until thermal rates learned

**Learning cycle:**
- Trigger manual learning, verify metrics calculated
- Wait for daily learning (3:00 AM), check auto-updates
- Verify learned thermal rates (cooling/heating C/hour)
- Check PWM period adjusts based on observed cycling

**Demand and heat source:**
- Zone calls for heat → demand switch ON
- First zone demand → startup delay → main heater ON
- All zones satisfied → main heater OFF immediately
- Mode sync: switch one zone to COOL, verify all follow

**Optional sensors:**
- Test with/without supply/return temps
- Test with/without flow sensor
- Test with/without volume meter
- Test with/without GJ meter
- Verify graceful degradation (features disabled, no errors)

**Vacation mode:**
- Activate vacation, verify all zones to frost protection
- Confirm learning paused during vacation
- Deactivate, verify zones resume normal operation

**Notifications:**
- Health alert triggered on short cycling
- Weekly report delivered on schedule
- Cost report includes actual vs estimated

### 4. Migration Testing

**Parallel operation:**
- Install fork alongside existing HASmartThermostat + PyScript
- Both systems observe same zones
- Compare recommended PID values (should be similar)

**Gradual migration:**
- Migrate one zone to fork, others remain on original
- Verify no conflicts between systems
- Monitor for 1-2 weeks before migrating next zone

**Validation:**
- Compare duty cycles between systems
- Compare learned metrics
- Verify cost/energy calculations match

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

1. **PID auto-tuned by default** - No manual kp/ki/kd config required; physics + learning handles it
2. **Thermal rates auto-learned** - No manual cool_rate/heat_rate config; system observes and learns
3. **Physics-based cold start** - Energy rating + zone dimensions + window area provide initial PID estimates before learning
4. **Optional PID override** - Advanced users can disable auto-tuning and set fixed values per zone
5. **Demand switch separate from PWM** - Single demand switch per zone (valve control) is distinct from PWM heater cycling
6. **Central heat source control** - Aggregates zone demands, controls main heater/cooler with startup delay (valves open first)
7. **Mode synchronization** - Switching one zone to heat/cool mode syncs all zones; OFF is independent
8. **Learning data in HA storage** - Uses `.storage/` for persistence, not external JSON files
9. **Single integration** - Replaces both HASmartThermostat and PyScript module
