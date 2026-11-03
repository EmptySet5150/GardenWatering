"""
Database model for the garden watering system.
Rebuilt from __pycache__/model.cpython-34.pyc
"""

from peewee import *

db = SqliteDatabase('gardenDB.db')


class Sensor(Model):
    TimeNow = DateTimeField()
    LastWatering = DateTimeField()
    NextWatering = DateTimeField()
    PumpStop = DateTimeField()
    Temperature = CharField()
    Humidity = CharField()
    WaterLevel = IntegerField()
    Rain = CharField()
    PumpStatus = CharField()
    ValveStatus = CharField()

    class Meta:
        database = db


class SensorData(object):
    def __init__(self):
        db.connect()
        db.create_tables([Sensor], safe=True)

    # Save one row of sensor readings to the database
    def saveData(self, timenow, lastwater, nextwater, pumpstop, temp, humid, waterlevel, rain, pump, valve):
        Sensor.create(TimeNow=timenow, LastWatering=lastwater, NextWatering=nextwater, PumpStop=pumpstop,
                      Temperature=temp, Humidity=humid, WaterLevel=waterlevel, Rain=rain,
                      PumpStatus=pump, ValveStatus=valve)

    # Returns the newest row, or None if the database is empty
    def getLast(self):
        return Sensor.select().order_by(Sensor.id.desc()).first()

    def close(self):
        db.close()
