## Please note that this is fork from https://github.com/plakna/ha-lg-hombot to have the LG HomBot work with Home Assistant 2026.8

⚠️ **BREAKING CHANGE in Version 2.0.0** - Battery information has been migrated to a separate sensor entity to comply with Home Assistant 2026.8 requirements. See [migration guide](#migration-from-version-1x-to-20) below.

# LG Hombot for Home Assistant

[![Validate Hassfest](https://github.com/aldweb/ha-lg-hombot/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/aldweb/ha-lg-hombot/actions/workflows/hassfest.yaml) [![Validate HACS](https://github.com/aldweb/ha-lg-hombot/actions/workflows/hacs.yaml/badge.svg)](https://github.com/aldweb/ha-lg-hombot/actions/workflows/hacs.yaml) 

This LG Hombot integration enables you to control and monitor your LG Hombot robot vacuum, also known as Roboking in some countries.

Before you can utilize this platform, you need to connect a Wi-Fi dongle to your robot and [install Wi-Fi support](https://www.roboter-forum.com/index.php?thread/10009-lg-hombot-3-0-wlan-kamera-steuerung-per-weboberfl%C3%A4che/&postID=107354#post107354).

With the platform, you can access and display a range of data, such as the robot's status, battery level, and various cleaning statistics.

## Features

### Vacuum Entity (`vacuum.lg_hombot`)
- ✅ Start/Stop/Pause cleaning
- ✅ Return to base
- ✅ Fan speed control (Normal/Turbo)
- ✅ Activity monitoring (cleaning, docked, paused, returning, idle)
- ✅ Extended attributes:
  - Repeat mode status
  - Cleaning mode
  - Robot nickname
  - Firmware version
  - Last cleaning timestamp
  - Total number of cleanings

### Battery Sensor (`sensor.lg_hombot_battery`)
- 🔋 Battery level (0-100%)
- 🔌 Charging status attribute
- 📊 Proper Home Assistant battery device class

## Setup

If you know how to use HACS click here:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=aldweb&repository=ha-lg-hombot&category=integration) [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=lg_hombot)

If not, follow this guide:

- **Step 1: Verify prerequisites** Make sure you have HACS (Home Assistant Community Store) installed and configured in your Home Assistant installation.

- **Step 2: Open HACS** Open the Home Assistant frontend and navigate to HACS.

- **Step 3: Add integration** Click on the "Integrations" tab in HACS. Look for the "Add Integration" option and click on it.

- **Step 4: Enter repository URL** Enter the URL to this GitHub repository: `https://github.com/aldweb/ha-lg-hombot`

- **Step 5: Install integration** Click on "Install." HACS will analyze the repository and display the available integrations.

- **Step 6: Select and install integration** Locate the integration from the list and click on "Install." HACS will download the integration and install it into Home Assistant.

- **Step 7: Configure** Go to **Settings > Devices & Services > Add Integration** and search for "LG Hombot". Enter your robot's IP address and name.

- **Step 8: Perform a restart** Restart Home Assistant to activate the integration.

- **Step 9: Start using the integration** After the restart, you'll find two entities:
  - `vacuum.lg_hombot` (or your configured name)
  - `sensor.lg_hombot_battery`

That's it! You have successfully integrated this repository into Home Assistant.

## Migration from Version 1.x to 2.0

### What Changed?

In version 2.0.0, the battery information has been moved from the vacuum entity to a separate sensor entity to comply with Home Assistant 2026.8 deprecation warnings.

**Before (v1.x):**
- Battery level: `state_attr('vacuum.lg_hombot', 'battery_level')`

**After (v2.0):**
- Battery level: `states('sensor.lg_hombot_battery')`
- Charging status: `state_attr('sensor.lg_hombot_battery', 'charging')`

### Update Your Automations

Replace any references to `battery_level` attribute in your automations:

**Old:**
```yaml
condition:
  - condition: template
    value_template: "{{ state_attr('vacuum.lg_hombot', 'battery_level') | int > 50 }}"
```

**New:**
```yaml
condition:
  - condition: numeric_state
    entity_id: sensor.lg_hombot_battery
    above: 50
```

### Update Your Dashboard

If you display battery level on your dashboard, update the entity reference from the vacuum's attribute to the new sensor entity.

## Breaking Changes

- **Version 2.0.0** (2026.8 compatibility):
  - Battery level moved to separate sensor entity `sensor.lg_hombot_battery`
  - Battery charging status available as sensor attribute
  - Removed deprecated `VacuumEntityFeature.BATTERY`
  - Added extended vacuum attributes (repeat_mode, cleaning_mode, nickname, etc.)

- **Version 1.0.4** (2026.1 compatibility):
  - Ensured compatibility with Home Assistant 2026.1
  - Prepared groundwork for battery sensor migration
  - Thanks to [plakna](https://github.com/plakna/ha-lg-hombot/issues/5) for collaboration

- **Version 1.0.3**:
  - The service `vacuum.turn_on` has been changed to `vacuum.start` (deprecated).
  - The status has been standardized according to [HomeAssistant conventions](https://developers.home-assistant.io/docs/core/entity/vacuum/#states).

- **Version 1.0.0**:
  - Removal of yaml file support: This function is no longer supported and should not be used anymore.

## Troubleshooting

### Battery sensor not appearing
1. Check that your Hombot is accessible at `http://[IP]:6260/status.txt`
2. Restart Home Assistant
3. Check logs in **Settings > System > Logs** for any errors

### Deprecation warnings still showing
1. Ensure you've updated to version 2.0.0
2. Clear browser cache
3. Restart Home Assistant
4. Check that `sensor.py` file exists in `custom_components/lg_hombot/`

### Connection issues
- Verify Wi-Fi dongle is properly installed on your Hombot
- Check network connectivity to the robot
- Test manually: `curl http://[IP]:6260/status.txt`

## Credits

This is a fork of [plakna](https://github.com/plakna/ha-lg-hombot)'s LG Hombot integration, which itself was forked from [ericpignet](https://github.com/ericpignet)'s original implementation. 

This version has been updated to ensure compatibility with Home Assistant 2026.8 and beyond by migrating battery information to a compliant sensor entity.

## License

MIT License - See LICENSE file for details.
