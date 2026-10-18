"""Function tags for every library piece, so a device can ask the library for "gps" or "relay" by name.

The tags are derived only from what the library itself says about a piece: its library nickname, symbol name,
description, keywords, family and footprint name. The same piece always gets the same tags (no model, no
randomness), so the index built from them is deterministic.
"""

from __future__ import annotations

import functools
import re
from typing import Any

# library nickname prefix -> tags every piece in it carries (checked with str.startswith, case sensitive,
# after an "extra" prefix such as "SparkFun-" or "Gekkio_" has been stripped)
LIB_TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("MCU_Module", ("mcu", "mcu-board")),
    ("MCU_RaspberryPi", ("mcu", "rp2040")),
    ("MCU_Espressif", ("mcu", "radio-wifi", "radio-ble")),
    ("MCU_Nordic", ("mcu", "radio-ble")),
    ("MCU_ST_STM32WB", ("mcu", "radio-ble")),
    ("MCU_ST_STM32WL", ("mcu", "radio-lora")),
    ("MCU_", ("mcu",)),
    ("CPU", ("cpu",)),
    ("DSP_", ("dsp",)),
    ("FPGA_", ("fpga",)),
    ("CPLD_", ("cpld",)),
    ("GPU", ("gpu",)),
    ("Espressif", ("mcu", "radio-wifi", "radio-ble")),
    ("RF_GPS", ("gps", "radio")),
    ("GNSS", ("gps", "radio")),
    ("RF_Bluetooth", ("radio-ble", "radio")),
    ("RF_WiFi", ("radio-wifi", "radio")),
    ("RF_ZigBee", ("radio-zigbee", "radio")),
    ("RF_GSM", ("radio-cellular", "radio")),
    ("RF_NFC", ("nfc", "radio")),
    ("RF_RFID", ("rfid", "nfc", "radio")),
    ("RF_AM_FM", ("radio-fm", "radio")),
    ("RF_Module", ("radio",)),
    ("RF_Filter", ("rf-filter",)),
    ("RF_Amplifier", ("rf-amplifier",)),
    ("RF_Switch", ("rf-switch",)),
    ("RF_Mixer", ("rf-mixer",)),
    ("RF", ("radio",)),
    ("Sensor_Temperature", ("sensor", "temperature")),
    ("Sensor_Humidity", ("sensor", "humidity")),
    ("Sensor_Pressure", ("sensor", "pressure")),
    ("Sensor_Gas", ("sensor", "gas")),
    ("Sensor_Optical", ("sensor", "light")),
    ("Sensor_Proximity", ("sensor", "proximity")),
    ("Sensor_Distance", ("sensor", "distance")),
    ("Sensor_Motion", ("sensor", "imu")),
    ("Sensor_Magnetic", ("sensor", "magnetic")),
    ("Sensor_Current", ("sensor", "current-sense")),
    ("Sensor_Energy", ("sensor", "energy-meter")),
    ("Sensor_Voltage", ("sensor", "voltage-sense")),
    ("Sensor_Audio", ("sensor", "mic", "sound")),
    ("Sensor_Touch", ("sensor", "touch")),
    ("Sensor", ("sensor",)),
    ("Display_Character", ("display", "display-lcd", "display-character")),
    ("Display_Graphic", ("display",)),
    ("Display", ("display",)),
    ("Driver_Display", ("display-driver",)),
    ("Driver_LED", ("led-driver",)),
    ("Driver_Motor", ("motor-driver",)),
    ("Driver_FET", ("gate-driver",)),
    ("Driver", ("driver",)),
    ("Motor", ("motor",)),
    ("Relay_SolidState", ("relay", "relay-ssr")),
    ("Relay", ("relay",)),
    ("Battery_Management", ("battery-management",)),
    ("Battery", ("battery",)),
    ("Regulator_Linear", ("regulator", "regulator-linear")),
    ("Regulator_Switching", ("regulator", "regulator-switching")),
    ("Regulator_SwitchedCapacitor", ("regulator", "charge-pump")),
    ("Regulator_Controller", ("regulator", "regulator-controller")),
    ("Regulator_Current", ("regulator", "current-source")),
    ("Regulator", ("regulator",)),
    ("Converter_DCDC", ("dc-dc", "power-module")),
    ("Converter_ACDC", ("ac-dc", "power-module")),
    ("Power_Supervisor", ("power-supervisor",)),
    ("Power_Protection", ("protection",)),
    ("Power_Management", ("power-management",)),
    ("Power", ("power",)),
    ("Reference_Voltage", ("voltage-reference",)),
    ("Reference_Current", ("current-reference",)),
    ("Amplifier_Operational", ("opamp", "amplifier")),
    ("Amplifier_Instrumentation", ("instrumentation-amp", "amplifier")),
    ("Amplifier_Audio", ("audio-amplifier", "amplifier", "audio")),
    ("Amplifier_Current", ("current-sense", "amplifier")),
    ("Amplifier_Difference", ("amplifier",)),
    ("Amplifier_Buffer", ("amplifier", "buffer")),
    ("Amplifier_Video", ("amplifier", "video")),
    ("Amplifier", ("amplifier",)),
    ("Comparator", ("comparator",)),
    ("Analog_ADC", ("adc",)),
    ("Analog_DAC", ("dac",)),
    ("Analog_Switch", ("analog-switch",)),
    ("Analog", ("analog",)),
    ("Potentiometer_Digital", ("digital-potentiometer",)),
    ("74xx", ("logic",)),
    ("74xGxx", ("logic",)),
    ("4xxx", ("logic",)),
    ("Logic_LevelTranslator", ("level-shifter", "logic")),
    ("Logic_Programmable", ("logic", "pld")),
    ("Logic", ("logic",)),
    ("Buffer", ("logic", "buffer")),
    ("Memory_EEPROM", ("memory", "eeprom")),
    ("Memory_Flash", ("memory", "flash")),
    ("Memory_RAM", ("memory", "ram")),
    ("Memory_NVRAM", ("memory", "nvram")),
    ("Memory_EPROM", ("memory", "eprom")),
    ("Memory_UniqueID", ("memory", "unique-id")),
    ("Memory", ("memory",)),
    ("Interface_UART", ("interface", "uart")),
    ("Interface_USB", ("interface", "usb")),
    ("Interface_CAN_LIN", ("interface", "can")),
    ("Interface_Ethernet", ("interface", "ethernet")),
    ("Interface_Expansion", ("interface", "io-expander")),
    ("Interface_HID", ("interface", "hid")),
    ("Interface_Optical", ("interface", "ir")),
    ("Interface_LineDriver", ("interface", "line-driver")),
    ("Interface_Telecom", ("interface", "telecom")),
    ("Interface_CurrentLoop", ("interface", "current-loop")),
    ("Interface_HDMI", ("interface", "hdmi", "video")),
    ("Interface", ("interface",)),
    ("Isolator_Analog", ("isolator",)),
    ("Isolator", ("isolator",)),
    ("Timer_RTC", ("rtc", "timer")),
    ("Timer_PLL", ("pll", "timer")),
    ("Timer", ("timer",)),
    ("Oscillator", ("oscillator", "clock")),
    ("Clock", ("clock",)),
    ("Audio", ("audio",)),
    ("Video", ("video",)),
    ("Connector_Audio", ("connector", "audio-jack")),
    ("Connector_Generic", ("connector",)),
    ("Connector", ("connector",)),
    ("Switch", ("switch",)),
    ("Diode_Bridge", ("diode", "bridge-rectifier")),
    ("Diode_Laser", ("diode", "laser")),
    ("Diode", ("diode",)),
    ("Transistor_BJT", ("transistor", "bjt")),
    ("Transistor_FET", ("transistor", "mosfet")),
    ("Transistor_FET_Other", ("transistor", "fet")),
    ("Transistor_IGBT", ("transistor", "igbt")),
    ("Transistor_Array", ("transistor", "transistor-array")),
    ("Transistor_Power_Module", ("transistor", "power-module")),
    ("Transistor", ("transistor",)),
    ("Triac_Thyristor", ("triac",)),
    ("Transformer", ("transformer",)),
    ("Filter", ("filter",)),
    ("Fiber_Optic", ("fiber-optic",)),
    ("Valve", ("valve",)),
    ("Jumper", ("jumper",)),
    ("Security", ("security",)),
    ("Coil", ("inductor",)),
    ("Fuse", ("fuse",)),
    ("Resistor", ("resistor",)),
    ("Capacitor", ("capacitor",)),
    ("LED", ("led",)),
    ("Electromechanical", ("electromechanical",)),
    ("Hardware", ("hardware",)),
    ("Board", ("board",)),
    ("Camera", ("camera",)),
    ("DiscreteSemi", ("discrete",)),
    ("marbastlib-mx", ("keyswitch", "switch")),
    ("marbastlib-choc", ("keyswitch", "switch")),
    ("marbastlib-gateron", ("keyswitch", "switch")),
    ("marbastlib-he", ("keyswitch", "hall")),
    ("marbastlib-promicroish", ("mcu", "mcu-board")),
)

