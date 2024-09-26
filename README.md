# Bonaire My Climate Custom Component

This Home Assistant custom component is a reverse engineered implementation of the "My Climate" iOS/Android app which is used to control the Wi-Fi module of a Bonaire heating and/or cooling system. This is in no way associated with the company Bonaire and comes with no guarantees or warranty. Use at your own risk.

## 1.0.0 - Breaking Changes

This custom component was completely re-written as of `1.0.0` and no longer uses yaml config and must be added through your `Integrations` page. Please direct all bugs over to [Issues](https://github.com/bremor/bonaire_myclimate/issues).


## 1.1.0 - New Service: "bonaire_myclimate.send_raw_command"

Introducing a new service that allows you to send commands directly to the Bonaire MyClimate Wi-Fi module. This is a much more reliable way for automations or scripts that require multiple changes at the same time, for example changing temperature and zone at the same time. For a complete list of all different raw commands, you should set your logging to debug for this integration to see what is sent and received by the WiFi module.
```
service: bonaire_myclimate.send_raw_command
data:
  raw_command:
    system: 'on'
    zoneList: '1'
    setPoint: '22'
    type: heat
    mode: econ
```
## 1.1.1 - Support for Evaporative cooler running in thermo mode.

Evaporative coooler runnning in thermo mode uses a set point for the fan Speed rather than fan speed.  This difference caused a bug with the previous versions that would crash the plugin when unit was changed to Evap and in thermo mode.  The only way to rectify it was to use the Navigator control to change the unit to manual  or back to heat.  This verion supports evaporative cooling running in thermo mode.  It can't change the unit to and from thermo mode but this should be possible with the bonaire_myclimate.send_raw_command service.

## Installation

[![hacs][hacsbadge]][hacs]

Install via HACS (default store) or install manually by copying the files in a new 'custom_components/bonaire_myclimate' directory.

## Prerequisites

- You must have the Bonaire Wifi Module installed and connected to your Wifi.
- You must not have your iOS/Android app connected to the module.

## Configuration
After you have installed the custom component (see above):
1. Goto the `Configuration` -> `Integrations` page.  
2. On the bottom right of the page, click on the `+ Add Integration` sign to add an integration.
3. Search for `Bonaire MyClimate`. (If you don't see it, try refreshing your browser page to reload the cache.)
4. Click `Submit` so add the integration.

## Troubleshooting
Please set your logging for the custom_component to debug:
```yaml
logger:
  default: warn
  logs:
    custom_components.bonaire_myclimate: debug
```

## Using "Simple Thermostat"
There is a custom card that looks great and works really well with this climate component. https://github.com/nervetattoo/simple-thermostat
This is the lovelace code I use to display my thermostat.

![image](https://user-images.githubusercontent.com/34525505/123367525-6c73dd80-d5bd-11eb-8efb-229a32e3e9aa.png)

```yaml
type: custom:simple-thermostat
entity: climate.bonaire_myclimate
header: false
layout:
  step: row
  mode:
    headings: false
hide:
  state: true
decimals: '0'
step_size: '1'
control:
  _headings: false
  hvac:
    cool:
      name: Cool
    fan_only:
      name: Fan Only
    heat:
      name: Heat
    'off':
      name: 'Off'
  preset:
    '1':
      name: Downstairs
    '2':
      name: Upstairs
    _hide_when_off: true
    1,2:
      name: Everywhere
```
<a href="https://www.buymeacoffee.com/bremor" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height=40px width=144px></a>

[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge
