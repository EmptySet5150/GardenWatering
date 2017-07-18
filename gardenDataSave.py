#!/usr/bin/python3

"""
This file  is to be used  as a cron
job ran  every ten  minutes to save
the sensor readings to the database
"""

# Lets import a few things needed for the system
from time import sleep
from datetime import datetime
from model import SensorData
import RPi.GPIO as GPIO
import DHT22
import pigpio

pi = pigpio.pi()

# Setup pin names
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
dht22 = DHT22.sensor(pi, 22)
rainSensor = 40
waterValveControl = 22
pumpControl = 32
waterLevel = 16
sensorDatabase = SensorData()

# Setup GPIO pin modes
GPIO.setup(waterValveControl, GPIO.OUT)
GPIO.setup(pumpControl, GPIO.OUT)
GPIO.setup(rainSensor, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(waterLevel, GPIO.IN, pull_up_down=GPIO.PUD_UP)


# Functions to return temperature and humidity readings from the DHT22 sensor
def temperature_status():
    dht22.trigger()
    sleep(.2)
    temp = '%.1f' % (dht22.temperature() * 1.8 + 32)
    return temp


def humidity_status():
    dht22.trigger()
    sleep(.2)
    humid = '%.1f' % (dht22.humidity())
    return humid


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


# def get_last():


# Function gets data from sensors and get_last() and saves it to the database
def save_database():
    #    lastwater, nextwater, pumpstop = get_last()
    lastwater = 'N/A'
    nextwater = 'N/A'
    pumpstop = 'N/A'
    timenow = (datetime.now().strftime('%d/%m/%y %H:%M'))
    temp = temperature_status()
    humid = humidity_status()
    pump = pump_status()
    rain = rain_status()
    waterlevel = water_status()
    valve = valve_status()
    sensorDatabase.saveData(timenow, lastwater, nextwater, pumpstop, temp, humid, waterlevel, rain, pump, valve)

save_database()
sensorDatabase.close()
