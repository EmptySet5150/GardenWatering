This is a outdoor garden watering
system. Using Raspberry PI, SQLite
and a lot of sensors. Please
send me a message about this project
if you have intrest in helping
with it

## Setup on the Raspberry Pi

Works on a Pi 4 or older with Python 3. `RPi.GPIO` and `pigpio` do not support the Pi 5.

Put the code in `/home/garden/wateringSystem`, then from that folder:

```
sudo apt update
sudo apt install pigpio python3-pigpio python3-rpi.gpio python3-venv i2c-tools
sudo raspi-config nonint do_i2c 0
sudo usermod -aG gpio,i2c garden
python3 -m venv --system-site-packages venv
venv/bin/pip install -r requirements.txt
```

Reboot after that. `raspi-config` turns on I2C, which the moisture sensor's ADS1115 board uses, and
`usermod` lets the `garden` user reach the GPIO pins and I2C. `i2cdetect -y 1` should show the
ADS1115 as `48`.

Raspberry Pi OS Bookworm and newer block `pip install` outside a virtual environment, so the
packages go in `venv`. `--system-site-packages` lets it use the `pigpio` and `RPi.GPIO` installed by
apt, so pip only adds `peewee`, `RPLCD` and `smbus2`.

## How it decides when to water

A capacitive soil moisture sensor, read through an ADS1115, gives the soil moisture from 0% (dry air)
to 100% (in water). When it drops below `dry_level`, the pump runs for `pump_run_minutes`. Then it
waits `soak_minutes` for the water to soak in before checking the soil again, and waters again if the
soil is still dry. These settings are in `config.ini`.

It never waters while the water tank is low. As soon as the tank is filled again, it waters if the soil
is still dry. If the moisture sensor can't be read, it doesn't water and the LCD shows
`Soil sensor ERROR`.

## Calibrate the moisture sensor

Every sensor reads a little differently, so set its dry and wet readings once. Stop the service, then:

```
venv/bin/python calibrate_moisture.py
```

Write down the reading with the sensor held in dry air, then with it in a glass of water up to the line
on the sensor. Put the two numbers in the `[moisture_sensor]` section of `config.ini` as `dry_reading`
and `wet_reading`.

## Run it as a service

```
sudo cp garden-watering.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pigpiod garden-watering
```

The service starts at boot and restarts the program if it crashes or the main loop stops responding
for 2 minutes. The last watering and soak times are saved in the database, so a restart doesn't water
again while the last watering is still soaking in. Watch the output with `journalctl -u garden-watering -f`.

## Pump failsafe

The program turns the pump and valve off when it stops. These keep them off if the Pi itself fails:

1. **Pull-up resistors on the relay inputs.** Put a 10k resistor from each relay IN pin to the relay
   module's VCC. The relays are active low, so if the Pi freezes, reboots or loses power and lets go of
   the pins, the resistors hold the inputs HIGH and the relays stay off.
2. **The Pi's hardware watchdog.** This reboots the Pi if it freezes. Add this line to
   `/etc/systemd/system.conf` and reboot:
   ```
   RuntimeWatchdogSec=15
   ```
3. **Optional: a timer relay on the pump power.** A hardware off-delay timer relay set a little longer
   than the pump run time cuts the pump even if everything else fails.

## Try it without a Raspberry Pi

`simulator.py` runs the real `autoWatering.py` on a PC with pretend hardware, in a small window:

```
pip install peewee
python simulator.py
```

The window shows the LCD and the pump and valve. The pretend soil dries out over time (faster when it's
hot) and gets wetter while the pump runs, or you can set the moisture yourself with the slider. You can
also unplug the moisture sensor, empty the tank, change the temperature and humidity, speed up time (up
to an hour per second) or skip the soak wait. The Stop button stops the program the same way systemd
does, so you can watch it turn the pump off.

The simulator uses the settings in `config.ini`. After changing them, press Stop and then Start
program in the window. It keeps its own database in `sim_data/`, so it never touches the real one.
