"""
Runs autoWatering.py on a PC with pretend hardware so you can try it without a Raspberry Pi.

The real autoWatering.py runs unchanged. This file swaps in fake versions of RPi.GPIO, pigpio,
DHT22, RPLCD and smbus2 (for the ADS1115 and moisture sensor), pretend soil that dries out and
soaks up water, and a simulated clock you can speed up. Each time the program starts it uses a
fresh copy of the project's config.ini, and it keeps its own gardenDB.db in the sim_data folder so
the real database is never touched.

Run it with:  python simulator.py   (needs peewee:  pip install peewee)
"""

import builtins
import configparser
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time as real_time
import traceback
import types
from datetime import datetime as real_datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(HERE, 'sim_data')
SCRIPT = os.path.join(HERE, 'autoWatering.py')

# Pin numbers from autoWatering.py (BOARD numbering)
WATER_PIN, VALVE_PIN, PUMP_PIN = 16, 22, 32

# How fast the pretend soil changes: watering adds this many percent per minute, and it dries
# out by a few percent per hour, faster when it's hot
WET_PER_MINUTE = 3.0


class SimClock:
    """A clock that runs `speed` times faster than real time and can jump forward."""

    def __init__(self):
        self.lock = threading.Lock()
        self.speed = 1.0
        self.base_real = real_time.monotonic()
        self.base_sim = real_time.time()

    def now(self):
        with self.lock:
            return self.base_sim + (real_time.monotonic() - self.base_real) * self.speed

    def set_speed(self, speed):
        with self.lock:
            self.base_sim += (real_time.monotonic() - self.base_real) * self.speed
            self.base_real = real_time.monotonic()
            self.speed = float(speed)

    def jump(self, seconds):
        with self.lock:
            self.base_sim += seconds


class Hardware:
    """Shared state between the pretend hardware and the window."""

    def __init__(self):
        self.pins = {VALVE_PIN: 1, PUMP_PIN: 1}  # relays are active low, 1 = off
        self.tank_low = False
        self.moisture = 45.0  # percent
        self.soil_auto = True  # soil dries out and soaks up water by itself
        self.soil_time = None  # simulated time the soil was last updated
        self.sensor_unplugged = False
        self.dry_reading = 20000  # calibration, read from config.ini when the program starts
        self.wet_reading = 8800
        self.temp_f = 72.0
        self.humidity = 55.0
        self.lcd = [[' '] * 20 for _ in range(4)]
        self.cursor = (0, 0)
        self.dht_triggers = 0


clock = SimClock()
hw = Hardware()
log_queue = queue.Queue()


class StopProgram:
    """The Stop button sets this; the next sleep() in the program calls its SIGTERM handler."""
    requested = False
    handler = None


def update_soil():
    """Moves the pretend soil moisture on by however much simulated time has passed."""
    now = clock.now()
    elapsed = 0 if hw.soil_time is None else now - hw.soil_time
    hw.soil_time = now
    if not hw.soil_auto or elapsed <= 0:
        return
    if hw.pins.get(PUMP_PIN) == 0:
        hw.moisture += elapsed / 60 * WET_PER_MINUTE
    else:
        dry_per_hour = 0.6 + max(0.0, hw.temp_f - 50) * 0.04
        hw.moisture -= elapsed / 3600 * dry_per_hour
    hw.moisture = max(0.0, min(100.0, hw.moisture))


