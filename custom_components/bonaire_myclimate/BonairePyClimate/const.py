"""Constants for the BonairePyClimate."""

FAN_MODES_COOL = ["thermo"]
FAN_MODES_EVAP = ["0","1", "2", "3", "4", "5", "6", "7", "8"]
FAN_MODES_FAN_ONLY = ["1", "2", "3", "4", "5", "6", "7", "8"]
FAN_MODES_HEAT = ["econ", "thermo", "boost"]

PORT_DISCOVERY = 10001
PORT_LOCAL = 10003

XML_DELETE = "<myclimate><delete>connection</delete></myclimate>"
XML_DISCOVERY = "<myclimate><get>discovery</get><ip>{}</ip><platform>android</platform><version>1.0.0</version></myclimate>"
XML_GETZONEINFO = "<myclimate><get>getzoneinfo</get></myclimate>"
XML_INSTALLATION = "<myclimate><get>installation</get></myclimate>"