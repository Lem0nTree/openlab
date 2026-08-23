"""Idempotently add a practical 100-module inventory for BUILD testing."""

import argparse
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from .db import SessionLocal
from .models import Capability, Lab, Location, Membership, Thing, ThingAlias, ThingInterface, User
from .services import apply_movement, create_thing

SEED_VERSION = 1


@dataclass(frozen=True)
class ModuleSeed:
    key: str
    name: str
    category: str
    description: str
    aliases: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()


def split_parts(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def m(key: str, name: str, category: str, description: str, aliases: str = "", capabilities: str = "", interfaces: str = "") -> ModuleSeed:
    return ModuleSeed(key, name, category, description, split_parts(aliases), split_parts(capabilities), split_parts(interfaces))


COMMON_MODULES = (
    m("esp32-devkit-v1", "ESP32 DevKit V1", "board", "Wi-Fi and Bluetooth microcontroller development board for connected sensors, controls, and automation.", "ESP32 development board", "microcontroller|wireless control|sensor integration", "Wi-Fi|Bluetooth|I2C|SPI|UART"),
    m("esp32-c3-supermini", "ESP32-C3 SuperMini", "board", "Compact Wi-Fi and Bluetooth LE microcontroller board for small connected projects.", "ESP32 C3 Super Mini", "microcontroller|wireless control|compact prototyping", "Wi-Fi|Bluetooth LE|I2C|SPI|UART"),
    m("esp32-s3-devkitc-1", "ESP32-S3 DevKitC-1", "board", "Wi-Fi and Bluetooth LE development board for connected sensing and embedded processing.", "ESP32 S3 development board", "microcontroller|wireless control|embedded processing", "Wi-Fi|Bluetooth LE|I2C|SPI|UART"),
    m("esp8266-nodemcu", "ESP8266 NodeMCU", "board", "Wi-Fi microcontroller development board for connected sensors and simple automation.", "NodeMCU ESP8266", "microcontroller|wireless control|sensor integration", "Wi-Fi|I2C|SPI|UART"),
    m("wemos-d1-mini", "Wemos D1 Mini", "board", "Compact ESP8266 Wi-Fi development board for connected prototypes.", "D1 Mini", "microcontroller|wireless control|compact prototyping", "Wi-Fi|I2C|SPI|UART"),
    m("arduino-uno-r3", "Arduino Uno R3", "board", "General-purpose microcontroller development board for sensors, actuators, and learning electronics.", "Uno R3", "microcontroller|prototyping|sensor integration", "I2C|SPI|UART"),
    m("arduino-nano", "Arduino Nano", "board", "Compact general-purpose microcontroller board for breadboard prototypes.", "Nano compatible board", "microcontroller|compact prototyping|sensor integration", "I2C|SPI|UART"),
    m("arduino-pro-mini", "Arduino Pro Mini", "board", "Small microcontroller board for embedded prototypes and sensor nodes.", "Pro Mini", "microcontroller|compact prototyping|sensor integration", "I2C|SPI|UART"),
    m("raspberry-pi-pico", "Raspberry Pi Pico", "board", "RP2040 microcontroller development board for control, sensing, and embedded projects.", "Pico RP2040", "microcontroller|embedded control|sensor integration", "I2C|SPI|UART"),
    m("raspberry-pi-pico-w", "Raspberry Pi Pico W", "board", "Wireless RP2040 microcontroller board for connected control and sensing projects.", "Pico W", "microcontroller|wireless control|sensor integration", "Wi-Fi|Bluetooth|I2C|SPI|UART"),
    m("stm32-blue-pill", "STM32 Blue Pill", "board", "Compact STM32 microcontroller board for embedded control and real-time peripherals.", "Blue Pill STM32", "microcontroller|embedded control|real-time processing", "I2C|SPI|UART"),
    m("xiao-rp2040", "Seeed XIAO RP2040", "board", "Tiny RP2040 microcontroller board for space-constrained embedded projects.", "XIAO RP2040", "microcontroller|compact prototyping|embedded control", "I2C|SPI|UART"),
    m("xiao-esp32c3", "Seeed XIAO ESP32C3", "board", "Tiny Wi-Fi and Bluetooth LE microcontroller board for compact connected devices.", "XIAO ESP32C3", "microcontroller|wireless control|compact prototyping", "Wi-Fi|Bluetooth LE|I2C|SPI|UART"),
    m("microbit-v2", "BBC micro:bit v2", "board", "Educational microcontroller board with onboard sensing for interactive projects.", "micro:bit", "microcontroller|education|interactive control", "Bluetooth|I2C|SPI|UART"),
    m("teensy-4", "Teensy 4.0", "board", "High-performance microcontroller development board for real-time control and signal processing.", "Teensy 4", "microcontroller|real-time processing|signal processing", "I2C|SPI|UART"),
    m("mcp23017", "MCP23017 GPIO Expander Module", "module", "Module that adds sixteen general-purpose digital input and output channels over I2C.", "MCP23017 I/O expander", "GPIO expansion|digital input|digital output", "I2C"),
    m("pcf8574", "PCF8574 GPIO Expander Module", "module", "Module that adds eight general-purpose digital input and output channels over I2C.", "PCF8574 I/O expander", "GPIO expansion|digital input|digital output", "I2C"),
    m("74hc595", "74HC595 Shift Register Module", "module", "Serial-to-parallel output module for controlling multiple digital outputs with few controller pins.", "74HC595 module", "digital output expansion|LED control", "SPI-like serial"),
    m("ads1115", "ADS1115 ADC Module", "module", "Multi-channel analog-to-digital converter module for measuring analog sensors over I2C.", "ADS1115 analog converter", "analog measurement|sensor acquisition", "I2C"),
    m("mcp3008", "MCP3008 ADC Module", "module", "Multi-channel analog-to-digital converter module for analog sensor acquisition over SPI.", "MCP3008 analog converter", "analog measurement|sensor acquisition", "SPI"),
    m("pca9685", "PCA9685 PWM Driver Module", "module", "Multi-channel PWM controller module for servos, LEDs, and other pulse-controlled outputs.", "PCA9685 servo driver", "PWM output|servo control|LED dimming", "I2C"),
    m("ds3231", "DS3231 RTC Module", "module", "Real-time clock module for keeping date and time in embedded projects.", "DS3231 clock", "timekeeping|scheduling", "I2C"),
    m("ds1307", "DS1307 RTC Module", "module", "Real-time clock module for basic date and time keeping.", "DS1307 clock", "timekeeping|scheduling", "I2C"),
    m("microsd", "MicroSD Card Module", "module", "Removable storage module for logging sensor data and storing files.", "SD card module", "data logging|file storage", "SPI"),
    m("ch340", "CH340 USB-to-TTL Module", "module", "USB serial adapter module for programming and communicating with embedded boards.", "CH340 serial adapter", "USB serial bridge|device programming", "USB|UART"),
    m("cp2102", "CP2102 USB-to-TTL Module", "module", "USB serial adapter module for programming and debugging embedded devices.", "CP2102 serial adapter", "USB serial bridge|device programming", "USB|UART"),
    m("ft232rl", "FT232RL USB-to-TTL Module", "module", "USB serial adapter module for embedded device communication and programming.", "FT232 serial adapter", "USB serial bridge|device programming", "USB|UART"),
    m("logic-level-4", "4-Channel Logic Level Converter", "module", "Bidirectional logic level conversion module for connecting digital devices using different signal levels.", "4 channel level shifter", "logic level conversion|signal interfacing", "digital"),
    m("txs0108e", "TXS0108E Logic Level Converter", "module", "Multi-channel bidirectional logic level conversion module for mixed-level digital buses.", "TXS0108E level shifter", "logic level conversion|signal interfacing", "digital"),
    m("tca9548a", "TCA9548A I2C Multiplexer", "module", "I2C bus multiplexer for connecting multiple devices that share an address.", "TCA9548A I2C switch", "I2C expansion|address conflict handling", "I2C"),
    m("dht11", "DHT11 Temperature and Humidity Sensor", "sensor", "Basic digital sensor module for measuring ambient temperature and relative humidity.", "DHT11 module", "temperature sensing|humidity sensing", "single-wire digital"),
    m("dht22", "DHT22 Temperature and Humidity Sensor", "sensor", "Digital sensor module for ambient temperature and relative humidity monitoring.", "AM2302", "temperature sensing|humidity sensing", "single-wire digital"),
    m("bme280", "BME280 Environmental Sensor", "sensor", "Environmental sensor module for temperature, humidity, and barometric pressure measurement.", "BME280 module", "temperature sensing|humidity sensing|pressure sensing", "I2C|SPI"),
    m("bmp280", "BMP280 Pressure Sensor", "sensor", "Barometric pressure and temperature sensor module for weather and altitude projects.", "BMP280 module", "pressure sensing|temperature sensing|altitude estimation", "I2C|SPI"),
    m("sht31", "SHT31 Temperature and Humidity Sensor", "sensor", "Digital environmental sensor module for temperature and humidity monitoring.", "SHT31 module", "temperature sensing|humidity sensing", "I2C"),
    m("ds18b20", "DS18B20 Temperature Sensor Module", "sensor", "Digital temperature sensor module for distributed and probe-based measurements.", "DS18B20 module", "temperature sensing", "1-Wire"),
    m("hc-sr04", "HC-SR04 Ultrasonic Distance Sensor", "sensor", "Ultrasonic ranging module for measuring distance and detecting nearby objects.", "HC SR04", "distance sensing|object detection", "trigger/echo"),
    m("vl53l0x", "VL53L0X Time-of-Flight Sensor", "sensor", "Optical time-of-flight distance sensor module for short-range ranging.", "VL53L0X distance module", "distance sensing|proximity detection", "I2C"),
    m("hc-sr501", "HC-SR501 PIR Motion Sensor", "sensor", "Passive infrared motion detector module for occupancy and movement sensing.", "HC SR501", "motion detection|occupancy sensing", "digital"),
    m("rcwl-0516", "RCWL-0516 Microwave Motion Sensor", "sensor", "Microwave radar motion detector module for presence and movement sensing.", "RCWL 0516", "motion detection|presence sensing", "digital"),
    m("mpu6050", "MPU6050 IMU Module", "sensor", "Motion sensor module combining acceleration and angular-rate measurement.", "GY-521", "acceleration sensing|gyroscope sensing|motion tracking", "I2C"),
    m("mpu9250", "MPU9250 IMU Module", "sensor", "Motion sensor module for acceleration, angular rate, and magnetic field measurement.", "MPU-9250", "acceleration sensing|gyroscope sensing|magnetic sensing", "I2C|SPI"),
    m("adxl345", "ADXL345 Accelerometer Module", "sensor", "Three-axis accelerometer module for motion, tilt, and vibration measurement.", "ADXL345 module", "acceleration sensing|tilt sensing|vibration sensing", "I2C|SPI"),
    m("lis3dh", "LIS3DH Accelerometer Module", "sensor", "Three-axis accelerometer module for motion and orientation sensing.", "LIS3DH module", "acceleration sensing|motion detection|tilt sensing", "I2C|SPI"),
    m("qmc5883l", "QMC5883L Magnetometer Module", "sensor", "Three-axis magnetic field sensor module for compass and heading projects.", "QMC5883L compass", "magnetic sensing|heading estimation", "I2C"),
    m("max30102", "MAX30102 Pulse Oximeter Sensor", "sensor", "Optical pulse sensing module for experimental heart-rate and blood-oxygen signal acquisition.", "MAX30102 module", "optical pulse sensing|biometric signal acquisition", "I2C"),
    m("max6675", "MAX6675 Thermocouple Module", "sensor", "Thermocouple interface module for high-temperature measurement.", "MAX6675 converter", "temperature sensing|thermocouple reading", "SPI"),
    m("hx711", "HX711 Load Cell Amplifier", "sensor", "Precision load-cell interface module for weight and force measurement.", "HX711 module", "weight sensing|force measurement|signal amplification", "two-wire digital"),
    m("ina219", "INA219 Current Sensor Module", "sensor", "Current and bus-voltage monitoring module for measuring DC power use.", "INA219 monitor", "current sensing|voltage sensing|power monitoring", "I2C"),
    m("ina226", "INA226 Current Sensor Module", "sensor", "Digital current and voltage monitor module for DC power measurement.", "INA226 monitor", "current sensing|voltage sensing|power monitoring", "I2C"),
    m("acs712", "ACS712 Current Sensor Module", "sensor", "Hall-effect current sensing module for measuring electrical current.", "ACS712 module", "current sensing", "analog"),
    m("bh1750", "BH1750 Light Sensor Module", "sensor", "Digital ambient light sensor module for illumination measurement.", "BH1750 lux sensor", "ambient light sensing|illumination measurement", "I2C"),
    m("tsl2561", "TSL2561 Light Sensor Module", "sensor", "Digital light sensor module for measuring ambient illumination.", "TSL2561 module", "ambient light sensing|illumination measurement", "I2C"),
    m("apds9960", "APDS-9960 Gesture Sensor", "sensor", "Optical sensor module for gesture, proximity, color, and ambient light detection.", "APDS9960 module", "gesture sensing|proximity sensing|color sensing|ambient light sensing", "I2C"),
    m("mq2", "MQ-2 Gas Sensor Module", "sensor", "General gas and smoke sensing module for experimental air-quality alarms.", "MQ2 module", "gas sensing|smoke detection", "analog|digital"),
    m("mq135", "MQ-135 Air Quality Sensor Module", "sensor", "General air-quality gas sensor module for experimental environmental monitoring.", "MQ135 module", "gas sensing|air quality monitoring", "analog|digital"),
    m("soil-capacitive", "Capacitive Soil Moisture Sensor", "sensor", "Capacitive sensor module for monitoring relative soil moisture in plant projects.", "capacitive soil sensor", "soil moisture sensing|plant monitoring", "analog"),
    m("rain-sensor", "Rain Sensor Module", "sensor", "Surface wetness sensor module for detecting rain or water droplets.", "raindrop sensor", "rain detection|water sensing", "analog|digital"),
    m("sw420", "SW-420 Vibration Sensor", "sensor", "Vibration switch module for detecting shock and movement.", "SW420 module", "vibration sensing|shock detection", "digital"),
    m("ky038", "KY-038 Sound Sensor", "sensor", "Microphone sensor module for detecting relative sound levels and sound events.", "sound detection module", "sound detection|audio level sensing", "analog|digital"),
    m("ssd1306", "SSD1306 OLED Display", "module", "Compact monochrome OLED display module for text, icons, and status graphics.", "0.96 inch OLED", "visual display|status output", "I2C|SPI"),
    m("sh1106", "SH1106 OLED Display", "module", "Monochrome OLED display module for compact user interfaces and status graphics.", "1.3 inch OLED", "visual display|status output", "I2C|SPI"),
    m("lcd1602", "1602 I2C LCD Module", "module", "Two-line character display module for simple text and status output.", "16x2 LCD", "text display|status output", "I2C"),
    m("lcd2004", "2004 I2C LCD Module", "module", "Four-line character display module for menus and status information.", "20x4 LCD", "text display|status output", "I2C"),
    m("ili9341", "ILI9341 TFT Display Module", "module", "Color graphical display module for embedded interfaces and dashboards.", "ILI9341 TFT", "color display|graphical interface", "SPI"),
    m("st7735", "ST7735 TFT Display Module", "module", "Compact color graphical display module for embedded user interfaces.", "ST7735 TFT", "color display|graphical interface", "SPI"),
    m("max7219-matrix", "MAX7219 8x8 LED Matrix", "module", "Driver-controlled LED matrix module for symbols, animation, and numeric output.", "8x8 LED matrix", "LED display|animation output", "SPI-like serial"),
    m("tm1637", "TM1637 4-Digit Display", "module", "Four-digit segmented display module for counters, clocks, and numeric readings.", "TM1637 display", "numeric display|status output", "two-wire serial"),
    m("ws2812-ring8", "WS2812B 8-LED Ring", "module", "Individually addressable RGB LED ring for indicators and lighting effects.", "8 pixel NeoPixel ring", "RGB lighting|visual indicator", "single-wire digital"),
    m("ws2812-ring16", "WS2812B 16-LED Ring", "module", "Individually addressable RGB LED ring for indicators, gauges, and lighting effects.", "16 pixel NeoPixel ring", "RGB lighting|visual indicator", "single-wire digital"),
    m("nrf24l01", "nRF24L01+ Radio Module", "module", "Short-range digital radio transceiver module for low-power device-to-device communication.", "nRF24L01", "wireless communication|sensor networking", "SPI"),
    m("nrf24l01-palna", "nRF24L01+ PA/LNA Radio Module", "module", "Amplified short-range radio transceiver module for device-to-device communication.", "nRF24L01 PA LNA", "wireless communication|sensor networking", "SPI"),
    m("hc05", "HC-05 Bluetooth Module", "module", "Bluetooth serial communication module for wireless links to embedded controllers.", "HC05", "wireless serial|device communication", "Bluetooth|UART"),
    m("hc06", "HC-06 Bluetooth Module", "module", "Bluetooth serial communication module for simple wireless embedded links.", "HC06", "wireless serial|device communication", "Bluetooth|UART"),
    m("sim800l", "SIM800L GSM/GPRS Module", "module", "Cellular communication module for text messaging, calls, and packet data projects.", "SIM800L", "cellular communication|SMS|packet data", "UART"),
    m("a6-gsm", "A6 GSM/GPRS Module", "module", "Cellular communication module for messaging, calls, and packet data.", "A6 GSM", "cellular communication|SMS|packet data", "UART"),
    m("neo6m", "NEO-6M GPS Module", "module", "Satellite positioning receiver module for location, speed, and time data.", "NEO6M", "positioning|location tracking|time reference", "UART"),
    m("sx1278", "SX1278 Ra-02 LoRa Module", "module", "Long-range low-data-rate radio module for remote sensors and telemetry.", "Ra-02 LoRa", "long-range communication|telemetry|sensor networking", "SPI"),
    m("rfm95", "RFM95 LoRa Module", "module", "Long-range low-data-rate radio transceiver module for telemetry and sensor networks.", "RFM95W", "long-range communication|telemetry|sensor networking", "SPI"),
    m("mcp2515", "MCP2515 CAN Bus Module", "module", "CAN controller and transceiver module for connecting embedded devices to a CAN network.", "MCP2515 CAN", "CAN communication|vehicle networking|device networking", "SPI|CAN"),
    m("max485", "MAX485 RS485 Module", "module", "Differential serial transceiver module for robust wired communication over longer distances.", "MAX485 module", "RS485 communication|wired networking", "UART|RS485"),
    m("w5500", "W5500 Ethernet Module", "module", "Wired Ethernet controller module for network-connected embedded projects.", "W5500 network module", "wired networking|Ethernet communication", "SPI|Ethernet"),
    m("l298n", "L298N Motor Driver Module", "module", "Dual-channel motor driver module for controlling small DC motors and basic stepper motors.", "L298N driver", "DC motor control|stepper control|bidirectional drive", "digital|PWM"),
    m("tb6612fng", "TB6612FNG Motor Driver Module", "module", "Dual-channel motor driver module for efficient bidirectional DC motor control.", "TB6612 motor driver", "DC motor control|bidirectional drive", "digital|PWM"),
    m("drv8833", "DRV8833 Motor Driver Module", "module", "Compact dual-channel motor driver module for DC motors and small steppers.", "DRV8833 driver", "DC motor control|stepper control|bidirectional drive", "digital|PWM"),
    m("a4988", "A4988 Stepper Driver Module", "module", "Step-and-direction motor driver module for bipolar stepper motors.", "A4988 driver", "stepper motor control|microstepping", "step/direction"),
    m("drv8825", "DRV8825 Stepper Driver Module", "module", "Step-and-direction motor driver module for bipolar stepper motors and motion projects.", "DRV8825 driver", "stepper motor control|microstepping", "step/direction"),
    m("uln2003", "ULN2003 Stepper Driver Module", "module", "Transistor driver module commonly used to control small unipolar stepper motors.", "ULN2003 driver", "stepper motor control|load switching", "digital"),
    m("sg90", "SG90 Micro Servo", "module", "Small position-controlled servo actuator for lightweight mechanisms and robotics.", "SG90 servo", "position control|robotic actuation", "PWM"),
    m("relay1", "1-Channel Relay Module", "module", "Single-channel electromechanical switching module for controller-operated loads.", "single relay module", "load switching|electrical isolation", "digital"),
    m("relay2", "2-Channel Relay Module", "module", "Two-channel electromechanical switching module for controller-operated loads.", "dual relay module", "load switching|electrical isolation", "digital"),
    m("relay4", "4-Channel Relay Module", "module", "Four-channel electromechanical switching module for multiple controller-operated loads.", "quad relay module", "load switching|electrical isolation", "digital"),
    m("mosfet-trigger", "MOSFET Trigger Module", "module", "Electronic switching module for controller-driven DC loads such as lights, pumps, and fans.", "MOSFET switch module", "DC load switching|PWM load control", "digital|PWM"),
    m("irlz44n-module", "IRLZ44N MOSFET Module", "module", "Power MOSFET switching module for controller-driven DC loads.", "IRLZ44N switch", "DC load switching|PWM load control", "digital|PWM"),
    m("lm2596", "LM2596 Buck Converter Module", "power", "Adjustable step-down power converter module for efficiently reducing a DC supply.", "LM2596 converter", "DC voltage step-down|power conversion", "DC power"),
    m("mt3608", "MT3608 Boost Converter Module", "power", "Adjustable step-up power converter module for increasing a DC supply.", "MT3608 converter", "DC voltage step-up|power conversion", "DC power"),
    m("ams1117-33", "AMS1117 3.3V Regulator Module", "power", "Linear regulator module for providing a fixed 3.3-volt supply in low-power projects.", "AMS1117 regulator", "voltage regulation|3.3V power", "DC power"),
    m("tp4056-usbc", "TP4056 USB-C Charger Module", "power", "Single-cell lithium battery charging module with a USB-C power connector.", "TP4056 Type-C", "battery charging|portable power", "USB-C|DC power"),
    m("xl4015", "XL4015 Buck Converter Module", "power", "Adjustable step-down DC power converter module for higher-current loads.", "XL4015 converter", "DC voltage step-down|power conversion", "DC power"),
    m("mb102", "MB102 Breadboard Power Supply", "power", "Breadboard power adapter module for supplying common prototype power rails.", "breadboard power module", "breadboard power|prototype power distribution", "DC power"),
)


def seed_common_modules(lab_id: str | None = None, quantity: Decimal = Decimal(3)) -> dict[str, int | str]:
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    with SessionLocal() as db:
        labs = list(db.scalars(select(Lab).order_by(Lab.created_at)).all())
        if lab_id:
            labs = [lab for lab in labs if lab.id == lab_id]
        if len(labs) != 1:
            raise RuntimeError("Select exactly one lab with --lab-id")
        lab = labs[0]
        membership = db.scalar(select(Membership).where(Membership.lab_id == lab.id).order_by(Membership.created_at))
        if not membership:
            raise RuntimeError("The selected lab has no member to own seed movements")
        user = db.get(User, membership.user_id)
        if not user:
            raise RuntimeError("The selected lab member no longer exists")

        public_code = f"demo-modules-{lab.id[:8]}"
        location = db.scalar(select(Location).where(Location.public_code == public_code))
        if not location:
            location = Location(lab_id=lab.id, name="Demo modules", public_code=public_code)
            db.add(location)
            db.flush()

        existing = list(db.scalars(select(Thing).where(Thing.lab_id == lab.id)).all())
        by_key = {str(thing.metadata_json.get("demo_seed_key")): thing for thing in existing if thing.metadata_json.get("demo_seed_key")}
        created = 0
        reused = 0
        for module in COMMON_MODULES:
            thing = by_key.get(module.key)
            if not thing:
                thing = create_thing(db, user, name=module.name, category=module.category, manufacturer=None, mpn=None, metadata={"description": module.description, "demo_seed_key": module.key, "demo_seed_version": SEED_VERSION}, aliases=module.aliases)
                created += 1
            else:
                reused += 1
            aliases = set(db.scalars(select(ThingAlias.value).where(ThingAlias.thing_id == thing.id)).all())
            capabilities = set(db.scalars(select(Capability.value).where(Capability.thing_id == thing.id)).all())
            interfaces = set(db.scalars(select(ThingInterface.kind).where(ThingInterface.thing_id == thing.id)).all())
            for alias in set(module.aliases) - aliases:
                db.add(ThingAlias(thing_id=thing.id, value=alias))
            for capability in set(module.capabilities) - capabilities:
                db.add(Capability(thing_id=thing.id, value=capability))
            for interface in set(module.interfaces) - interfaces:
                db.add(ThingInterface(thing_id=thing.id, kind=interface, details={}))
            apply_movement(db, user, thing_id=thing.id, quantity=quantity, movement_type="receive", idempotency_key=f"demo-modules-v{SEED_VERSION}:{module.key}", to_location_id=location.id, note="OpenLab common-module demo inventory")
        db.commit()
        return {"lab": lab.name, "location": location.name, "modules": len(COMMON_MODULES), "created": created, "reused": reused, "quantity_each": str(quantity)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-id")
    parser.add_argument("--quantity", type=Decimal, default=Decimal(3))
    args = parser.parse_args()
    result = seed_common_modules(args.lab_id, args.quantity)
    print(" | ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()
