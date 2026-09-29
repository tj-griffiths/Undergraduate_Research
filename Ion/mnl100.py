# RS-232 interface for MNL 100 nitrogen laser

"""
Transparency features:
  * verbose=True prints every command sent (TX) and every reply (RX) with a
    plain-English description.
  * laser.log keeps every exchange (time, command, raw bytes, result) - turn it
    into a table with pandas.DataFrame(laser.log).
  * explain_telegram('m0A') shows how a command is built, byte by byte.
  * print_flags(...) decodes status flag bytes bit by bit.
  * laser.raw('UT') sends any command from the manual that has no helper.
  * port='SIM' runs against a built-in simulator - practice without the laser.
 
Laser behavior to remember:
  * If the PC sends nothing for > 30 s the laser drops out of its run mode
    -> a background keep-alive polls status every few seconds (logged, not printed).
  * After LASOn (standby) it ignores commands for ~10 s (warning lamp flashes).
  * Repetition / burst / external trigger can only be started from STANDBY.
"""

import threading
import time
from datetime import datetime

CR = b"\r" # carriage return
ESC = b"\x1B" # escape


ERROR_TYPES = {
    "1": "checksum error",
    "2": "incorrect format",
    "3": "incorrect parameter",
    "4": "forbidden - laser is in the wrong state for this command",
    "5": "busy - previous command still processing (e.g. 10 s after LASOn)",
    "6": "laser's transmit queue full",
}

COMMANDS = {
    "X": ("LASER OFF (high voltage off)", None),
    "g": ("LASOn -> STANDBY (high voltage on)", None),
    "h": ("start REPETITION mode", None),
    "j": ("start BURST mode", None),
    "u": ("start EXTERNAL TRIGGER mode", None),
    "i": ("STOP -> back to STANDBY", None),
    "l": ("set QUANTITY (burst shots)", "hexword"),
    "m": ("set REPETITION RATE", "hz"),
    "n": ("set HV", "pct"),
    "o1": ("HV +1 %", None),
    "o0": ("HV -1 %", None),
    "z1": ("OPEN shutter", None),
    "z0": ("CLOSE shutter", None),
    "s": ("reset energy-monitor error", None),
    "W": ("request SHORT STATUS", None),
    "UT": ("request STATUS 7 (state + settings)", None),
    "UU": ("request STATUS 8 (live readings)", None),
    "US": ("request SERIAL NUMBERS", None),
    "UV": ("request ATTENUATOR STATUS", None),
    "V3": ("request FIRMWARE VERSION", None),
    "P": ("request ENERGY VALUES buffer", None),
    "O4": ("set ATTENUATOR transmission", "halfpct"),
    "O3": ("set ATTENUATOR stepper position", "steps"),
    "O5": ("set ATTENUATOR output energy", None),
    "O60000": ("re-initialise ATTENUATOR", None),
}

class MNL100Error(RuntimeError):
    pass

# Telegram building
def checksum(data: bytes) -> str:
    return f"{sum(data) % 256:02X}" # sum of bytes modulo 256, formatted as two hex digits

def build_telegram(cmd: str, dest: str = "!", src: str = "@") -> bytes:
    # Builds a request telegram with the given command, destination, and source.
    body = f"#{dest}{src}{cmd}".encode("ascii")
    return body + checksum(body).encode("ascii") + CR # append checksum and carriage return

def describe(cmd: str) -> str:
    # Describes a command in plain English, including its name, kind, and argument if applicable.
    for key in sorted(COMMANDS, key = len, reverse = True):
        if cmd.startswith(key):
            name, kind = COMMANDS[key]
            arg = cmd[len(key):]
            if kind and arg:
                val = int(arg, 16)
                if kind == "halfpct":                  # attenuator: value is in 0.5 % steps
                    val = val / 2
                unit = {"hz": "Hz", "pct": "%", "hexword": "shots", "halfpct": "%", "steps": "steps"}[kind]
                return f"{name} ({val} {unit}) (hex {arg})"
            return name
    return f"unknown command {cmd!r}"

