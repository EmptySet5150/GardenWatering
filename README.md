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
sudo apt install pigpio python3-pigpio python3-rpi.gpio python3-venv
python3 -m venv --system-site-packages venv
venv/bin/pip install -r requirements.txt
```

Raspberry Pi OS Bookworm and newer block `pip install` outside a virtual environment, so the
packages go in `venv`. `--system-site-packages` lets it use the `pigpio` and `RPi.GPIO` installed by
apt, so pip only adds `peewee` and `RPLCD`.

Settings (days between waterings, pump run time, low water wait, watering hour) are in `config.ini`.

## Run it as a service

```
sudo cp garden-watering.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pigpiod garden-watering
```

The service starts at boot and restarts the program if it crashes or the main loop stops responding
for 2 minutes. The last and next watering times are saved in the database, so a restart picks up the
schedule where it left off. Watch the output with `journalctl -u garden-watering -f`.

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

The window shows the LCD and the pump and valve, and lets you make it rain, empty the tank, change the
temperature and humidity, speed up time (up to an hour per second) or jump to the next watering. The
Stop button stops the program the same way systemd does, so you can watch it turn the pump off.

The simulator keeps its own `config.ini` and database in `sim_data/`, so it never touches the real ones.