# family (as kicad_library._family writes it) -> tags
FAMILY_TAGS: dict[str, tuple[str, ...]] = {
    "resistor": ("resistor",),
    "capacitor": ("capacitor",),
    "electrolytic capacitor": ("capacitor", "capacitor-electrolytic"),
    "LED": ("led",),
    "addressable LED": ("rgb-led", "addressable-led", "led"),
    "RGB LED": ("rgb-led", "led"),
    "inductor": ("inductor",),
    "crystal": ("crystal", "clock"),
    "buzzer": ("buzzer", "sound-out"),
    "speaker": ("speaker", "sound-out"),
    "thermistor": ("thermistor", "temperature", "sensor"),
    "fuse": ("fuse",),
    "diode": ("diode",),
    "transistor": ("transistor",),
    "BJT transistor": ("transistor", "bjt"),
    "MOSFET": ("transistor", "mosfet"),
    "battery": ("battery",),
    "microphone": ("mic", "sound", "sensor"),
    "rotary encoder": ("encoder", "input"),
    "potentiometer": ("potentiometer", "input"),
    "trimmer potentiometer": ("potentiometer",),
    "light sensor": ("light", "sensor"),
    "USB connector": ("usb", "connector"),
    "USB-C data connector": ("usb", "usb-c", "connector"),
    "audio jack": ("audio-jack", "connector"),
    "switch": ("switch",),
    "relay": ("relay",),
    "motor": ("motor",),
    "motor driver": ("motor-driver",),
    "GPS module": ("gps", "radio"),
    "radio module": ("radio",),
    "Wi-Fi microcontroller module": ("mcu", "radio-wifi", "radio-ble"),
    "USB microcontroller": ("mcu", "mcu-board", "usb"),
    "microcontroller": ("mcu",),
    "microcontroller module": ("mcu", "mcu-board"),
    "temperature sensor": ("temperature", "sensor"),
    "humidity sensor": ("humidity", "sensor"),
    "pressure sensor": ("pressure", "sensor"),
    "gas sensor": ("gas", "sensor"),
    "proximity sensor": ("proximity", "sensor"),
    "motion sensor": ("imu", "sensor"),
    "magnetic sensor": ("magnetic", "sensor"),
    "current sensor": ("current-sense", "sensor"),
    "IR receiver": ("ir", "ir-receiver", "sensor"),
    "display": ("display",),
    "EEPROM": ("eeprom", "memory"),
    "flash memory": ("flash", "memory"),
    "battery charger": ("charger", "battery-management"),
    "linear regulator": ("regulator", "regulator-linear"),
    "LDO regulator": ("regulator", "regulator-linear"),
    "switching regulator": ("regulator", "regulator-switching"),
    "timer IC": ("timer",),
    "oscillator": ("oscillator", "clock"),
    "ESD protection": ("protection", "esd"),
    "tri-state buffer": ("logic", "buffer"),
    "connector": ("connector",),
    "passive": ("passive",),
    "jumper": ("jumper",),
}

