# LG Hombot/Roboking Component for Home Assistant

[![Validate Hassfest](https://github.com/plakna/ha-lg-hombot/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/plakna/ha-lg-hombot/actions/workflows/hassfest.yaml) [![Validate HACS](https://github.com/plakna/ha-lg-hombot/actions/workflows/hacs.yaml/badge.svg)](https://github.com/plakna/ha-lg-hombot/actions/workflows/hacs.yaml) 

The LG Hombot `vacuum` platform allows you to control the state of your LG Hombot robot vacuum.
This robot is also called Roboking in some countries.

You first have to plug a wifi dongle to your robot and [install Wifi support](https://www.roboter-forum.com/index.php?thread/10009-lg-hombot-3-0-wlan-kamera-steuerung-per-weboberfl%C3%A4che/&postID=107354#post107354) before it can be controlled by this platform.

You can display various data like the robot status or the charging level via template sensors.

## Installation

Manual: copy `custom_components/lg_hombot` folder into your `custom_components`.

## Configuration

```yaml
vacuum:
  - platform: lg_hombot
    host: your_hombot_ip_address
    port: 6260

sensor:
  - platform: template
    sensors:
      hombot_battery:
        friendly_name: Battery
        entity_id: vacuum.hombot
        unit_of_measurement: '%'
        value_template: "{{ states.vacuum.hombot.attributes.battery_level }}"
        icon_template: "{{ states.vacuum.hombot.attributes.battery_icon }}"
      hombot_status:
        friendly_name: Status
        entity_id: vacuum.hombot
        value_template: "{{ states.vacuum.hombot.attributes.status }}"
```

## Lovelace card

You can use a simple `Entities` card like:

```yaml
type: entities
title: Vacuum
entities:
  - entity: vacuum.hombot
  - entity: sensor.hombot_status
  - entity: sensor.hombot_battery
```

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
  - service: vacuum.turn_on
    target:
      entity_id: vacuum.hombot

mode: single
```

## Credits

This is a fork of [ericpignet](https://github.com/ericpignet)'s LG Hombot integration. Since the original implementation is no longer compatible with the latest version of Home Assistant and [ericpignet](https://github.com/ericpignet) no longer maintains the project, I forked and upgraded the project.