# ---------- Fake hardware modules ----------
def make_fake_modules():
    gpio = types.ModuleType('RPi.GPIO')
    gpio.BOARD, gpio.BCM, gpio.OUT, gpio.IN = 'BOARD', 'BCM', 'OUT', 'IN'
    gpio.PUD_UP, gpio.PUD_DOWN, gpio.HIGH, gpio.LOW = 'PUD_UP', 'PUD_DOWN', 1, 0
    gpio.setmode = lambda mode: None
    gpio.setwarnings = lambda flag: None

    def setup(pin, mode, initial=None, pull_up_down=None):
        if mode == 'OUT':
            hw.pins[pin] = 0 if initial is None else initial  # a real output starts LOW without initial

    def output(pin, value):
        hw.pins[pin] = 1 if value else 0

    def gpio_input(pin):
        if pin == WATER_PIN:
            return 0 if hw.tank_low else 1  # switch closed (LOW) = tank low
        return hw.pins.get(pin, 1)

    def cleanup():
        # Pins go back to inputs; the pull-up resistors hold the relays off
        hw.pins[VALVE_PIN] = 1
        hw.pins[PUMP_PIN] = 1

    gpio.setup, gpio.output, gpio.input, gpio.cleanup = setup, output, gpio_input, cleanup
    rpi = types.ModuleType('RPi')
    rpi.GPIO = gpio

    pigpio = types.ModuleType('pigpio')
    pigpio.pi = lambda: object()

    dht = types.ModuleType('DHT22')

    class Sensor:
        def __init__(self, pi, gpio_pin, LED=None, power=None):
            pass

        def trigger(self):
            hw.dht_triggers += 1

        def temperature(self):
            return (hw.temp_f - 32) / 1.8  # the sensor reports Celsius

        def humidity(self):
            return hw.humidity

        def cancel(self):
            pass

    dht.sensor = Sensor

    rplcd = types.ModuleType('RPLCD')

    class CharLCD:
        def __init__(self, **kwargs):
            pass

        def clear(self):
            hw.lcd = [[' '] * 20 for _ in range(4)]
            hw.cursor = (0, 0)

        @property
        def cursor_pos(self):
            return hw.cursor

        @cursor_pos.setter
        def cursor_pos(self, pos):
            hw.cursor = pos

        def write_string(self, text):
            row, col = hw.cursor
            for ch in text:
                if col < 20:
                    hw.lcd[row][col] = ch
                col += 1
            hw.cursor = (row, col)

    rplcd.CharLCD = CharLCD

    smbus2 = types.ModuleType('smbus2')

    class SMBus:
        """The ADS1115 on the I2C bus, reading the pretend moisture sensor on A0."""

        def __init__(self, bus=None):
            pass

        def _check(self):
            if hw.sensor_unplugged:
                raise OSError(121, 'Remote I/O error')

        def write_i2c_block_data(self, address, register, data):
            self._check()

        def read_i2c_block_data(self, address, register, length):
            self._check()
            update_soil()
            # The sensor's voltage drops as the soil gets wetter
            raw = int(hw.dry_reading - hw.moisture / 100 * (hw.dry_reading - hw.wet_reading))
            return [(raw >> 8) & 0xFF, raw & 0xFF]

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    smbus2.SMBus = SMBus
    return {'RPi': rpi, 'RPi.GPIO': gpio, 'pigpio': pigpio, 'DHT22': dht, 'RPLCD': rplcd, 'smbus2': smbus2}


# ---------- Fake time, datetime and signal, seen only by autoWatering.py ----------
class _DatetimeMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, real_datetime)


class SimDatetime(real_datetime, metaclass=_DatetimeMeta):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.fromtimestamp(clock.now(), tz)


def check_stop():
    if StopProgram.requested and StopProgram.handler:
        StopProgram.requested = False
        StopProgram.handler(15, None)  # behaves like systemd stopping the service


def sim_sleep(seconds):
    check_stop()
    end = clock.now() + seconds
    while True:
        left = end - clock.now()
        if left <= 0:
            return
        real_time.sleep(min(left / clock.speed, 0.05))
        check_stop()


fake_time = types.ModuleType('time')
fake_time.sleep = sim_sleep
fake_time.time = clock.now

fake_datetime = types.ModuleType('datetime')
fake_datetime.datetime = SimDatetime
fake_datetime.timedelta = timedelta

fake_signal = types.ModuleType('signal')
fake_signal.SIGTERM = 15


def _signal(signum, handler):
    StopProgram.handler = handler


fake_signal.signal = _signal
SCRIPT_ONLY = {'time': fake_time, 'datetime': fake_datetime, 'signal': fake_signal}


def sim_print(*args, sep=' ', end='\n', **kwargs):
    log_queue.put(sep.join(str(a) for a in args))


def make_builtins():
    b = dict(builtins.__dict__)
    real_import = builtins.__import__

    def sim_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in SCRIPT_ONLY and level == 0:
            return SCRIPT_ONLY[name]
        return real_import(name, globals, locals, fromlist, level)

    b['__import__'] = sim_import
    b['print'] = sim_print
    return b


