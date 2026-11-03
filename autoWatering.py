"""
This program  gets data from several sensors
to  determine  when the  garden needs  to be 
watered. When it needs watering it will turn
on a pump and  open a valve for a set amount
of time to water the garden. If it starts to
rain  or the  water level  gets low the pump
will shut off and rest to  the next watering
date.

Settings are read from config.ini next to this file.
It is meant to run as a systemd service (see garden-watering.service).
"""

# Lets import a few things needed for the system
from RPLCD import CharLCD  # Libaray for the LCD screen
from time import sleep, time
from datetime import datetime, timedelta
import configparser
import os
import signal
import socket
import sys
from model import SensorData  # Using peewee to interface with a SQLite database
import RPi.GPIO as GPIO
import DHT22  # Used for Temperature and Humidity sensor
import pigpio  # Only used for DHT22 sensor - Looking into another library

pi = pigpio.pi()  # Only used for DHT22 sensor - Looking into another library

# Setup pin names
GPIO.setmode(GPIO.BOARD)  # Setting the pin number
lcd = CharLCD(cols=20, rows=4, pin_rs=11, pin_e=12, pins_data=[31, 33, 35, 37])
dht22 = DHT22.sensor(pi, 22)  # This library is setup for GPIO numbering
rainSensor = 40
waterValveControl = 22
pumpControl = 32
waterLevel = 16
sensorDatabase = SensorData()