# (tag, regex) over "lib name description keywords family" (lower case). Word-ish boundaries keep "led" from
# matching "controlled" and "mic" from "microcontroller".
TEXT_RULES: tuple[tuple[str, str], ...] = (
    (
        "mcu",
        r"\bmicrocontroller\b|\b(rp2040|rp2350|atmega\d|attiny\d|stm32|esp32|esp8266|nrf5[0-9]|samd\d|pic1[0-9]f|ch32v)",
    ),
    (
        "radio-ble",
        r"\bbluetooth\b|\bble\b|\bnrf5[0-9]|\bcc254\d|\bda1458\d|\besp32(?!-s2)",
    ),
    (
        "radio-wifi",
        r"wi-?fi|\bwlan\b|802\.11|\besp8266|\besp32|\bcyw43|\bpico ?w\b|\bwinc15",
    ),
    ("radio-lora", r"\blora\b|\bsx12[67]\d|\brfm9[5-8]|\brak\d{4}"),
    ("radio-zigbee", r"zigbee|802\.15\.4|\bxbee"),
    (
        "radio-cellular",
        r"\bgsm\b|\blte\b|\bgprs\b|\bsim800|\bsim7\d\d|\bnb-?iot|cellular",
    ),
    (
        "radio",
        r"\btransceiver\b|\brf module\b|\bradio\b|\bsub-?ghz|\b(433|868|915) ?mhz|\b2\.4 ?ghz",
    ),
    (
        "gps",
        r"\bgps\b|\bgnss\b|\bglonass\b|\bbeidou\b|\bneo-?[678]m?|\bu-?blox\b|\bmax-?m10|\bl80\b|\bl76\b|\bpa1010",
    ),
    ("antenna", r"\bantenna\b"),
    (
        "imu",
        r"\bimu\b|inertial|\bmpu-?(6050|6500|9250|9150)|\blsm6d|\bbmi\d{3}|\bicm-?\d{5}|\bbno0\d\d|\b6-?axis|\b9-?axis",
    ),
    (
        "accelerometer",
        r"accelerometer|\badxl\d|\blis[23]dh|\bmma8\d|\bmpu-?(6050|6500|9250)|\blsm6d|\bbmi\d{3}|\bicm-?\d{5}",
    ),
    (
        "gyroscope",
        r"gyroscope|\bgyro\b|\bl3gd|\bmpu-?(6050|6500|9250)|\blsm6d|\bicm-?\d{5}",
    ),
    (
        "magnetometer",
        r"magnetometer|\bcompass\b|\bhmc58|\bqmc58|\blis3mdl|\bak89\d\d|\bmmc5",
    ),
    ("hall", r"\bhall\b"),
    (
        "temperature",
        r"temperature sensor|\bthermistor\b|\bntc\b|\bds18b20|\blm3[45]\b|\btmp\d{2,3}\b|\bmcp9[78]\d\d|thermocouple|\bmax3185|\bmax6675|\bdht\d\d|\bsht[34]\d|\bsi70\d\d|\bbme[26]80|\bhdc10\d\d|\baht[12]0|\bam23\d\d",
    ),
    (
        "humidity",
        r"humidity|\bhygrometer|\bdht\d\d|\bsht[34]\d|\bsi70\d\d|\bbme[26]80|\bhdc10\d\d|\baht[12]0|\bam23\d\d",
    ),
    (
        "pressure",
        r"\bpressure sensor|\bbarometer|barometric|\bbmp[0-9]{3}|\bbme[26]80|\blps\d\dh|\bms5611|\bdps310",
    ),
    (
        "light",
        r"light sensor|ambient light|\bldr\b|photoresistor|\bphotocell|\bphotodiode|phototransistor|\blux\b|\bbh1750|\btsl25\d\d|\bveml\d{4}|\bopt3001|\bcds\b|light dependent",
    ),
    ("color-sensor", r"colou?r sensor|\btcs34\d\d|\btcs3200|\bapds-?99\d\d"),
    ("uv", r"\buv sensor|\bultraviolet|\bveml6070|\bltr390|\bguva"),
    (
        "distance",
        r"\bdistance\b|\bultrasonic|\bhc-?sr04|\bvl53l\d|\bvl6180|time.of.flight|\btof\b|\blidar\b|\branging",
    ),
    ("proximity", r"proximity|\bapds-?99\d\d|\bvcnl\d{4}"),
    (
        "gas",
        r"\bgas sensor|\bco2\b|\bmq-?\d|\bccs811|\bsgp\d\d|\bscd[34]\d|\bsmoke\b|air quality|\bvoc\b|\bens160|\bbme680",
    ),
    ("particulate", r"particulate|\bpm2\.5|\bpms\d{4}|\bsps30"),
    (
        "mic",
        r"\bmicrophone\b|\bmems mic|\bmic\b|\binmp441|\bsph0645|\bmax4466|\bmax9814",
    ),
    ("sound", r"\bmicrophone\b|\bsound sensor|\baudio sensor"),
    ("pir", r"\bpir\b|pyroelectric|passive infrared|\bam312|\bhc-?sr501"),
    (
        "ir",
        r"\binfrared\b|\bir receiver|\bir led|\btsop\d|\bvs1838|\bir emitter|\bir remote",
    ),
    ("ir-receiver", r"\bir receiver|\btsop\d|\bvs1838|infrared receiver"),
    ("touch", r"\btouch\b|capacitive sens|\bttp22\d|\bcap1188|\bmpr121"),
    ("load-cell", r"\bload cell|\bhx711|strain gauge|\bweigh"),
    ("pulse", r"\bmax3010\d|pulse oxim|heart.?rate|\bppg\b"),
    ("current-sense", r"current sens|\bina2\d\d|\bacs7\d\d|\bshunt monitor"),
    ("soil", r"\bsoil\b|\bmoisture\b"),
    ("water", r"\bwater\b|\bliquid level|\bflow sensor|\brain sensor"),
    ("tilt", r"\btilt\b|\bvibration switch|\bsw-?420|\bshock sensor"),
    ("reed", r"\breed\b"),
    ("camera", r"\bcamera\b|\bov2640|\bov7670|\bimage sensor"),
    (
        "display",
        r"\bdisplay\b|\boled\b|\blcd\b|\btft\b|e-?paper|e-?ink|\b7-?segment|seven.segment|\bdot matrix",
    ),
    ("display-oled", r"\boled\b|\bssd13\d\d|\bsh110\d"),
    (
        "display-lcd",
        r"\blcd\b|\bhd44780|\bst77\d\d|\bili9\d{3}|\btft\b|\bnokia 5110|\bpcd8544",
    ),
    ("display-epaper", r"e-?paper|e-?ink|\bepd\b"),
    ("display-7seg", r"7-?segment|seven.segment|\bseg7\b|\btm1637|\bmax7219"),
    ("led-matrix", r"\bled matrix|\bdot matrix|\bmax7219"),
    (
        "rgb-led",
        r"\brgb\b|\bws281\d|\bsk68\d\d|\bneopixel|\bapa10[12]|\bled_rgb|\brgb_?led|\bled_?r?kgb|\bled_rgb",
    ),
    ("addressable-led", r"\bws281\d|\bsk68\d\d|\bneopixel|\bapa10[12]|addressable"),
    ("led", r"\bled\b|\bleds\b|light emitting|\bled_|_led\b"),
    ("laser", r"\blaser\b"),
    (
        "button",
        r"\bpush ?button|\bpushbutton|\btactile|\bsw_push|\bmomentary|\btact\b|\bkeyswitch|\bkey switch|\bcherry mx|\bkailh|\bchoc\b",
    ),
    (
        "keyswitch",
        r"\bkeyswitch|\bkey switch|\bcherry mx|\bmx\b.*switch|\bkailh|\bchoc\b|\bhotswap|\bhot-swap",
    ),
    (
        "switch",
        r"\bswitch\b|\bsw_|\bspdt\b|\bspst\b|\bdpdt\b|\bdip switch|\bslide switch|\btoggle\b",
    ),
    ("encoder", r"\bencoder\b|\bec11\b|\bec12\b"),
    (
        "potentiometer",
        r"potentiometer|\bpot\b|\br_pot|\btrimmer pot|\bfader\b|\brheostat",
    ),
    ("joystick", r"\bjoystick|\bthumbstick"),
    ("keypad", r"\bkeypad\b|\bkeyboard\b"),
    ("buzzer", r"\bbuzzer\b|\bpiezo\b|\bbeeper\b|\bsounder\b"),
    ("speaker", r"\bspeaker\b|\bloudspeaker\b"),
    (
        "audio-amplifier",
        r"audio amp|\blm386|\bpam8\d{3}|\bmax98357|\btpa\d{4}|class.?d",
    ),
    ("audio", r"\baudio\b|\bcodec\b|\bi2s\b|\bmp3\b|\bdfplayer"),
    ("audio-jack", r"\baudio ?jack|\btrrs\b|\btrs\b|\b3\.5 ?mm"),
    ("motor", r"\bmotor\b"),
    (
        "motor-driver",
        r"motor driver|\bh-?bridge|\bl293|\bl298|\bdrv8\d\d\d|\btb6612|\ba4988|\btmc2\d\d\d|\buln200\d|stepper driver|\bbldc driver",
    ),
    ("stepper", r"\bstepper\b|\bstep motor|\b28byj"),
    ("servo", r"\bservo\b"),
    ("fan", r"\bfan\b"),
    ("solenoid", r"\bsolenoid\b|\bvalve\b"),
    ("pump", r"\bpump\b"),
    ("vibration-motor", r"vibration motor|\berm\b|\blra\b|\bdrv260\d|haptic"),
    ("relay", r"\brelay\b"),
    ("triac", r"\btriac\b|\bthyristor\b|\bscr\b"),
    (
        "optocoupler",
        r"opto-?coupler|opto-?isolator|\bpc817|\b4n2[5-8]|\b6n13\d|photocoupler",
    ),
    (
        "battery",
        r"\bbattery\b|\bbatteries\b|\bbattery_cell|\bcoin cell|\bcr20\d\d|\blipo\b|\bli-?ion\b|\b18650\b|\baaa?\b cell|\bbatteryholder",
    ),
    ("coin-cell", r"\bcoin cell|\bcr20\d\d|\bcr1220|\b2032\b|x2032\b|\bbutton cell"),
    (
        "charger",
        r"\bcharger\b|\bcharge controller|\btp4056|\bmcp738\d\d|\bbq24\d{3}|\bbq25\d{3}|li-?ion charg|lipo charg|battery charg",
    ),
    ("fuel-gauge", r"fuel gauge|\bmax1704\d|\bbq27\d{3}"),
    (
        "regulator",
        r"\bregulator\b|\bldo\b|\bams1117|\blm78\d\d|\blm317|\bbuck\b|\bboost\b|\bdc-?dc\b|\bmcp1700|\bap2112|\bxc62\d\d",
    ),
    (
        "regulator-linear",
        r"\bldo\b|linear regulator|\bams1117|\blm78\d\d|\blm317|\bmcp1700|\bap2112|\bxc62\d\d|low.dropout",
    ),
    (
        "regulator-switching",
        r"\bbuck\b|\bboost\b|switching regulator|step-?down|step-?up|\bdc-?dc\b",
    ),
    ("power-in", r"\bbarrel\b|\bdc jack|\bpower jack|\bjack_dc|\bbarreljack"),
    ("usb", r"\busb\b|\busb_|\btype-?c\b|\bmicro-?b\b|\busb-?c\b"),
    ("usb-c", r"\btype-?c\b|\busb_?c\b|\busb-c\b"),
    (
        "sd-card",
        r"\bsd card|\bmicro.?sd|\bsdcard|\btf card|\bmicrosd|\bsd_card|\bmicro_sd",
    ),
    (
        "rtc",
        r"\brtc\b|real.time clock|\bds1307|\bds3231|\bpcf85(63|23)|\bmcp794\d\d|\brv-?\d{4}",
    ),
    ("eeprom", r"\beeprom\b|\b24(lc|c|aa)\d+|\bat24c"),
    ("flash", r"\bflash\b|\bw25q\d|\bat25|\bspi nor"),
    ("ram", r"\bsram\b|\bdram\b|\bsdram\b|\bpsram\b|\bfram\b"),
    ("crystal", r"\bcrystal\b|\bresonator\b|\bxtal\b"),
    ("oscillator", r"\boscillator\b"),
    ("resistor", r"\bresistor\b"),
    ("capacitor", r"\bcapacitor\b"),
    ("inductor", r"\binductor\b|\bchoke\b|\bferrite"),
    ("diode", r"\bdiode\b|\brectifier\b|\bschottky\b"),
    ("schottky", r"schottky (barrier )?(diode|rectifier)|\bd_schottky|\b1n58\d\d|\bbat\d\d|\bss[1-3]\d\b"),
    ("zener", r"\bzener\b"),
    ("tvs", r"\btvs\b|transient voltage"),
    (
        "bjt",
        r"\bnpn\b|\bpnp\b|\bbjt\b|\b2n3904|\b2n3906|\b2n2222|\bpn2222|\bbc54\d|\bbc55\d|\bs8050|\bs8550",
    ),
    ("npn", r"\bnpn\b|\b2n3904|\b2n2222|\bpn2222|\bbc54[6-9]|\bs8050|\btip12[0-2]\b"),
    ("pnp", r"\bpnp\b|\b2n3906|\bbc55[6-9]|\bs8550|\btip12[5-7]\b"),
    (
        "mosfet",
        r"\bmosfet\b|\bn-?channel\b|\bp-?channel\b|\birlz|\birf\d|\bao34\d\d|\b2n7002|\bbss138",
    ),
    ("darlington", r"darlington|\buln200\d|\btip12\d"),
    (
        "opamp",
        r"\bop-?amp\b|operational amplifier|\blm358|\blm324|\bmcp600\d|\btl07\d|\bne5532",
    ),
    ("comparator", r"\bcomparator\b|\blm393|\blm339"),
    ("adc", r"\badc\b|analog.to.digital|\bads1[01]\d\d|\bmcp30\d\d|\bmcp320\d"),
    ("dac", r"\bdac\b|digital.to.analog|\bmcp47\d\d|\bmcp49\d\d"),
    ("timer", r"\btimer\b|\bne555|\b555\b|\blm555|\bicm7555"),
    ("shift-register", r"shift register|\b74\w*595|\b74\w*165|\b74\w*164|\bcd4094"),
    (
        "io-expander",
        r"i/?o expander|port expander|\bmcp230\d\d|\bpcf857\d|\bpca95\d\d|\btca95\d\d",
    ),
    (
        "level-shifter",
        r"level.shift|level.translat|voltage.translat|\btxs010\d|\btxb010\d",
    ),
    ("mux", r"\bmultiplexer\b|\bmux\b|\bdemultiplexer\b|\bcd4051|\bcd74hc4067"),
    (
        "logic",
        r"\bgate\b|\bflip-?flop\b|\blatch\b|\binverter\b|\b74(hc|hct|lvc|ls|ac|ahc)\d",
    ),
    ("uart", r"\buart\b|\busb.to.serial|\bch340|\bcp210\d|\bft232|\brs-?232"),
    ("rs485", r"\brs-?485\b|\brs-?422\b|\bmax485|\bsp3485"),
    (
        "can",
        r"\bcan bus|\bcan transceiver|\bmcp2515|\bmcp2551|\btja10\d\d|\bsn65hvd2[3-5]",
    ),
    ("ethernet", r"\bethernet\b|\bw5500|\benc28j60|\brj-?45|\b10/100"),
    ("nfc", r"\bnfc\b|\brfid\b|\bpn532|\bmfrc522|\brc522|\bst25|\bntag"),
    ("rfid", r"\brfid\b|\bmfrc522|\brc522|\bem4100|\b125 ?khz"),
    ("isolator", r"\bisolator\b|\bdigital isolat"),
    ("transformer", r"\btransformer\b"),
    ("fuse", r"\bfuse\b|\bpolyfuse|\bptc\b"),
    ("esd", r"\besd\b"),
    ("crystal", r"\bcrystal\b"),
    (
        "header",
        r"\bpin ?header|\bpinheader|\bpin ?socket|\bpinsocket|\bheader\b|\bbreakout",
    ),
    ("terminal-block", r"terminal ?block|screw terminal|\bterminalblock|\bmkds\b"),
    (
        "jst",
        r"\bjst\b|\bjst_|\bph\b.*\b2\.0 ?mm|\bxh\b|\bs\d+b-ph|\bb\d+b-(ph|xh)|\bqwiic\b|\bstemma\b",
    ),
    ("qwiic", r"\bqwiic\b|\bstemma qt\b"),
    ("test-point", r"\btest ?point|\btestpoint"),
    ("mounting", r"\bmounting ?hole|\bmountinghole|\bstandoff\b|\bfiducial\b"),
    ("jumper", r"\bjumper\b|\bsolder ?jumper|\bsolderjumper"),
    (
        "power-supervisor",
        r"\bsupervisor\b|\breset ic\b|\bvoltage detector|\bvoltage monitor",
    ),
    (
        "led-driver",
        r"\bled driver\b|\bconstant.current led|\bws2811\b|\btlc59\d\d|\bpca9685|\bht16k33",
    ),
    ("pwm-driver", r"\bpwm driver|\bpca9685|\btlc59\d\d"),
    ("gate-driver", r"\bgate driver|\bmosfet driver|\bhalf.bridge driver"),
    ("thermal", r"\bpeltier|\bheater\b|\bheating\b"),
    (
        "security",
        r"\bsecure element|\bcrypto(graphic)? (chip|authentication)|\batecc\d|\btpm\b",
    ),
    ("video", r"\bvideo\b|\bhdmi\b|\bvga\b|\bcomposite\b"),
    (
        "mcu-board",
        r"\barduino\b|\braspberry pi pico|raspberry ?pi ?pico|\bpico ?w\b|\bwemos\b|\bd1 mini|\bxiao\b|\bpro micro|\bfeather\b|\bteensy|\bnodemcu|\bdevkit|\bqt py|\bblack ?pill|\bblue ?pill",
    ),
)

