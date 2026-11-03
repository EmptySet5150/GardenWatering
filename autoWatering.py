"""
This program  reads a soil  moisture sensor
to  determine  when the  garden needs  to be
watered. When the soil is drier than the dry
level it opens a valve  and runs a pump until
the soil reaches the wet level, then waits for
the water to soak in before checking again.
If the water level gets low the pump shuts off
and waits for the tank to be filled, then
waters if the soil is still dry.

Settings are read from config.ini next to this file.
It is meant to run as a systemd service (see garden-watering.service).
"""

# Lets import a few things needed for the system
from RPLCD import CharLCD  # Libaray for the LCD screen
from time import sleep, time
from datetime import datetime, timedelta
from collections import deque
import configparser
import os
import signal
import socket
import sys
from smbus2 import SMBus  # Used to talk to the ADS1115 that reads the moisture sensor
from model import SensorData  # Using peewee to interface with a SQLite database
import RPi.GPIO as GPIO
import DHT22  # Used for Temperature and Humidity sensor
import pigpio  # Only used for DHT22 sensor - Looking into another library

pi = pigpio.pi()  # Only used for DHT22 sensor - Looking into another library

# Setup pin names
GPIO.setmode(GPIO.BOARD)  # Setting the pin number
lcd = CharLCD(cols=20, rows=4, pin_rs=11, pin_e=12, pins_data=[31, 33, 35, 37])
dht22 = DHT22.sensor(pi, 22)  # This library is setup for GPIO numbering
waterValveControl = 22
pumpControl = 32
waterLevel = 16
i2c = SMBus(1)  # I2C bus on pins 3 (SDA) and 5 (SCL)
ADS1115_ADDRESS = 0x48  # The ADS1115's ADDR pin is wired to GND
sensorDatabase = SensorData()

