# Heating Analysis for Home Assistant

Dashboard visualization, weekly reports, and **adaptive PID tuning** for floor heating performance.

## Architecture

```
pyscript/
├── config/
│   ├── zones.yaml          # Zone definitions (area, volume, entities)
│   ├── constants.yaml      # System constants and thresholds
│   └── learning_data.json  # Adaptive learning data (auto-generated)
├── dashboard/
│   └── heating.yaml        # Dashboard configuration
├── heating_services.py     # Main pyscript module
└── README.md

/config/configuration.yaml  # Source of truth for current PID values
```

**Single source of truth:**
- Zone configuration: `config/zones.yaml`
- System constants: `config/constants.yaml`
- Current PID values: Read directly from `/config/configuration.yaml` (HASmartThermostat entries)
- Learned metrics: `config/learning_data.json` (auto-generated)

## Installation

1. Copy the `pyscript/` folder to your Home Assistant `/config/` directory
2. Add the input_number helper to your `configuration.yaml`:

```yaml
input_number:
  heating_gj_cost:
    name: "Heating Cost per GJ"
    min: 0
    max: 100
    step: 0.01
    unit_of_measurement: "EUR/GJ"
    icon: mdi:currency-eur
    initial: 35
```

3. Update `config/constants.yaml` with your notification service name
4. Restart Home Assistant or reload pyscript
5. Install the dashboard (see Dashboard section below)

## Sensors Created

### Per-Zone Performance
| Sensor | Description |
|--------|-------------|
| `sensor.heating_<zone>_power_m2` | Current power demand (W/m²) |
| `sensor.heating_<zone>_cycle_time` | Average cycle duration (minutes) |
| `sensor.heating_<zone>_duty_cycle` | Heating duty cycle (%) |

### Per-Zone PID Values
| Sensor | Description |
|--------|-------------|
| `sensor.heating_<zone>_current_kp` | Current Kp from configuration.yaml |
| `sensor.heating_<zone>_current_ki` | Current Ki from configuration.yaml |
| `sensor.heating_<zone>_current_kd` | Current Kd from configuration.yaml |
| `sensor.heating_<zone>_recommended_kp` | Adaptive recommended Kp |
| `sensor.heating_<zone>_recommended_ki` | Adaptive recommended Ki |
| `sensor.heating_<zone>_recommended_kd` | Adaptive recommended Kd |

### Per-Zone Learning Metrics
| Sensor | Description |
|--------|-------------|
| `sensor.heating_<zone>_overshoot` | Learned average overshoot (°C) |
| `sensor.heating_<zone>_settling_time` | Learned settling time (minutes) |
| `sensor.heating_<zone>_oscillations` | Learned oscillation count |

### System-Wide
| Sensor | Description |
|--------|-------------|
| `sensor.heating_total_power_m2` | System average W/m² |
| `sensor.heating_total_cost` | Weekly energy cost (EUR) |
| `sensor.heating_system_health` | Overall health status |

## Services

| Service | Description |
|---------|-------------|
| `pyscript.heating_weekly_report` | Generate and send weekly performance report |
| `pyscript.heating_health_check` | Run health check and alert on issues |
| `pyscript.heating_pid_recommendations` | Generate PID tuning recommendations per zone |
| `pyscript.heating_cost_report` | Generate energy cost breakdown |
| `pyscript.heating_run_learning` | Manually trigger adaptive learning |

## Scheduled Tasks

| Schedule | Task |
|----------|------|
| Daily 3:00 AM | Adaptive learning analysis |
| Sunday 9:00 AM | Weekly performance report |
| Every 6 hours | Health monitoring check |
| Every 5 minutes | Performance sensor updates |
| Hourly | Cost sensor updates |

## Adaptive Learning

The system **learns from actual performance** and adjusts PID recommendations over time.

### How It Works

1. **Daily analysis** (3:00 AM): Analyzes 7 days of temperature and heating history
2. **Per-zone metrics**: Measures overshoot, settling time, oscillations, and rise time
3. **Adaptive adjustments**: Modifies PID recommendations based on observed behavior
4. **Persistent storage**: Learning data saved to `learning_data.json`

### Metrics Tracked

| Metric | Description | Impact on PID |
|--------|-------------|---------------|
| **Overshoot** | Temperature exceeds setpoint | >0.5°C: reduce Kp, Ki |
| **Undershoot** | Room not reaching setpoint | >0.3°C: increase Ki |
| **Settling time** | Time to stabilize after heating | >90 min: increase Kd |
| **Oscillations** | Setpoint crossings before settling | >3: reduce Kp, increase Kd |
| **Rise time** | Time to reach setpoint | >60 min + no overshoot: increase Kp |

### Adjustment Rules