def show(raw: bytes) -> str:
    # Makes control characters visible: CR -> '⏎', ESC -> '␛'
    return raw.decode("ascii", errors = "replace").replace("\r", "⏎").replace("\x1B", "␛")

def explain_telegram(cmd: str) -> None:
    # Print how a command is turned into bytes on the wire

    tel = build_telegram(cmd)
    body = tel[:-3] # everything except checksum and CR
    parts = [("#", "start of a request"), ("!", "address of the laser"), ("@", "address of the PC")]
    key = next((k for k in sorted(COMMANDS, key = len, reverse = True) if cmd.startswith(k)), cmd[:1])

    parts += [(c, "command letter") for c in key]
    parts += [(c, "parameter (hex digit)") for c in cmd[len(key):]]
    print(f"Command {cmd!r}: {describe(cmd)}\n")
    print(f"{'char':>5} {'ASCII':>6} {'hex':>5}   meaning")
    for ch, meaning in parts:
        print(f"{ch!r:>5} {ord(ch):>6} {ord(ch):>5X}   {meaning}")
    total = sum(body)
    print(f"\nsum of ASCII codes = {total} -> {total} mod 256 = {total % 256}"
          f" = 0x{total % 256:02X}  ->  checksum characters '{checksum(body)}'")
    print("end character       = CR (carriage return, 0x0D)")
    print(f"\nOn the wire: {show(tel)}  bytes: {' '.join(f'{b:02X}' for b in tel)}")

# Flag decoding (bit tables from the interface manual)

FLAG_BITS = {
    "short": ["HV enabled (STANDBY)", "laser working (HV switched on)", "-", "EEPROM error", "energy-monitor error", "temperature warning (>48 C)", "static error", "operation error - switch laser off"],
    "flag1": ["shutter open", "-", "READY (HV may be switched on)", "STANDBY (HV on)", "mode bit: repetition", "mode bit: burst", "mode bit: external trigger", "-"],
    "flag3": ["service mode", "(always 1)", "-", "-", "-", "EEPROM error", "watchdog reset occurred", "-"],
    "flag4": ["static error", "-", "enclosure open", "external interlock open", "temperature limit (>60 C)", "temp 1 warning (>48 C)", "temp 2 warning (>48 C)", "energy-monitor error"],
    "flag5": ["operation error - switch laser off", "-", "-", "HV Supply / temp error", "temp sensor 1 error", "temp sensor 2 error", "power switch damaged", "power supply too weak"]
}

def print_flags(value: int, table: str) -> None:
    # Print each bit of a flag byte with its meaning. Table: short/flag1/flag3/flag4/flag5
    print(f"{table} = 0x{value:02X} = {value:08b}b")
    for bit, name in enumerate(FLAG_BITS[table]):
        if name == "-":
            continue
        on = bool(value >> bit & 1)
        print(f"    bit {bit}: {'■' if on else '·'} {name}")

# Driver