# footprint name -> package tags (ranking and "I want through-hole" asks)
_THT = re.compile(
    r"THT|_TH\b|DIP-\d|TO-92|TO-220|TO-3|DO-41|DO-35|DO-15|DO-201|_Axial_|_Radial_|PinHeader|PinSocket|TerminalBlock|_Vertical|_Horizontal|SW_PUSH_6mm|SIP-\d|_P2\.54mm",
    re.I,
)
_SMD = re.compile(
    r"SMD|SMT|SOIC|SOT-|SOT\d|SSOP|TSSOP|QFN|QFP|DFN|BGA|LGA|WLCSP|SON-|MSOP|_0402|_0603|_0805|_1206|SOD-|SMA\b|SMB\b|SMC\b|PowerPAK|D2PAK|DPAK|TO-252|TO-263|MLF|VSON|WSON|UFQFPN|LQFP|TQFP|PLCC",
    re.I,
)
_CONN_N = re.compile(r"Conn_(\d\d)x(\d\d)")
_FP_PINS = re.compile(r"_(\d)x(\d{1,2})(?:_|$)")

_TEXT_RX = tuple((t, re.compile(rx)) for t, rx in TEXT_RULES)
_TEXT_BY_TAG: dict[str, re.Pattern[str]] = {}
for _t, _rx in _TEXT_RX:
    _TEXT_BY_TAG.setdefault(_t, _rx)
