"""
This program  gets data from several sensors
to  determine  when the  garden needs  to be 
watered. When it needs watering it will turn
on a pump and  open a valve for a set amount
of time to water the garden. If it starts to
rain  or the  water level  gets low the pump
will shut off and rest to  the next watering
date.
"""

# Lets import a few things needed for the system
from RPLCD import CharLCD  # Libaray for the LCD screen
from time import sleep
from datetime import datetime
from dateutil.relativedelta import relativedelta  # Only used to add days to current date
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
GPIO.setup(waterValveControl, GPIO.OUT)  # Set pin as output
GPIO.setup(pumpControl, GPIO.OUT)  # Set pin as output
GPIO.setup(rainSensor, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Set pin as input and use internal pull up resistor
GPIO.setup(waterLevel, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Set pin as imput and use internal pull up resistor

# Setup some global variables
waitDays = int(input("How many days to wait between waterings? "))
pumpRunTime = int(input("How many minutes for the pump to run? "))
valveNotOpen = int(input("How many minutes to wait to restart if the valve failes to open? "))
lowWaterReset = int(input("How many days to wait if the water tank is low? "))
firstRain = 1  # Setting this so we only get one reading while its raining
firstLowWater = 1  # Setting this so we only get one reading while water level is low

# This data should get from the database
lastWatered = (datetime.now())
nextWatering = (datetime.now())
pumpStopTime = (datetime.now())


# Functions to return temperature and humidity readings from the DHT22 sensor
def temperature_status():
    dht22.trigger()  # Read the sensor then wait because the first reading is null
    sleep(.2)
    temp = '%.1f' % (dht22.temperature() * 1.8 + 32)  # Converting C to F and taking it out to one decimal place
    return temp


def humidity_status():
    dht22.trigger()
    sleep(.2)
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
    elif cont == 'ERROR':
        GPIO.output(pumpControl, 1)
        GPIO.output(waterValveControl, 1)
        print("Water valve failed to open")


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


# Function gets data from get_data() and saves it to the database
def save_database():
    timenow, temp, humid, pump, rain, waterlevel, lastwater, nextwater, valve, pumpstop = get_data()
    sensorDatabase.saveData(timenow, lastwater, nextwater, pumpstop, temp, humid, waterlevel, rain, pump, valve)


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


try:
    while True:
        lcd_display()
        print_data()
        print("Main loop display")
        print("FirstLowWater " + str(firstLowWater))
        print("FirstRain " + str(firstRain))
        # If the pump is off we check to see if its time to turn it on
        if pump_status() == 'OFF' and datetime.now() >= nextWatering:
            pumpStopTime = datetime.now() + relativedelta(minutes=pumpRunTime)
            print("Time to Start pump")
            pump_control('Start')
            save_database()
            print("Saved to database - PUMP ON")
            # We also check to see if the valve is closed and return a error if the valve is SHUT
            if valve_status() == 'SHUT':
                print("Valve is CLOSED - Unable to Start")
                pump_control('ERROR')
                nextWatering = lastWatered + relativedelta(minutes=valveNotOpen)
                save_database()
                print("Saved to database - VALVE ERROR")
        # If the pump is on we check to see if it is time to turn it off and update nextWatering
        if pump_status() == 'ON' and datetime.now() >= pumpStopTime:
            lastWatering = datetime.now()
            addDays = datetime.now() + relativedelta(days=waitDays)
            nextWatering = addDays.replace(hour=21, minute=0, second=0)
            print("Pump was turned off")
            pump_control('Stop')
            save_database()
            print("Saved to database - PUMP STOP TIME")
        # If the rain sensor is wet we reset the nextWatering and lastWatered time
        if firstRain == 1 and rain_status() == 'Wet':
            firstRain = firstRain + 1
            print("Its Raining")
            pump_control('Stop')
            lastWatered = datetime.now()
            addDays = datetime.now() + relativedelta(days=waitDays)
            nextWatering = addDays.replace(hour=21, minute=00, second=00)
            save_database()
            print("Saved data to database - RAIN SENSOR")
        if firstRain > 1 and rain_status() == 'Dry':
            firstRain = 1
            print("Its Not Raining")
        # If water level is to low we reset nextWatering to the next day
        if firstLowWater == 1 and water_status() < 10:
            firstLowWater = firstLowWater + 1
            print("Water Level is LOW")
            pump_control('Stop')
            lowWater = datetime.now() + relativedelta(days=lowWaterReset)
            nextWatering = lowWater.replace(hour=21, minute=00, second=00)
            save_database()
            print("Saved to database - LOW WATER SENSOR")
        if firstLowWater > 1 and water_status() >= 10:
            firstLowWater = 1
            print("Water level is okay")

# When program ends or is interupted we cleanup the GPIO pins
finally:
    print("Shutdown")
    sensorDatabase.close()
    print("Closed database")
    GPIO.cleanup()
    print("GPIO pins cleanup")