class MNL100:
    def __init__(self, port: str, verbose: bool = True, keepalive_s: float = 5, timeout: float = 1.0, standby_wait_s: float = 10.5):
        # port: 'COM3' (Windows), '/dev/cu.usbserial-XXXX' (macOS - use cu., not tty.), or 'SIM' (simulator)
        if port.upper() == "SIM":
            self.ser = SimulatedMNL100()
            print("*** SIMULATOR - no real laser is connected ***")
        else:
            import serial
            # exclusive=True: refuse to open a port that is already open (e.g. an old `laser` object from
            # re-running the connect cell). Two readers on one port steal each other's replies.
            self.ser = serial.Serial(port=port, baudrate = 9600, bytesize = serial.EIGHTBITS, parity = serial.PARITY_NONE, stopbits = serial.STOPBITS_ONE, timeout = timeout, exclusive = True)
        # everything below runs for BOTH the real laser and the simulator
        self.port = port
        self.verbose = verbose
        self.standby_wait_s = standby_wait_s
        self.log = []
        self.glitches = 0 # serial-link glitches recovered by retrying (see _transact)
        # "._" prefix means "internal use only" - not part of the public API
        self._lock = threading.Lock() # to prevent multiple threads from sending commands at the same time
        self._stop = threading.Event() # to stop the keepalive thread
        self._keepalive_s = keepalive_s  # seconds between keepalive polls
        self._ka_thread = threading.Thread(target=self._keepalive, daemon=True)
        self._ka_thread.start()

    # Low Level

    def _transact(self, cmd: str, source: str = "user", retries: int = 2) -> str:
        # Send one command and return the data part of the reply ('' for ACK).
        # A glitch on the serial link (e.g. interference from the laser's discharge hitting the USB adapter)
        # can cut a reply in half. Then: wait for the rest of the broken reply to arrive, throw it away, retry.
        tx =  build_telegram(cmd)
        for attempt in range(retries + 1):
            link_error, strays = None, []
            with self._lock:
                try:
                    self.ser.reset_input_buffer()
                    self.ser.write(tx)
                    for _ in range(3):           # skip up to 2 stray fragments of an earlier broken reply
                        rx = self.ser.read_until(CR)
                        if not rx or self._plausible(cmd, rx):
                            break
                        strays.append(rx)
                except Exception as e:           # serial.SerialException etc.
                    rx, link_error = b"", e
                if strays and link_error is None and self._plausible(cmd, rx):
                    self.glitches += 1
                    print(f"\n   ~ skipped stray fragment {' '.join(show(s) for s in strays)} before reply to {cmd!r}"
                          f" (glitches so far: {self.glitches})")
                broken = link_error is not None or (rx and not self._plausible(cmd, rx))
                if broken and attempt < retries:
                    time.sleep(0.1)              # let the rest of any half-read reply arrive...
                    self.ser.reset_input_buffer()  # ...then discard it
                    self.glitches += 1
                    reason = f"{type(link_error).__name__}" if link_error else f"garbled reply {show(rx)}"
                    self.log.append({"time": datetime.now().strftime("%H:%M:%S.%f")[:-3], "source": source,
                                     "command": cmd, "meaning": describe(cmd), "tx": show(tx), "rx": show(rx),
                                     "result": f"GLITCH ({reason}) - retrying"})
                    print(f"\n   ~ serial glitch on {cmd!r} ({reason}) - retried (glitches so far: {self.glitches})")
                    continue
            break
        if link_error is not None:
            raise MNL100Error(f"serial link failed on {cmd!r} after {retries + 1} tries: {link_error} "
                              "(adapter unplugged? another program using the port?)")

        entry = {"time": datetime.now().strftime("%H:%M:%S.%f")[:-3], "source":source, "command": cmd, "meaning": describe(cmd), "tx": show(tx), "rx": show(rx)}

        try:
            data, entry["result"] = self._parse_reply(cmd, rx)
        except MNL100Error as e:
            entry["result"] = f"ERROR: {e}"
            raise
        finally:
            self.log.append(entry)
            if self.verbose and source == "user":
                print(f"-> TX {entry['tx']:<16} {entry['meaning']}")
                print(f"<- RX {entry['rx']:<16} {entry['result']}")

        return data
    
    STATUS_COMMANDS = ("W", "UT", "UU", "US", "UV", "V3", "P")   # these always answer with data

    @classmethod
    def _plausible(cls, cmd: str, rx: bytes) -> bool:
        # Could this be the laser's complete answer to cmd? (rather than a fragment of an older reply)
        if not rx.endswith(CR):
            return False
        if rx.startswith((b"<@!", ESC + ESC)):
            return True
        return rx == CR and cmd not in cls.STATUS_COMMANDS   # bare ACK only makes sense for non-status commands

    @staticmethod
    def _parse_reply(cmd: str, rx: bytes):
        # Parse the reply from the laser, checking for errors and returning the data part.
        if not rx.endswith(CR):
            raise MNL100Error(f"no/partial reply to {cmd!r}: {rx!r} "
                              "(check cable, port name, laser power)")
        if rx.startswith(ESC + ESC):
            code = rx[2:3].decode(errors = "replace")
            raise MNL100Error(f"laser rejected {cmd!r}: {ERROR_TYPES.get(code, code)}")
        if rx == CR:
            return "", "ACK - accepted"
        text = rx[:-1].decode("ascii")
        if not text.startswith("<@!"):
            raise MNL100Error(f"unexpected reply to {cmd!r}: {rx!r}")
        body, cs = text[:-2], text[-2:]
        ok = checksum(body.encode("ascii")) == cs
        return text[3:-2], f"data reply ({len(text) - 5} chars), checksum {'OK' if ok else 'BAD'}"
    
    def raw(self, cmd: str) -> str:
        # Send any command from the manual (e.g. 'UT', 'm14') and return the reply data
        return self._transact(cmd)
    
    def _keepalive(self):
        # Send a keepalive command to the laser to prevent it from dropping out of run mode.
        while not self._stop.wait(self._keepalive_s):
            try:
                self._transact("W", source="keepalive")
            except MNL100Error:
                pass # laser answered with an error (e.g. busy during lockout) - logged, harmless
            except Exception as e:
                # a real failure (port unplugged, bug...) - say so once, loudly
                if not getattr(self, "_ka_warned", False):
                    print(f"\n!!! keep-alive failed: {e!r} - the laser will stop after 30 s without it")
                    self._ka_warned = True

    def close(self):
        # Stop the keepalive thread and close the serial port
        self._stop.set()
        self._ka_thread.join(timeout = 2)
        self.ser.close()
        if self.verbose:
            print(f"Closed {self.port}")

    def __enter__(self): 
        return self
    
    def __exit__(self, *exc):
        # Close the laser connection when exiting a 'with' block
        try:
            self.stop()
            self.laser_off()
        finally:
            self.close()


    # State control

    def standby(self, wait: bool = True):
        # LASOn: switch HV on -> STANDBY. Laser must be READY. Then ~10s lockout before it will accept other commands.
        self._transact("g")
        if wait:
            end = time.time() + self.standby_wait_s
            while (left := end - time.time()) > 0:
                print(f"\r   laser locked out, warning lamp flashing: {left:4.1f} s ", end="")
                time.sleep(min(0.5, left))
            print("\r  lockout over - laser accepts commands again.        ")
        
    def _safety_command(self, cmd: str, retries: int = 3):
        # STOP and LASER OFF must get through: retry if the reply is lost or garbled.
        # Both are harmless to repeat - the laser just stays stopped / off.
        for attempt in range(1, retries + 1):
            try:
                self._transact(cmd)
                return
            except Exception as e:
                last = e
                print(f"   !! {describe(cmd)} attempt {attempt} failed: {e}")
                time.sleep(0.3)
        raise last

    def laser_off(self):
        self._safety_command("X")

    def stop(self):
        self._safety_command("i")

    
    # Run Modes (from STANDBY)

    def start_repetition(self):
        self._transact("h")

    def start_burst(self):
        self._transact("j")

    def start_external_trigger(self):
        self._transact("u")


    # Parameters

    def set_frequency(self, hz: int):
        if not 1 <= hz <= 60:
            raise ValueError("MNL 100 repetition rate is 1..60 Hz")
        self._transact(f"m{hz:02X}")

    def set_quantity(self, n: int):
        if not 0 <= n <= 65535:
            raise ValueError("MNL 100 burst quantity is 0..65535")
        self._transact(f"l{n:04X}")

    def set_hv_percent(self, pct: int):
        if not 0 <= pct <= 100:
            raise ValueError("MNL 100 HV percent is 0..100 %")
        self._transact(f"n{pct:02X}")


    def shutter(self, open: bool):
        self._transact(f"z{1 if open else 0}")

    def reset_energy_monitor_error(self):
        self._transact("s")

    # Status

    def short_status(self) -> dict:
        # Return a dictionary of the short status flags and their meanings.
        d = self._transact("W")
        f = int(d[1:3], 16)
        return {"raw": d, "flag": f,
                **{name: bool(f >> b & 1) for b, name in enumerate(FLAG_BITS["short"])
                   if name != "-"}}
    
    def status7(self) -> dict:
        # Returns a dictionary of the laser's state and settings from STATUS 7 (UT command).
        d = self._transact("UT")
        f1, f3 = int(d[2:4], 16), int(d[6:8], 16)
        mode = {0: "off", 1: "repetition", 2: "burst", 4: "external trigger"}.get(f1 >> 4 & 0xF, f"unknown ({f1 >> 4:04b})")
        return {
            "raw": d, "flag1": f1, "flag3": f3,
            "ready": bool(f1 & 0x04), "standby": bool(f1 & 0x08),
            "shutter_open": bool(f1 & 0x01), "mode": mode,
            "quantity_set": int(d[8:12], 16), "frequency_set": int(d[12:14], 16),
            "hv_percent": int(d[14:16], 16)}
    
    def status8(self) -> dict:
        # Returns a dictionary of the laser's live readings from STATUS 8 (UU command).
        d = self._transact("UU")
        return {
            "raw": d, "flag4": int(d[2:4], 16), "flag5": int(d[4:6], 16),
            "supply_V": round(int(d[6:8], 16) * 0.11, 2), "temp2_C": int(d[8:10], 16), "temp1_C": int(d[10:12], 16),
            "energy_avg_uJ": round(int(d[12:16], 16) * 250/64000, 2),
            "burst_remaining": int(d[16:20], 16),
            "shot_counter": int(d[20:28], 16)}
    
    def version(self) -> str:
        # Returns the firmware version string from the laser (V3 command).
        # reply data is 'V' uu vv ää öö wwwwwwww nn <text> - it starts with 'V' only, not 'V3'
        d = self._transact("V3")
        return f"firmware {d[9:17].strip()}, type {d[19:]}" if len(d) > 19 else d
    
    def serial_numbers(self) -> dict:
        # Returns a dictionary of the laser's serial numbers from the US command.
        d = self._transact("US")
        return {"laser": d[2:10], "energy_monitor": d[10:14]}
    