class Program:
    """Runs autoWatering.py in a background thread."""

    def __init__(self):
        self.thread = None
        self.globals = {}

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        os.makedirs(SIM_DIR, exist_ok=True)
        shutil.copy(os.path.join(HERE, 'config.ini'), SIM_DIR)  # always use the current settings
        config = configparser.ConfigParser()
        config.read(os.path.join(SIM_DIR, 'config.ini'))
        hw.dry_reading = config.getint('moisture_sensor', 'dry_reading', fallback=20000)
        hw.wet_reading = config.getint('moisture_sensor', 'wet_reading', fallback=8800)
        StopProgram.requested = False
        self.globals = {'__name__': '__main__', '__file__': os.path.join(SIM_DIR, 'autoWatering.py'),
                        '__builtins__': make_builtins()}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        log_queue.put('=== Program started ===')
        try:
            os.chdir(SIM_DIR)  # model.py opens gardenDB.db in the working folder
            with open(SCRIPT, encoding='utf-8') as f:
                code = compile(f.read(), SCRIPT, 'exec')
            exec(code, self.globals)
        except SystemExit:
            log_queue.put('=== Program stopped ===')
        except Exception:
            log_queue.put('=== Program crashed ===\n' + traceback.format_exc())

    def stop(self):
        if self.running:
            StopProgram.requested = True

    def value(self, name):
        return self.globals.get(name)


def setup_imports():
    sys.path.insert(0, HERE)
    sys.modules.update(make_fake_modules())