# Setup GPIO pin modes
GPIO.setup(waterValveControl, GPIO.OUT, initial=GPIO.HIGH)  # Set pin as output - relays are active low so start HIGH (off)
GPIO.setup(pumpControl, GPIO.OUT, initial=GPIO.HIGH)  # Set pin as output - relays are active low so start HIGH (off)
GPIO.setup(rainSensor, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Set pin as input and use internal pull up resistor
GPIO.setup(waterLevel, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Set pin as imput and use internal pull up resistor

# Read the settings from config.ini
config = configparser.ConfigParser()
if not config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')):
    sys.exit("config.ini is missing")
waitDays = config.getint('watering', 'wait_days')
pumpRunTime = config.getint('watering', 'pump_run_minutes')
lowWaterReset = config.getint('watering', 'low_water_reset_days')
wateringHour = config.getint('watering', 'watering_hour')

# Setup some global variables
firstRain = 1  # Setting this so we only get one reading while its raining
firstLowWater = 1  # Setting this so we only get one reading while water level is low
lastDhtRead = 0  # Time of the last DHT22 reading
nextLogTime = datetime.now()  # Sensor readings are saved to the database every ten minutes

# Get the watering schedule from the last database row so a restart doesn't water right away
lastWatered = datetime.now()
nextWatering = datetime.now()
pumpStopTime = datetime.now()
lastRow = sensorDatabase.getLast()
if lastRow is not None and isinstance(lastRow.LastWatering, datetime) and isinstance(lastRow.NextWatering, datetime):
    lastWatered = lastRow.LastWatering
    nextWatering = lastRow.NextWatering


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


# Returns the next watering time, some days from now at the watering hour
def watering_time(days):
    return (datetime.now() + timedelta(days=days)).replace(hour=wateringHour, minute=0, second=0, microsecond=0)


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


def rain_status():
    if GPIO.input(rainSensor) == False:
        return 'WET'
    elif GPIO.input(rainSensor) == True:
        return 'DRY'


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


# Function reads the data from all the senors and returns it
def get_data():
    timenow = (datetime.now().strftime('%d/%m %H:%M'))
    temp = temperature_status()
    humid = humidity_status()
    pump = pump_status()
    rain = rain_status()
    waterlevel = water_status()
    lastwater = lastWatered.strftime('%d/%m %H:%M')
    nextwater = nextWatering.strftime('%d/%m %H:%M')
    valve = valve_status()
    pumpstop = pumpStopTime.strftime('%d/%m %H:%M')
    return timenow, temp, humid, pump, rain, waterlevel, lastwater, nextwater, valve, pumpstop


# Function reads the sensors and saves them to the database with the times as real datetimes
def save_database():
    sensorDatabase.saveData(datetime.now(), lastWatered, nextWatering, pumpStopTime, temperature_status(),
                            humidity_status(), water_status(), rain_status(), pump_status(), valve_status())


# Function gets data from get_data and displays it on the LCD screen
def lcd_display():
    timenow, temp, humid, pump, rain, waterlevel, lastwater, nextwater, valve, pumpstop = get_data()
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
    waterlevelstr = str(waterlevel)
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string("Time: " + timenow)
    lcd.cursor_pos = (1, 0)
    lcd.write_string("Temp " + temp)
    lcd.cursor_pos = (1, 10)
    lcd.write_string("Humid " + humid)
    lcd.cursor_pos = (2, 0)
    lcd.write_string("Rain:" + rain)
    lcd.cursor_pos = (2, 12)
    lcd.write_string("Tank:" + waterlevelstr)
    lcd.cursor_pos = (3, 0)
    lcd.write_string("NxtWater:" + nextwater)


# Function gets data from get_data() and print is to the terminal window
def print_data():
    timenow, temp, humid, pump, rain, waterlevel, lastwater, nextwater, valve, pumpstop = get_data()
    waterlevelstr = str(waterlevel)
    print("    -----START-----")
    print('Temperature    -- ' + temp + 'F')
    print('Humidity       -- ' + humid + '%')
    print('Pump Status    -- ' + pump)
    print('Water Valve    -- ' + valve)
    print('Rain Sensor    -- ' + rain)
    print('Water Level    -- ' + waterlevelstr)
    print('Time Now       -- ' + (datetime.now().strftime('%d/%m/%y %H:%M:%S')))
    print('Last Watered   -- ' + lastwater)
    print('Pump Stop Time -- ' + pumpstop)
    print('Next Watering  -- ' + nextwater)
    print("    ------END------")


sd_notify('READY=1')

try:
    while True:
        sd_notify('WATCHDOG=1')  # If the loop hangs systemd restarts the program, which turns the pump off
        lcd_display()
        print_data()
        print("Main loop display")
        print("FirstLowWater " + str(firstLowWater))
        print("FirstRain " + str(firstRain))
        # Save the sensor readings every ten minutes (this used to be the gardenDataSave.py cron job)
        if datetime.now() >= nextLogTime:
            nextLogTime = datetime.now() + timedelta(minutes=10)
            save_database()
            print("Saved to database - TEN MINUTE LOG")
        # If the pump is off we check to see if its time to turn it on - never start it in the rain or with a low tank
        if pump_status() == 'OFF' and datetime.now() >= nextWatering and rain_status() == 'DRY' and water_status() >= 10:
            pumpStopTime = datetime.now() + timedelta(minutes=pumpRunTime)
            print("Time to Start pump")
            pump_control('Start')
            save_database()
            print("Saved to database - PUMP ON")
            # NOTE: valve_status() only reads back the relay pin we just set, so it can't tell
            # if the valve really opened. A valve failure check needs a feedback sensor on the valve.
        # If the pump is on we check to see if it is time to turn it off and update nextWatering
        if pump_status() == 'ON' and datetime.now() >= pumpStopTime:
            lastWatered = datetime.now()
            nextWatering = watering_time(waitDays)
            print("Pump was turned off")
            pump_control('Stop')
            save_database()
            print("Saved to database - PUMP STOP TIME")
        # If the rain sensor is wet we reset the nextWatering and lastWatered time
        if firstRain == 1 and rain_status() == 'WET':
            firstRain = firstRain + 1
            print("Its Raining")
            pump_control('Stop')
            lastWatered = datetime.now()
            nextWatering = watering_time(waitDays)
            save_database()
            print("Saved data to database - RAIN SENSOR")
        if firstRain > 1 and rain_status() == 'DRY':
            firstRain = 1
            print("Its Not Raining")
        # If water level is to low we reset nextWatering to the next day
        if firstLowWater == 1 and water_status() < 10:
            firstLowWater = firstLowWater + 1
            print("Water Level is LOW")
            pump_control('Stop')
            nextWatering = watering_time(lowWaterReset)
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
    sensorDatabase.close()
    print("Closed database")
    GPIO.cleanup()
    print("GPIO pins cleanup")