_EXTRA_PREFIX = re.compile(r"^(SparkFun-|Gekkio_|JLCPCB-|LCSC_|GH_)")


# end-product tags ("this IS a relay", "this IS a buzzer") that free text may only give a discrete or module piece:
# an IC whose description says "relay driver", "RGB video" or "piezo horn driver" is not the relay, LED or buzzer
END_TAGS = frozenset(
    {
        "relay", "motor", "servo", "buzzer", "speaker", "led", "rgb-led", "addressable-led", "battery",
        "coin-cell", "button", "keyswitch", "switch", "mic", "sound", "display-oled", "display-lcd",
        "display-epaper", "display-7seg", "fan", "pump", "solenoid", "vibration-motor", "stepper", "laser",
        "joystick", "keypad", "potentiometer", "encoder", "antenna", "camera", "usb", "usb-c", "audio-jack",
        "sd-card", "power-in", "header", "jst", "terminal-block", "qwiic",
    }
)
# libraries that hold discrete parts, connectors, sensors and modules (free-text end tags allowed)
DEVICE_LIBS = re.compile(
    r"^(Device|Connector|Switch|Relay|Motor|LED|Sensor|Display_Graphic|Display_Character|Battery\b|RF_GPS|RF_Module|"
    r"RF_Bluetooth|RF_WiFi|RF_ZigBee|RF_GSM|RF_NFC|RF_RFID|MCU_Module|Jumper|Mechanical|Fuse|"
    r"SparkFun-(?!IC-)|Gekkio_(Device|Connector|Switch)|JLCPCB|LCSC_|GH_|marbastlib|kiswitch|ai03|cdfer|seeed|wemos|Seeed|Wemos|"
    r"Transistor_BJT|Transistor_FET|Diode|Oscillator|Crystal)"
)
# the footprint names the part for generic connector / device symbols; only these tags may come from it
FP_TAGS = (
    "usb", "usb-c", "jst", "terminal-block", "header", "sd-card", "audio-jack", "power-in", "battery",
    "coin-cell", "buzzer", "relay", "crystal", "potentiometer", "encoder", "qwiic",
)
FP_LIBS = re.compile(r"^(Connector|Device|Switch|Gekkio_Connector|SparkFun-Connector|SparkFun-Electromechanical)")
_SENSE = frozenset(
    {
        "temperature", "humidity", "pressure", "gas", "light", "distance", "imu", "accelerometer", "gyroscope",
        "magnetometer", "mic", "pir", "soil", "load-cell", "touch", "hall", "color-sensor", "uv", "particulate",
        "pulse", "proximity", "water", "tilt",
    }
)
_MEM = frozenset({"flash", "ram", "eeprom"})


