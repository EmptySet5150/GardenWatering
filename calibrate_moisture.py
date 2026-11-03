#!/usr/bin/python3

"""
Prints the moisture sensor's raw reading once a second so you can
calibrate it. Stop autoWatering.py first, then:

1. Hold the sensor in dry air and write down the reading -> dry_reading
2. Put it in a glass of water up to the line and write it down -> wet_reading
3. Put both numbers in the [moisture_sensor] section of config.ini

Press Ctrl+C to stop.
"""

from time import sleep
import configparser
import os
from smbus2 import SMBus

ADS1115_ADDRESS = 0x48

config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini'))
dryReading = config.getint('moisture_sensor', 'dry_reading', fallback=20000)
wetReading = config.getint('moisture_sensor', 'wet_reading', fallback=8800)


# Same reading as autoWatering.py: one reading of A0 on the ADS1115, range +-4.096V
def read_moisture_raw(i2c):
    i2c.write_i2c_block_data(ADS1115_ADDRESS, 0x01, [0xC3, 0x83])
    sleep(.01)
    high, low = i2c.read_i2c_block_data(ADS1115_ADDRESS, 0x00, 2)
    raw = (high << 8) | low
    return raw - 65536 if raw > 32767 else raw


with SMBus(1) as i2c:
    print("Raw reading   Volts   Moisture with the current config.ini")
    try:
        while True:
            raw = read_moisture_raw(i2c)
            percent = (dryReading - raw) * 100 / (dryReading - wetReading)
            print('%11d   %5.2f   %3d%%' % (raw, raw * 4.096 / 32768, max(0, min(100, round(percent)))))
            sleep(1)
    except KeyboardInterrupt:
        pass