```
High overshoot (>0.5°C)     → Reduce Kp by up to 15%, reduce Ki
Moderate overshoot (>0.2°C) → Slight Kp reduction (5%)
Slow response (>60 min)     → Increase Kp by 10%
Undershoot (>0.3°C)         → Increase Ki by up to 20%
Many oscillations (>3)      → Reduce Kp by 10%, increase Kd by 20%
Some oscillations (>1)      → Increase Kd by 10%
Slow settling (>90 min)     → Increase Kd by 15%
```

### Minimum Data Requirements

- At least **3 analyzed heating events** required before adaptive recommendations
- Analysis looks at the **last 20 heating cycles** within a 7-day window
- Zones with insufficient data fall back to physics-based recommendations

### Manual Trigger

Run learning analysis on demand:
```yaml
service: pyscript.heating_run_learning
```

## PID Base Tuning (Physics-Based)

When no learning data exists, recommendations are based on:

- **Thermal time constant** (τ) - estimated from volume and cooling rate
- **Zone characteristics** - doors, skylights, ventilation
- **A+++ house constraints** - conservative tuning for high-efficiency homes

Zone-specific adjustments:
| Zone | Factor | Adjustment |
|------|--------|------------|
| Kitchen | Oven/door disturbances | Lower Ki |
| Bathroom | Skylight heat loss | Higher Kp |
| Bedroom | Night ventilation | Lower Ki |
| Ground Floor | Exterior doors | Higher Ki |

## Health Monitoring

Alerts are sent when:
- **Very short cycles** (<10 min) - actuator stress risk
- **Short cycles** (<15 min) - consider tuning adjustment
- **High power** (>20 W/m²) - insulation or setpoint issue
- **Sensor unavailable** - temperature sensor offline

## Energy Cost Tracking

- Set your GJ price via `input_number.heating_gj_cost`
- Cost is calculated from duty cycles when no GJ meter is available
- When `gj_meter_sensor` is configured, actual consumption is used

## Configuration

### zones.yaml
Define each zone with:
- `area_m2`, `volume_m3`, `ceiling_height_m`
- `cool_rate_c_per_hour` - natural cooling rate
- Entity IDs for climate, heater switch, temperature sensor
- Notes about zone characteristics

### constants.yaml
- Notification service name
- Performance thresholds (W/m², cycle times)
- PID tuning limits
- Health monitoring settings
- Energy conversion factors

### learning_data.json (auto-generated)
Stores per-zone learned metrics:
```json
{
  "kitchen": {
    "overshoot": 0.15,
    "undershoot": 0.0,
    "settling_time": 45.2,
    "oscillation_count": 1.5,
    "rise_time": 32.0,
    "analyzed_events": 12,
    "timestamp": "2025-01-08T03:00:00"
  }
}
```

## Dashboard

A pre-configured dashboard is included at `dashboard/heating.yaml`.

### Dashboard Features

- **System Overview**: Health status, total power gauge, weekly cost gauge, GJ price input
- **Power History**: 7-day graph showing power demand for all zones
- **Quick Actions**: Buttons to trigger learning, reports, and health checks
- **PID Tuning Cards**: Per-zone comparison of current vs recommended PID values
- **Learning Metrics**: Overshoot, settling time, oscillations per zone

### Dashboard Installation

**Option A: UI Import (Recommended)**

1. Go to **Settings** > **Dashboards** > **Add Dashboard**
2. Choose "New dashboard from scratch"
3. Name it "Heating", icon `mdi:radiator`
4. Open the new dashboard
5. Click the three dots menu > **Edit Dashboard**
6. Click three dots again > **Raw configuration editor**
7. Delete existing content and paste the contents of `dashboard/heating.yaml`
8. Save

**Option B: YAML Mode**

If you use YAML mode for dashboards, add to `configuration.yaml`:

```yaml
lovelace:
  mode: yaml
  dashboards:
    heating:
      mode: yaml
      filename: pyscript/dashboard/heating.yaml
      title: Heating
      icon: mdi:radiator
      show_in_sidebar: true
```

### Dashboard Layout

```
+------------------+------------------+------------------+------------------+
|  System Health   |  Total Power     |  Weekly Cost     |  GJ Price Input  |
|     (badge)      |    (gauge)       |    (gauge)       |    (slider)      |
+------------------+------------------+------------------+------------------+
|                    Power History - 7 Day Graph                            |
|                    (all zones overlaid)                                   |
+--------------------------------------------------------------------------+
| Run Learning | PID Report | Health Check | Weekly Report | Cost Report   |
+--------------------------------------------------------------------------+
|  Ground Floor PID    |  Kitchen PID        |  Living Room PID            |
|  Kp/Ki/Kd current    |  Kp/Ki/Kd current   |  Kp/Ki/Kd current           |
|  vs recommended      |  vs recommended     |  vs recommended             |
|  + learning metrics  |  + learning metrics |  + learning metrics         |
+----------------------+---------------------+-----------------------------+
|  Bedroom PID         |  Bathroom PID       |  Study PID    | Hallway PID |
+----------------------+---------------------+---------------+-------------+
```