# ---------- Window ----------
def run_window():
    import tkinter as tk
    from tkinter import ttk

    program = Program()
    root = tk.Tk()
    root.title('Garden Watering Simulator')
    root.configure(bg='#eef1ea')
    root.minsize(900, 640)

    style = ttk.Style()
    style.theme_use('clam')
    for name in ('TFrame', 'TLabelframe', 'TLabelframe.Label', 'TLabel', 'TRadiobutton', 'TCheckbutton'):
        style.configure(name, background='#eef1ea')
    style.configure('TLabelframe.Label', font=('Segoe UI', 10, 'bold'))
    style.configure('Big.TLabel', font=('Segoe UI', 11))
    style.configure('Clock.TLabel', font=('Consolas', 15, 'bold'))

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill='both', expand=True)
    outer.columnconfigure(0, weight=1)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(2, weight=1)

    # LCD
    lcd_frame = ttk.LabelFrame(outer, text='LCD 20x4', padding=8)
    lcd_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
    lcd = tk.Canvas(lcd_frame, width=20 * 14 + 24, height=4 * 26 + 20, bg='#2f5d1e', highlightthickness=2,
                    highlightbackground='#1d3a12')
    lcd.pack()
    lcd_lines = [lcd.create_text(12, 14 + r * 26, anchor='nw', font=('Consolas', 14, 'bold'), fill='#d8f5b4', text='')
                 for r in range(4)]

    # Schedule
    sched = ttk.LabelFrame(outer, text='Program state', padding=8)
    sched.grid(row=1, column=0, sticky='nsew', padx=(0, 8), pady=(8, 0))
    clock_lbl = ttk.Label(sched, style='Clock.TLabel')
    clock_lbl.grid(row=0, column=0, columnspan=2, sticky='w')
    rows = {}
    for i, key in enumerate(('Running', 'Last watered', 'Soaking until', 'Pump stops at', 'Settings')):
        ttk.Label(sched, text=key + ':', style='Big.TLabel').grid(row=i + 1, column=0, sticky='w', padx=(0, 10))
        rows[key] = ttk.Label(sched, style='Big.TLabel')
        rows[key].grid(row=i + 1, column=1, sticky='w')

    # Hardware
    hwf = ttk.LabelFrame(outer, text='Hardware', padding=8)
    hwf.grid(row=0, column=1, rowspan=2, sticky='nsew')
    lights = tk.Canvas(hwf, width=320, height=70, bg='#eef1ea', highlightthickness=0)
    lights.grid(row=0, column=0, columnspan=2, sticky='w')
    pump_light = lights.create_oval(10, 10, 50, 50, outline='#333', width=2)
    lights.create_text(60, 30, anchor='w', text='Pump', font=('Segoe UI', 12, 'bold'))
    valve_light = lights.create_oval(170, 10, 210, 50, outline='#333', width=2)
    lights.create_text(220, 30, anchor='w', text='Valve', font=('Segoe UI', 12, 'bold'))

    # Soil moisture
    moist_lbl = ttk.Label(hwf, style='Big.TLabel')
    moist_lbl.grid(row=1, column=0, columnspan=2, sticky='w')
    moist = ttk.Scale(hwf, from_=0, to=100, length=260, command=lambda v: setattr(hw, 'moisture', float(v)))
    moist.set(hw.moisture)
    moist.grid(row=2, column=0, columnspan=2, sticky='w')
    auto_var = tk.BooleanVar(value=True)
    unplug_var = tk.BooleanVar(value=False)
    tank_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(hwf, text='Soil dries out and soaks up water by itself', variable=auto_var,
                    command=lambda: setattr(hw, 'soil_auto', auto_var.get())).grid(row=3, column=0, columnspan=2,
                                                                                  sticky='w', pady=(4, 0))
    ttk.Checkbutton(hwf, text='Moisture sensor unplugged', variable=unplug_var,
                    command=lambda: setattr(hw, 'sensor_unplugged', unplug_var.get())).grid(row=4, column=0,
                                                                                           columnspan=2, sticky='w')
    ttk.Checkbutton(hwf, text='Water tank low (float switch closed)', variable=tank_var,
                    command=lambda: setattr(hw, 'tank_low', tank_var.get())).grid(row=5, column=0, columnspan=2,
                                                                                 sticky='w')
    temp_lbl = ttk.Label(hwf, style='Big.TLabel')
    temp_lbl.grid(row=6, column=0, sticky='w', pady=(8, 0))
    temp = ttk.Scale(hwf, from_=20, to=110, length=260, command=lambda v: setattr(hw, 'temp_f', float(v)))
    temp.set(hw.temp_f)
    temp.grid(row=7, column=0, columnspan=2, sticky='w')
    hum_lbl = ttk.Label(hwf, style='Big.TLabel')
    hum_lbl.grid(row=8, column=0, sticky='w', pady=(8, 0))
    hum = ttk.Scale(hwf, from_=0, to=100, length=260, command=lambda v: setattr(hw, 'humidity', float(v)))
    hum.set(hw.humidity)
    hum.grid(row=9, column=0, columnspan=2, sticky='w')

    # Time and program controls
    ctl = ttk.LabelFrame(hwf, text='Time', padding=8)
    ctl.grid(row=10, column=0, columnspan=2, sticky='ew', pady=(10, 0))
    speed_var = tk.StringVar(value='1')
    for i, (label, value) in enumerate((('1x', '1'), ('60x (1 min/s)', '60'), ('600x', '600'),
                                        ('3600x (1 hr/s)', '3600'))):
        ttk.Radiobutton(ctl, text=label, value=value, variable=speed_var,
                        command=lambda: clock.set_speed(float(speed_var.get()))).grid(row=i // 2, column=i % 2,
                                                                                     sticky='w', padx=(0, 12))

    def skip_soak():
        soak = program.value('soakUntil')
        if soak is None:
            return
        gap = soak.timestamp() - clock.now() + 1
        if gap > 0:
            clock.jump(gap)
            log_queue.put(f'--- Skipped the soak wait ({timedelta(seconds=int(gap))}) ---')

    btns = ttk.Frame(ctl)
    btns.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
    ttk.Button(btns, text='Skip soak wait', command=skip_soak).pack(side='left')
    ttk.Button(btns, text='+1 hour', command=lambda: clock.jump(3600)).pack(side='left', padx=6)

    prog = ttk.Frame(hwf)
    prog.grid(row=11, column=0, columnspan=2, sticky='w', pady=(10, 0))
    start_btn = ttk.Button(prog, text='Start program', command=program.start)
    start_btn.pack(side='left')
    stop_btn = ttk.Button(prog, text='Stop (like systemd)', command=program.stop)
    stop_btn.pack(side='left', padx=6)

    def reset_db():
        if program.running:
            log_queue.put('--- Stop the program before resetting the database ---')
            return
        path = os.path.join(SIM_DIR, 'gardenDB.db')
        if os.path.exists(path):
            os.remove(path)
        log_queue.put('--- Simulator database cleared ---')

    ttk.Button(prog, text='Clear sim database', command=reset_db).pack(side='left')

    # Log and database
    bottom = ttk.Frame(outer)
    bottom.grid(row=2, column=0, columnspan=2, sticky='nsew', pady=(10, 0))
    bottom.columnconfigure(0, weight=1)
    bottom.columnconfigure(1, weight=1)
    bottom.rowconfigure(0, weight=1)
    logf = ttk.LabelFrame(bottom, text='Program output', padding=4)
    logf.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
    log = tk.Text(logf, height=14, font=('Consolas', 9), bg='#fbfcf8', wrap='none')
    log.pack(fill='both', expand=True)
    dbf = ttk.LabelFrame(bottom, text='Database (newest rows)', padding=4)
    dbf.grid(row=0, column=1, sticky='nsew')
    cols = ('Time', 'Pump', 'Valve', 'Soil', 'Tank', 'Temp', 'Soak until')
    table = ttk.Treeview(dbf, columns=cols, show='headings', height=12)
    for c, w in zip(cols, (120, 45, 50, 45, 40, 50, 120)):
        table.heading(c, text=c)
        table.column(c, width=w, anchor='w')
    table.pack(fill='both', expand=True)

    def short(ts):
        return str(ts)[5:16] if ts else ''

    last_db = {'mtime': None}

    def refresh_db():
        path = os.path.join(SIM_DIR, 'gardenDB.db')
        if not os.path.exists(path):
            table.delete(*table.get_children())
            return
        mtime = os.path.getmtime(path)
        if mtime == last_db['mtime']:
            return
        last_db['mtime'] = mtime
        try:
            con = sqlite3.connect(path)
            data = con.execute('select TimeNow, PumpStatus, ValveStatus, Moisture, WaterLevel, Temperature, SoakUntil '
                               'from readings order by id desc limit 40').fetchall()
            con.close()
        except sqlite3.Error:
            return
        table.delete(*table.get_children())
        for r in data:
            table.insert('', 'end', values=(short(r[0]), r[1], r[2], 'ERR' if r[3] is None else f'{r[3]}%',
                                            'LOW' if r[4] < 10 else 'OK', r[5], short(r[6])))

    def fmt(v):
        return v.strftime('%a %d %b %H:%M') if isinstance(v, real_datetime) else '-'

    def tick():
        update_soil()
        if hw.soil_auto:
            moist.set(hw.moisture)
        moist_lbl.configure(text=f'Soil moisture: {hw.moisture:.0f} %')
        for r in range(4):
            lcd.itemconfigure(lcd_lines[r], text=''.join(hw.lcd[r]))
        pump_on = hw.pins.get(PUMP_PIN) == 0
        valve_open = hw.pins.get(VALVE_PIN) == 0
        lights.itemconfigure(pump_light, fill='#2fb344' if pump_on else '#c9ccc4')
        lights.itemconfigure(valve_light, fill='#1c7ed6' if valve_open else '#c9ccc4')
        clock_lbl.configure(text='Simulated time:  ' + real_datetime.fromtimestamp(clock.now()).strftime(
            '%a %d %b %Y  %H:%M:%S'))
        rows['Running'].configure(text='yes' if program.running else 'no')
        rows['Last watered'].configure(text=fmt(program.value('lastWatered')))
        soak = program.value('soakUntil')
        soaking = isinstance(soak, real_datetime) and soak.timestamp() > clock.now()
        rows['Soaking until'].configure(text=fmt(soak) if soaking else '-')
        pump_stop = program.value('pumpStopTime') if pump_on else None
        rows['Pump stops at'].configure(text=fmt(pump_stop))
        if program.value('dryLevel') is not None:
            rows['Settings'].configure(text=f"water below {program.value('dryLevel')}%, pump "
                                            f"{program.value('pumpRunTime')} min, soak "
                                            f"{program.value('soakTime')} min")
        temp_lbl.configure(text=f'Temperature: {hw.temp_f:.0f} F')
        hum_lbl.configure(text=f'Humidity: {hw.humidity:.0f} %')
        start_btn.state(['disabled'] if program.running else ['!disabled'])
        stop_btn.state(['!disabled'] if program.running else ['disabled'])
        lines = []
        while not log_queue.empty():
            lines.append(log_queue.get_nowait())
        if lines:
            stamp = real_datetime.fromtimestamp(clock.now()).strftime('%d/%m %H:%M:%S')
            log.insert('end', ''.join(f'{stamp}  {line}\n' for line in lines))
            if int(log.index('end-1c').split('.')[0]) > 2000:
                log.delete('1.0', '500.0')
            log.see('end')
        refresh_db()
        root.after(100, tick)

    def on_close():
        program.stop()
        root.after(300, root.destroy)

    root.protocol('WM_DELETE_WINDOW', on_close)
    log_queue.put(f'Simulator files are in {SIM_DIR}')
    log_queue.put('Settings come from config.ini. After editing it, press Stop, then Start program.')
    program.start()
    tick()
    root.mainloop()


if __name__ == '__main__':
    try:
        import peewee  # noqa: F401  (autoWatering.py needs it through model.py)
    except ImportError:
        sys.exit('The simulator needs peewee. Install it with:  pip install peewee')
    setup_imports()
    run_window()
