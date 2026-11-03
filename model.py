"""
Database model for the garden watering system.
Rebuilt from __pycache__/model.cpython-34.pyc
"""

from peewee import *

db = SqliteDatabase('gardenDB.db')


# Readings are kept in the "readings" table. Older versions of the program used a "sensor" table
# with different columns, which is left as it is.
class Sensor(Model):
    TimeNow = DateTimeField()
    LastWatering = DateTimeField(null=True)
    SoakUntil = DateTimeField(null=True)
    PumpStop = DateTimeField(null=True)
    Temperature = CharField()
    Humidity = CharField()
    Moisture = IntegerField(null=True)
    WaterLevel = IntegerField()
    PumpStatus = CharField()
    ValveStatus = CharField()

    class Meta:
        database = db
        table_name = 'readings'


class SensorData(object):
    def __init__(self):
        db.connect()
        db.create_tables([Sensor], safe=True)

    # Save one row of sensor readings to the database
    def saveData(self, timenow, lastwater, soakuntil, pumpstop, temp, humid, moisture, waterlevel, pump, valve):
        Sensor.create(TimeNow=timenow, LastWatering=lastwater, SoakUntil=soakuntil, PumpStop=pumpstop,
                      Temperature=temp, Humidity=humid, Moisture=moisture, WaterLevel=waterlevel,
                      PumpStatus=pump, ValveStatus=valve)

    # Returns the newest row, or None if the database is empty
    def getLast(self):
        return Sensor.select().order_by(Sensor.id.desc()).first()

    def close(self):
        db.close()
