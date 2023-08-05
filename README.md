⚠️ **Attention: Breaking Change in Version 1.0.0 - Please note that in this version, a deprecated function has been removed (*.yaml file) and is no longer supported.**

# LG Hombot for Home Assistant

[![Validate Hassfest](https://github.com/plakna/ha-lg-hombot/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/plakna/ha-lg-hombot/actions/workflows/hassfest.yaml) [![Validate HACS](https://github.com/plakna/ha-lg-hombot/actions/workflows/hacs.yaml/badge.svg)](https://github.com/plakna/ha-lg-hombot/actions/workflows/hacs.yaml) 

This LG Hombot integration enables you to control and monitor your LG Hombot robot vacuum, also known as Roboking in some countries.

Before you can utilize this platform, you need to connect a Wi-Fi dongle to your robot and [install Wi-Fi support](https://www.roboter-forum.com/index.php?thread/10009-lg-hombot-3-0-wlan-kamera-steuerung-per-weboberfl%C3%A4che/&postID=107354#post107354).

With the platform, you can access and display a range of data, such as the robot's status and the current charging level.

## Setup

If you know how to use HACS click here:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=plakna&repository=ha-lg-hombot&category=integration) [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=lg_hombot)

If not, follow this guide:

- **Step 1: Verify prerequisites** Make sure you have HACS (Home Assistant Community Store) installed and configured in your Home Assistant installation.

- **Step 2: Open HACS** Open the Home Assistant frontend and navigate to HACS.

- **Step 3: Add integration** Click on the "Integrations" tab in HACS. Look for the "Add Integration" option and click on it.

- **Step 4: Enter repository URL** Enter the URL to this GitHub repository: `https://github.com/plakna/ha-lg-hombot`

- **Step 5: Install integration** Click on "Install." HACS will analyze the repository and display the available integrations.

- **Step 6: Select and install integration** Locate your extension from the list of available integrations and click on "Install." HACS will download the integration and install it into Home Assistant.

- **Step 7: Configure** Go to your Home Assistant configuration and add the required configuration parameters for the LG Homebot extension. Refer to the documentation of your extension to learn the proper configuration steps.

- **Step 8: Perform a restart** Perform a restart of Home Assistant to activate the new integration.

- **Step 9: Start using the integration** After the restart, you should be able to access the features of the LG Homebot extension in Home Assistant. Refer to the extension's documentation to learn how to use and configure it.

That's it! You have successfully integrated this repository into Home Assistant.

## Automation

```yaml
id: "...." # use https://www.guidgenerator.com 

alias: "Start Hombot"

trigger:
  - platform: time
    at: "09:15"

condition:
  - condition: time
    weekday:
      - tue
      - sun

action:
  - service: vacuum.start
    target:
      entity_id: vacuum.hombot

mode: single
```

## Breaking Changes

- Version 1.0.0:
  - Removal of yaml file support: This function is no longer supported and should not be used anymore.
- Version 1.0.3:
  - The service `vacuum.turn_on` has been changed to `vacuum.start` (deprecated).
  - The status has been standardized according to [HomeAssistant conventions](https://developers.home-assistant.io/docs/core/entity/vacuum/#states).

## Credits

This is a fork of [ericpignet](https://github.com/ericpignet)'s LG Hombot integration. Since the original implementation is no longer compatible with the latest version of Home Assistant and is no longer actively maintained by ericpignet, I have taken the initiative to fork the project and provide necessary updates.