@functools.lru_cache(maxsize=None)
def _lib_tags(lib: str) -> tuple[str, ...]:
    # a few hundred libraries, ~40k pieces: cached (library load spent 1.7 s here re-scanning LIB_TAGS per piece)
    out: list[str] = []
    for base in {lib, _EXTRA_PREFIX.sub("", lib)}:
        for pre, tags in LIB_TAGS:
            if base.startswith(pre):
                out += tags
                break
    return tuple(out)


@functools.lru_cache(maxsize=65536)
def _symbol_tags(lib: str, name: str, descr: str, kw: str, family: str) -> frozenset[str]:
    """Tags from what the symbol says about itself (shared by all footprint variants of one symbol)."""
    tags: set[str] = set(_lib_tags(lib)) | set(FAMILY_TAGS.get(family, ()))
    given = set(tags)
    text = " ".join((lib, name, name.replace("_", " "), descr, kw, family)).lower()
    for tag, rx in _TEXT_RX:
        if tag not in tags and rx.search(text):
            tags.add(tag)
    if not DEVICE_LIBS.match(lib):
        tags -= END_TAGS - given
    if "mcu" in tags and not lib.startswith("Memory") and not given & _MEM:
        # a microcontroller's description lists its own flash, RAM and EEPROM
        tags -= _MEM - given
    if "mcu-board" in tags:
        tags.add("mcu")
    if "buzzer" in tags:
        tags.discard("crystal")  # KiCad files buzzers under "quartz resonator ceramic"
    if tags & _SENSE and not tags & {"mcu", "regulator", "connector"}:
        tags.add("sensor")
    return frozenset(tags)


