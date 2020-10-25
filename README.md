[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

The `Bonaire MyClimate` climate platform is a reverse engineered implementation of the MyClimate app which is used to control the WiFi module of a Bonaire heating and/or cooling system. This is in no way associated with the company Bonaire and comes with no guarantees or warranty. Use at your own risk.

# Installation (There are two methods, with HACS or manual)

### 1. Easy Mode

We support [HACS](https://hacs.netlify.com/). Go to "STORE", search "Bonaire MyClimate" and install.

### 2. Manual

Install it as you would do with any homeassistant custom component:

1. Download `custom_components` folder.
2. Copy the `bonaire_myclimate` directory within the `custom_components` directory of your homeassistant installation.
The `custom_components` directory resides within your homeassistant configuration directory.
**Note**: if the custom_components directory does not exist, you need to create it.
After a correct installation, your configuration directory should look like the following.

    ```
    └── ...
    └── configuration.yaml
    └── custom_components
        └── bonaire_myclimate
            └── __init__.py
            └── climate.py
            └── manifest.json
            └── services.yaml
            └── BonairePyClimate
                └── bonairepyclimate.py
    ```

# Prerequisites

### 1. Hardware
You must have the Bonaire Wifi Module installed and connected to your Wifi.

# Configuration
Add the following to your `configuration.yaml` file:

```yaml
# Example configuration.yaml entry
climate:
  - platform: bonaire_myclimate
    name: this_is_my_entity_name
```

Configuration variables:

- **name** (*Optional*): The name of your climate entity. Default is `Bonaire MyClimate`


# Troubleshooting
Please set your logging for the custom_component to debug:
```yaml
logger:
  default: warn
  logs:
    custom_components.bonaire_myclimate: debug
```

# Using Simple Thermostat
There is a custom card that looks great and works really well with this climate component. https://github.com/nervetattoo/simple-thermostat
This is the lovelace code I use to display my thermostat. It uses conditional card to display different things depending on if your climate entity is  off/heat/cool/fan.
```yaml
cards:
  - card:
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
      decimals: 0
      entity: climate.bonaire_myclimate
      label:
        temperature: Remote
      name: false
      step_size: 1
      type: 'custom:simple-thermostat'
    conditions:
      - entity: climate.bonaire_myclimate
        state: 'off'
    type: conditional
  - card:
      control:
        _headings: false
        fan:
          boost:
            name: Boost
          econ:
            name: Economy
          thermo:
            name: Balanced
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
          '1,2':
            name: Everywhere
          none: false
      decimals: 0
      entity: climate.bonaire_myclimate
      label:
        temperature: Remote
      name: false
      step_size: 1
      type: 'custom:simple-thermostat'
    conditions:
      - entity: climate.bonaire_myclimate
        state: heat
    type: conditional
  - card:
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
          '1,2':
            name: Everywhere
          none: false
      decimals: 0
      entity: climate.bonaire_myclimate
      label:
        temperature: Remote
      name: false
      step_size: 1
      type: 'custom:simple-thermostat'
    conditions:
      - entity: climate.bonaire_myclimate
        state: cool
    type: conditional
  - card:
      control:
        _headings: false
        fan:
          boost:
            name: Boost
          econ:
            name: Economy
          thermo:
            name: Balanced
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
          '1,2':
            name: Everywhere
          none: false
      decimals: 0
      entity: climate.bonaire_myclimate
      label:
        temperature: Remote
      name: false
      step_size: 1
      type: 'custom:simple-thermostat'
    conditions:
      - entity: climate.bonaire_myclimate
        state: fan_only
    type: conditional
type: vertical-stack
```

<a href="https://www.buymeacoffee.com/bremor" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height=40px width=144px></a>