# Simulator - behaves like the laser's serial port

class SimulatedMNL100:
    def __init__(self, lockout_s: float = 10.0):
        self.lockout_s = lockout_s
        self.ready, self.standby, self.mode = True, False, 0 # mode bits as flag1
        self.freq, self.qty, self.hv = 10, 100, 39   # hv = internal regulated-HV reading (no HV control)
        self.att_tr = 200                            # attenuator transmission in 0.5 % steps (200 = 100 %)
        self.shots, self.burst_left = 1_234_567, 0
        self._run_t0 = None
        self._locked_until = 0.0
        self._out = b""

    def reset_input_buffer(self):
        # Clear the output buffer (simulating the laser's behavior)
        self._out = b""

    def close(self):
        pass

    def read_until(self, _term = CR):
        # Simulate reading from the laser's serial port until a termination character (CR).
        out, self._out = self._out, b""
        return out
    
    def write(self, data: bytes):
        # Simulate writing to the laser's serial port, processing the command and generating a reply.
        self._update_shots()
        self._out = self._handle(data)

    # Internals
    def _update_shots(self):
        # Update the shot counter based on the elapsed time and the current mode (repetition or burst).
        if self._run_t0 is None:
            return
        n = int((time.time() - self._run_t0) * self.freq)
        if self.mode == 2:
            n = min(n, self.burst_left)
            self.burst_left -= n
            if self.burst_left == 0:
                self.mode, self._run_t0 = 0, None
        if n and self._run_t0 is not None:
            self._run_t0 += n / self.freq
        self.shots += n


    @staticmethod
    def _err(code: str) -> bytes:
        # Return an error reply with the given error code (1-6) as bytes.
        body = ESC + ESC + code.encode()
        return body + checksum(body).encode() + CR
    
    @staticmethod
    def _reply(data: str) -> bytes:
        # Return a data reply with the given data string as bytes.
        body = f"<@!{data}".encode()
        return body + checksum(body).encode() + CR
    
    def _handle(self, data: bytes) -> bytes:
        # Handle a command sent to the simulated laser, returning the appropriate reply.
        if len(data) < 6 or not data.startswith(b"#!@") or not data.endswith(CR):
            return self._err("2")
        
        body, cs = data[:-3], data[-3:-1].decode()
        if checksum(body) != cs:
            return self._err("1")
        
        cmd = body[3:].decode()

        # status requests always work
        if cmd == "W":
            f = (self.standby << 0) | ((self.mode !=0) << 1)
            return self._reply(f"W{f:02X}")
        if cmd == "UT":
            f1 = (self.ready << 2) | (self.standby << 3) | (self.mode << 4)
            return self._reply(f"UT{f1:02X}0003{self.qty:04X}{self.freq:02X}{self.hv:02X}00000000")
        if cmd == "UU":
            firing = self.mode in (1,2)
            e = int(120 * 64000/250 * (1 if firing else 0))     # placeholder 120 µJ while firing
            return self._reply(f"UU0000D91E{29 + 3 * firing:02X}{e:04X}"
                               f"{self.burst_left:04X}{self.shots:08X}")
        if cmd == "V3":
            # mirrors the real lab laser: release byte 0x73 = MNL, energy monitor, attenuator, no HV control, no shutter
            return self._reply("VBD732002V 002.610FMNL100 (103 PD)")
        if cmd == "UV":   # attenuator status: aa=01 initialised, set pos, actual pos, transmission in 0.5 % steps
            pos = self.att_tr * 2
            return self._reply(f"UV01{pos:04X}{pos:04X}{self.att_tr:02X}")
        if cmd == "US":
            return self._reply("US000123450678")
        if time.time() < self._locked_until:
            return self._err("5")
        if cmd == "X":
            self.standby, self.mode, self._run_t0 = False, 0, None
            return CR
        if cmd == "g":
            if not self.ready:
                return self._err("4")
            self.standby = True
            self._locked_until = time.time() + self.lockout_s
            return CR
        if cmd == "i":
            self.mode, self._run_t0 = 0, None
            return CR
        if cmd in ("h", "j", "u"):
            if not self.standby or self.mode:
                return self._err("4")
            self.mode = {"h": 1, "j": 2, "u": 4}[cmd]
            if cmd == "j":
                self.burst_left = self.qty
            if cmd != "u":
                self._run_t0 = time.time()
            return CR
        try:
            if cmd[0] == "m" and len(cmd) == 3:
                v = int(cmd[1:], 16)
                if not 1 <= v <= 30:          # MNL 103 PD max 30 Hz
                    return self._err("3")
                self.freq = v
                return CR
            if cmd[0] == "l" and len(cmd) == 5:
                self.qty = int(cmd[1:], 16)
                return CR
            if cmd.startswith("O4") and len(cmd) == 4:     # attenuator transmission, nn = 0.5 % steps
                v = int(cmd[2:], 16)
                if v > 200:
                    return self._err("3")
                self.att_tr = v
                return CR
            if cmd == "O60000":                            # re-initialise attenuator
                return CR
        except ValueError:
            return self._err("2")
        if cmd[0] in ("n", "o"):
            return self._err("4") # like the lab laser: no HV control -> rejected
        if cmd == "s":
            return CR             # reset energy-monitor error
        if cmd in ("z1", "z0"):
            return self._err("4") # no shutter fitted
        return self._err("2") # unknown command
    
# Quick standalone ex
if __name__ == "__main__":
    PORT = "SIM" # <-- your port, e.g. "COM3" (Windows) or "/dev/cu.usbserial-XXXX" (macOS)
    with MNL100(PORT, standby_wait_s= 10.5) as laser:
        print(laser.version())
        laser.set_frequency(20)
        laser.standby()
        laser.start_repetition()
        for _ in range(5):
            time.sleep(1)
            s = laser.status8()
            print(f"  shots = {s['shot_counter']}  E={s['energy_avg_uJ']} uJ ")