def tags_for(p: dict[str, Any]) -> list[str]:
    """Deterministic, sorted function tags for one library record."""
    lib, _, name = p["lib_id"].partition(":")
    fname = p.get("footprint", "").split(":", 1)[-1]
    tags = set(_symbol_tags(lib, name, p.get("descr", ""), p.get("kw", ""), p.get("family", "")))
    if FP_LIBS.match(lib):
        ftext = fname.replace("_", " ").lower()
        for tag in FP_TAGS:
            if tag not in tags and _TEXT_BY_TAG[tag].search(ftext):
                tags.add(tag)
    if "connector" in tags or lib.startswith("Connector"):
        tags.add("connector")
        m = _CONN_N.search(name) or _FP_PINS.search(fname)
        if m:
            tags.add(f"connector-{int(m.group(1)) * int(m.group(2))}")
        elif p.get("pins"):
            tags.add(f"connector-{len(p['pins'])}")
    fp = p.get("footprint", "")  # the library nickname says it too: LED_THT, Resistor_SMD
    if _THT.search(fp):
        tags.add("tht")
    elif _SMD.search(fp):
        tags.add("smd")
    return sorted(tags)


def primary_tags(p: dict[str, Any]) -> frozenset[str]:
    """Tags a piece gets from its library and family alone (what it IS, not what its description mentions)."""
    lib = p["lib_id"].split(":", 1)[0]
    name = p["lib_id"].split(":", 1)[-1]
    extra = {"button"} if _BUTTON_SYMBOL.match(name) else set()
    if lib == "Transistor_BJT":
        # a discrete BJT is primarily its polarity (an optocoupler's NPN output is not)
        extra |= {t for t in ("npn", "pnp") if t in set(p.get("tags") or ())}
    return frozenset(_lib_tags(lib)) | frozenset(FAMILY_TAGS.get(p.get("family", ""), ())) | extra


_BUTTON_SYMBOL = re.compile(r"SW_Push|SW_Tactile|SPST_Push|Button", re.I)
