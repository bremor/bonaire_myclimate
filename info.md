[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

The `Bonaire MyClimate` climate platform is a reverse engineered implementation of the MyClimate app which is used to control the WiFi module of a Bonaire heating and/or cooling system. This is in no way associated with the company Bonaire and comes with no guarantees or warranty. Use at your own risk.

## Prerequisites
### Hardware
You must have the Bonaire Wifi Module installed and connected to your Wifi.

## Current Functionality as of v0.1
- Read heat/cool/fan operation
- Read room temperature
- Control target temperature.

## Coming soon...
- Control system on/off.
- Control zones.
- Control type (heat/cool/fan)
- Control mode (econ/thermo/boost) 

## Future goals...
- Control fan speed

## Configuration
Add the following to your `configuration.yaml` file:

```yaml
# Example configuration.yaml entry
climate:
  - platform: bonaire_myclimate
    name: this_is_my_entity_name
```

Configuration variables:

- **name** (*Optional*): The name of your climate entity. Default is `Bonaire MyClimate`

## Troubleshooting
Please set your logging for the custom_component to debug:
```
logger:
  default: warn
  logs:
    custom_components.bonaire_myclimate: debug
```