# Setup GPIO pin modes
GPIO.setup(waterValveControl, GPIO.OUT, initial=GPIO.HIGH)  # Set pin as output - relays are active low so start HIGH (off)
GPIO.setup(pumpControl, GPIO.OUT, initial=GPIO.HIGH)  # Set pin as output - relays are active low so start HIGH (off)
GPIO.setup(waterLevel, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Set pin as imput and use internal pull up resistor

# Read the settings from config.ini
config = configparser.ConfigParser()
if not config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')):
    sys.exit("config.ini is missing")
dryLevel = config.getint('watering', 'dry_level')
wetLevel = config.getint('watering', 'wet_level')
soakTime = config.getint('watering', 'soak_minutes')
maxRunTime = config.getint('watering', 'max_run_minutes', fallback=60)
dryReading = config.getint('moisture_sensor', 'dry_reading')
wetReading = config.getint('moisture_sensor', 'wet_reading')

# Setup some global variables
firstLowWater = 1  # Setting this so we only get one reading while water level is low
firstSensorError = 1  # Setting this so we only get one message while the moisture sensor can't be read
lastDhtRead = 0  # Time of the last DHT22 reading
moistureHistory = deque()  # (time, moisture) readings from the last hour, for the soil trend
nextLogTime = datetime.now()  # Sensor readings are saved to the database every ten minutes

# Get the last watering and soak times from the database so a restart doesn't water during a soak
lastWatered = None
soakUntil = datetime.now()
pumpStartTime = datetime.now()
lastRow = sensorDatabase.getLast()
if lastRow is not None and isinstance(lastRow.SoakUntil, datetime):
    lastWatered = lastRow.LastWatering
    soakUntil = lastRow.SoakUntil


# Tells systemd we are ready or still alive - does nothing when not running as a service
def sd_notify(state):
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return
    if address.startswith('@'):
        address = '\0' + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        sock.sendall(state.encode())


# systemd stops the service with SIGTERM - turn it into a normal exit so the finally block runs
def stop_signal(signum, frame):
    sys.exit(0)


signal.signal(signal.SIGTERM, stop_signal)


# Triggers a new DHT22 reading at most once every 3 seconds - reading it faster will hang the sensor
def read_dht22():
    global lastDhtRead
    if time() - lastDhtRead >= 3:
        dht22.trigger()  # Read the sensor then wait because the first reading is null
        sleep(.2)
        lastDhtRead = time()


# Functions to return temperature and humidity readings from the DHT22 sensor
def temperature_status():
    read_dht22()
    temp = '%.1f' % (dht22.temperature() * 1.8 + 32)  # Converting C to F and taking it out to one decimal place
    return temp


def humidity_status():
    read_dht22()
    humid = '%.1f' % (dht22.humidity())  # Taking the humidity out to one decimal place
    return humid


# Reads the moisture sensor's voltage on the ADS1115's A0 input as a raw number
def read_moisture_raw():
    i2c.write_i2c_block_data(ADS1115_ADDRESS, 0x01, [0xC3, 0x83])  # Start one reading of A0, range +-4.096V
    sleep(.01)  # A reading takes about 8 ms
    high, low = i2c.read_i2c_block_data(ADS1115_ADDRESS, 0x00, 2)
    raw = (high << 8) | low
    return raw - 65536 if raw > 32767 else raw


# Returns soil moisture from 0 (dry air) to 100 (in water), or None if the sensor can't be read
def moisture_status():
    global firstSensorError
    try:
        raw = read_moisture_raw()
    except OSError:
        if firstSensorError == 1:
            firstSensorError = firstSensorError + 1
            print("Moisture sensor can't be read - check the ADS1115 wiring")
        return None
    firstSensorError = 1
    percent = (dryReading - raw) * 100 / (dryReading - wetReading)  # The sensor's voltage drops as the soil gets wetter
    return int(round(max(0, min(100, percent))))


# Function starts or stops the pump and opens or closes the water valve and returns its status
def pump_control(cont):
    if cont == 'Start':
        print("Opening Valve")
        GPIO.output(waterValveControl, 0)
        sleep(5)
        print("Starting Pump")
        GPIO.output(pumpControl, 0)
    elif cont == 'Stop':
        print("Stopping Pump")
        GPIO.output(pumpControl, 1)
        sleep(5)
        print("Closing Valve")
        GPIO.output(waterValveControl, 1)


# Functions read the GPIO pins to determine if they are on or off and return a status
def water_status():
    if GPIO.input(waterLevel) == False:
        return 9
    elif GPIO.input(waterLevel) == True:
        return 77


def pump_status():
    if GPIO.input(pumpControl) == False:
        return 'ON'
    elif GPIO.input(pumpControl) == True:
        return 'OFF'


def valve_status():
    if GPIO.input(waterValveControl) == False:
        return 'OPEN'
    elif GPIO.input(waterValveControl) == True:
        return 'SHUT'


# Returns one line saying what the system is doing, for the LCD and the terminal
def watering_state(moisture):
    if pump_status() == 'ON':
        return "Watering to %d%%" % wetLevel
    if water_status() < 10:
        return "Tank low - no water"
    if moisture is None:
        return "Soil sensor ERROR"
    if datetime.now() < soakUntil:
        return "Soaking til " + soakUntil.strftime('%H:%M')
    if moisture < dryLevel:
        return "Dry - watering now"
    return "Soil OK"


# Returns how fast the soil moisture is changing, in percent per hour, over the last hour of readings.
# Returns None until there are at least 10 minutes of readings
def soil_trend(moisture):
    now = datetime.now()
    if moisture is not None and (not moistureHistory or now - moistureHistory[-1][0] >= timedelta(minutes=1)):
        moistureHistory.append((now, moisture))  # Keep at most one reading a minute
    while moistureHistory and now - moistureHistory[0][0] > timedelta(hours=1):
        moistureHistory.popleft()
    if len(moistureHistory) < 2:
        return None
    (firstTime, firstMoisture), (lastTime, lastMoisture) = moistureHistory[0], moistureHistory[-1]
    hours = (lastTime - firstTime).total_seconds() / 3600
    if hours < 10 / 60:
        return None
    return int(round(max(-999, min(999, (lastMoisture - firstMoisture) / hours))))


# Function reads the data from all the senors and returns it
def get_data():
    timenow = (datetime.now().strftime('%d/%m %H:%M'))
    temp = temperature_status()
    humid = humidity_status()
    pump = pump_status()
    moisture = moisture_status()
    waterlevel = water_status()
    lastwater = lastWatered.strftime('%d/%m %H:%M') if lastWatered else '--/-- --:--'
    valve = valve_status()
    state = watering_state(moisture)
    trend = soil_trend(moisture)
    return timenow, temp, humid, pump, moisture, trend, waterlevel, lastwater, valve, state


# Function reads the sensors and saves them to the database with the times as real datetimes
def save_database():
    sensorDatabase.saveData(datetime.now(), lastWatered, soakUntil, temperature_status(),
                            humidity_status(), moisture_status(), water_status(), pump_status(), valve_status())


# Function gets data from get_data and displays it on the LCD screen
def lcd_display():
    timenow, temp, humid, pump, moisture, trend, waterlevel, lastwater, valve, state = get_data()
    sleep(5)  # Wait then clear the display
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string("Time: " + timenow)
    lcd.cursor_pos = (1, 0)
    lcd.write_string("Temp " + temp)
    lcd.cursor_pos = (1, 10)
    lcd.write_string("Humid " + humid)
    lcd.cursor_pos = (2, 0)
    lcd.write_string("Pump:" + pump)
    lcd.cursor_pos = (2, 10)
    lcd.write_string("Valve:" + valve)
    lcd.cursor_pos = (3, 0)
    lcd.write_string("LstWater:" + lastwater)
    sleep(5)  # We wait and then clear the screen to display more data
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string("Time: " + timenow)
    lcd.cursor_pos = (1, 0)
    lcd.write_string("Soil:" + ('ERR' if moisture is None else '%3d%%' % moisture))
    lcd.cursor_pos = (1, 11)
    lcd.write_string("Tank:" + ('LOW' if waterlevel < 10 else 'OK'))
    lcd.cursor_pos = (2, 0)
    lcd.write_string("Soil Trend: " + ('--' if trend is None else '%+d%%/hr' % trend))
    lcd.cursor_pos = (3, 0)
    lcd.write_string(state)


# Function gets data from get_data() and print is to the terminal window
def print_data():
    timenow, temp, humid, pump, moisture, trend, waterlevel, lastwater, valve, state = get_data()
    print("    -----START-----")
    print('Temperature    -- ' + temp + 'F')
    print('Humidity       -- ' + humid + '%')
    print('Soil Moisture  -- ' + ('ERROR' if moisture is None else str(moisture) + '%'))
    print('Soil Trend     -- ' + ('not enough readings yet' if trend is None else '%+d%% per hour' % trend))
    print('Pump Status    -- ' + pump)
    print('Water Valve    -- ' + valve)
    print('Water Level    -- ' + str(waterlevel))
    print('Time Now       -- ' + (datetime.now().strftime('%d/%m/%y %H:%M:%S')))
    print('Last Watered   -- ' + lastwater)
    print('Status         -- ' + state)
    print("    ------END------")


sd_notify('READY=1')

try:
    while True:
        sd_notify('WATCHDOG=1')  # If the loop hangs systemd restarts the program, which turns the pump off
        lcd_display()
        print_data()
        print("Main loop display")
        print("FirstLowWater " + str(firstLowWater))
        # Save the sensor readings every ten minutes
        if datetime.now() >= nextLogTime:
            nextLogTime = datetime.now() + timedelta(minutes=10)
            save_database()
            print("Saved to database - TEN MINUTE LOG")
        # Start the pump when the soil is dry, unless the last watering is still soaking in or the tank is low
        moisture = moisture_status()
        if pump_status() == 'OFF' and moisture is not None and moisture < dryLevel \
                and datetime.now() >= soakUntil and water_status() >= 10:
            print("Soil is dry (" + str(moisture) + "%) - starting pump")
            pumpStartTime = datetime.now()
            pump_control('Start')
            save_database()
            print("Saved to database - PUMP ON")
            # NOTE: valve_status() only reads back the relay pin we just set, so it can't tell
            # if the valve really opened. A valve failure check needs a feedback sensor on the valve.
        # Keep watering until the soil reaches the wet level, then give the water time to soak in
        moisture = moisture_status()
        if pump_status() == 'ON' and moisture is not None and moisture >= wetLevel:
            lastWatered = datetime.now()
            soakUntil = datetime.now() + timedelta(minutes=soakTime)
            print("Soil is wet (" + str(moisture) + "%) - pump turned off, soaking until " + soakUntil.strftime('%H:%M'))
            pump_control('Stop')
            save_database()
            print("Saved to database - PUMP STOP")
        # Backstop: if the soil never reaches the wet level (sensor pulled out of the soil, a broken hose)
        # stop after max_run_minutes so the garden doesn't flood and the tank isn't emptied
        if pump_status() == 'ON' and datetime.now() >= pumpStartTime + timedelta(minutes=maxRunTime):
            lastWatered = datetime.now()
            soakUntil = datetime.now() + timedelta(minutes=soakTime)
            print("Soil didn't reach " + str(wetLevel) + "% in " + str(maxRunTime) +
                  " minutes - pump turned off. Check the moisture sensor and hose")
            pump_control('Stop')
            save_database()
            print("Saved to database - MAX RUN TIME")
        # If the moisture sensor stops working while watering we can't tell when to stop, so stop now
        if pump_status() == 'ON' and moisture is None:
            print("Moisture sensor lost while watering - pump turned off")
            pump_control('Stop')
            save_database()
            print("Saved to database - SENSOR LOST")
        # If water level is to low we stop the pump. When the tank is filled the soil is checked again
        # and watering starts if it is still dry
        if firstLowWater == 1 and water_status() < 10:
            firstLowWater = firstLowWater + 1
            print("Water Level is LOW")
            if pump_status() == 'ON':
                pump_control('Stop')
            save_database()
            print("Saved to database - LOW WATER SENSOR")
        if firstLowWater > 1 and water_status() >= 10:
            firstLowWater = 1
            print("Water level is okay")

# When program ends or is interupted we turn the pump off and cleanup the GPIO pins
finally:
    print("Shutdown")
    if pump_status() == 'ON':
        pump_control('Stop')
    GPIO.output(pumpControl, 1)
    GPIO.output(waterValveControl, 1)
    lcd.clear()  # So the screen doesn't keep showing the pump as on
    lcd.write_string("Program stopped")
    sensorDatabase.close()
    print("Closed database")
    i2c.close()
    GPIO.cleanup()
    print("GPIO pins cleanup")
