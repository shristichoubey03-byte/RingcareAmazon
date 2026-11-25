"""
Arduino Uno (ATmega328P) USB Hardware Controller Module for RingCare.

Integrates an external Arduino Uno microcontroller development board connected via
USB Serial to drive an active buzzer on Digital Pin D8 for emergency care check-in
alerts, sensor telemetry, and manual testing.

Hardware Specifications:
- Microcontroller: Arduino Uno (ATmega328P @ 16 MHz, 5V logic)
- Physical Connection: USB Data Cable (USB Type-B to Type-A)
- Buzzer Signal (I/O): Digital Pin D8
- Buzzer VCC: 5V power rail from Arduino Uno
- Buzzer GND: Common ground (GND) on Arduino Uno
- Communication: USB Serial (COM port, 115200 baud) - No Wi-Fi required

Serial Protocol (Newline terminated):
- "ALERT\n" : Activates physical buzzer on Digital Pin D8 (pulsing alarm pattern with auto-timeout).
- "SAFE\n"  : Deactivates buzzer immediately (returns to IDLE).
- "STOP\n"  : Alias for SAFE.
- "TEST\n"  : Plays a short 3-beep test sequence on Digital Pin D8 and stops automatically.
- "STATUS\n": Queries current operational state and sensor telemetry from Arduino Uno.

Features:
- Thread-safe serial communication with threading.Lock().
- Persistent connection pooling (avoids re-opening the port per command, preventing DTR auto-resets).
- Auto-detection of Arduino Uno COM ports (ATmega16U2, CH340, FTDI).
- Graceful disconnect/reconnect recovery without crashing the Flask backend.
- High-fidelity simulated fallback when hardware is not plugged in (DEMO_MODE).
- Automatic database logging to hardware_events table.
- Real-time Server-Sent Events (SSE) broadcasting to the caregiver dashboard.
"""

import time
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

from config import (
    ARDUINO_PORT,
    ARDUINO_BAUDRATE,
    ARDUINO_TIMEOUT,
    ARDUINO_MAX_BUZZER_DURATION,
    DEMO_MODE,
    get_now,
    format_timestamp
)
import database
import webhook_handler

logger = logging.getLogger("ringcare.arduino")

# Known USB-to-UART bridge Vendor/Product IDs for Arduino Uno & Compatible Boards:
# - Official Arduino Uno R3 (ATmega16U2): VID 0x2341, PID 0x0043 / 0x0001 / 0x0243
# - Arduino Uno SMD / Dog Hunter: VID 0x2A03, PID 0x0043
# - CH340 USB-Serial (Common Uno clones): VID 0x1A86, PID 0x7523 / 0x55D4
# - FTDI FT232R (Uno clone variants): VID 0x0403, PID 0x6001
# - Silicon Labs CP210x (Uno variants): VID 0x10C4, PID 0xEA60
KNOWN_ARDUINO_VID_PIDS = [
    (0x2341, 0x0043),  # Arduino Uno R3 (ATmega16U2)
    (0x2341, 0x0001),  # Arduino Uno R1/R2
    (0x2341, 0x0243),  # Arduino Uno R3 clone
    (0x2A03, 0x0043),  # Arduino Uno SMD
    (0x1A86, 0x7523),  # QinHeng CH340 Uno clone
    (0x1A86, 0x55D4),  # QinHeng CH9102
    (0x0403, 0x6001),  # FTDI FT232R
    (0x10C4, 0xEA60),  # Silicon Labs CP210x
]


class ArduinoController:
    """Thread-safe controller for Arduino Uno USB Serial communication."""

    def __init__(self):
        self.lock = threading.Lock()
        self.serial_conn: Optional[Any] = None
        self.configured_port: str = ARDUINO_PORT
        self.baudrate: int = ARDUINO_BAUDRATE
        self.timeout: float = ARDUINO_TIMEOUT
        self.max_duration: int = ARDUINO_MAX_BUZZER_DURATION

        # Hardware Specifications
        self.microcontroller: str = "Arduino Uno (ATmega328P)"
        self.buzzer_pin: str = "Digital Pin D8"
        self.operating_voltage: str = "5V"
        self.connectivity: str = "USB Serial (No Wi-Fi)"

        # Hardware & Telemetry State
        self.connection_status: str = "disconnected"  # 'connected', 'disconnected', 'error'
        self.buzzer_status: str = "OFF"               # 'ON', 'OFF', 'TESTING'
        self.last_activation_reason: str = "System Initialized"
        self.last_activation_time: Optional[str] = None
        self.last_activation_source: str = "system"   # 'automatic', 'manual', 'system'
        self.last_error: Optional[str] = None
        self.active_alert_id: Optional[str] = None
        self.active_port_name: str = ""
        self.latest_sensor_telemetry: Dict[str, Any] = {
            "buzzer_pin": "D8",
            "state": "IDLE",
            "uptime_seconds": 0,
            "signal": "LOW"
        }

        # Auto-timeout watchdog thread handle
        self._auto_timeout_timer: Optional[threading.Timer] = None

    def list_ports(self) -> List[Dict[str, str]]:
        """Scans and returns all available USB COM ports on the host system."""
        ports_list = []
        if not SERIAL_AVAILABLE:
            return ports_list

        try:
            for p in serial.tools.list_ports.comports():
                ports_list.append({
                    "port": p.device,
                    "description": p.description or "Serial Port",
                    "hwid": p.hwid or ""
                })
        except Exception as e:
            logger.error(f"Error enumerating COM ports: {e}")
        return ports_list

    def auto_detect_port(self) -> Optional[str]:
        """
        Attempts to detect an Arduino Uno COM port by matching known Arduino / CH340 / FTDI
        VID/PIDs or description keywords ('Arduino', 'Uno', 'CH340', 'USB-SERIAL', 'ATmega').
        """
        if not SERIAL_AVAILABLE:
            return None

        try:
            available = list(serial.tools.list_ports.comports())
            if not available:
                return None

            # 1. Match against known Arduino Uno USB bridge VID/PIDs
            for p in available:
                if p.vid and p.pid:
                    for vid, pid in KNOWN_ARDUINO_VID_PIDS:
                        if p.vid == vid and p.pid == pid:
                            logger.info(f"Auto-detected Arduino Uno by VID/PID on {p.device} ({p.description})")
                            return p.device

            # 2. Match by description keywords
            for p in available:
                desc = (p.description or "").lower()
                if any(kw in desc for kw in ("arduino", "uno", "ch340", "usb serial", "usb-serial", "atmega", "ftdi")):
                    logger.info(f"Auto-detected Arduino Uno by description on {p.device} ({p.description})")
                    return p.device

            # 3. Fallback: If only one COM port exists, select it
            if len(available) == 1:
                logger.info(f"Single COM port found; selecting {available[0].device} for Arduino Uno")
                return available[0].device

        except Exception as e:
            logger.error(f"Error during Arduino Uno auto-detection: {e}")
        return None

    def connect_arduino(self, port: Optional[str] = None, baudrate: Optional[int] = None) -> bool:
        """
        Establishes a persistent USB Serial connection to Arduino Uno.
        Avoids repeatedly opening an already active connection to prevent unwanted DTR auto-resets.
        """
        with self.lock:
            target_port = port or self.configured_port
            target_baud = baudrate or self.baudrate

            # Auto-detect if no port specified
            if not target_port:
                detected = self.auto_detect_port()
                if detected:
                    target_port = detected
                    self.configured_port = detected

            if not target_port:
                self.connection_status = "disconnected"
                self.last_error = "No Arduino Uno COM port detected or configured. Please connect via USB."
                logger.warning(self.last_error)
                return False

            if not SERIAL_AVAILABLE:
                self.connection_status = "error"
                self.last_error = "pyserial library is not installed."
                logger.error(self.last_error)
                return False

            # Check if existing connection on same port is still healthy
            if self.serial_conn and self.serial_conn.is_open:
                if self.active_port_name == target_port:
                    self.connection_status = "connected"
                    self.last_error = None
                    return True
                else:
                    self._close_serial_unlocked()

            try:
                logger.info(f"Connecting to Arduino Uno on {target_port} at {target_baud} baud...")
                self.serial_conn = serial.Serial(
                    port=target_port,
                    baudrate=target_baud,
                    timeout=0.5,
                    write_timeout=1.0
                )
                # Arduino Uno resets upon DTR toggle; allow 1.5s settling time for bootloader
                time.sleep(1.5)

                # Flush initial bootloader messages
                if self.serial_conn.in_waiting:
                    init_msg = self.serial_conn.read(self.serial_conn.in_waiting).decode("utf-8", errors="ignore").strip()
                    logger.info(f"Arduino Uno Boot Msg: {init_msg}")

                self.connection_status = "connected"
                self.active_port_name = target_port
                self.last_error = None
                logger.info(f"Successfully connected to Arduino Uno on {target_port} (Buzzer: Pin D8)")
                self._broadcast_hardware_update()
                return True

            except serial.SerialException as se:
                self.connection_status = "error"
                self.last_error = f"USB Serial Error on {target_port}: {str(se)}"
                logger.error(self.last_error)
                self._close_serial_unlocked()
                self._broadcast_hardware_update()
                return False
            except Exception as e:
                self.connection_status = "error"
                self.last_error = f"Unexpected error connecting to {target_port}: {str(e)}"
                logger.error(self.last_error)
                self._close_serial_unlocked()
                self._broadcast_hardware_update()
                return False

    def disconnect_arduino(self) -> bool:
        """Closes the active serial connection safely."""
        with self.lock:
            self._cancel_auto_timeout_unlocked()
            self._close_serial_unlocked()
            self.connection_status = "disconnected"
            self.buzzer_status = "OFF"
            logger.info("Arduino Uno disconnected by user or application shutdown.")
            self._broadcast_hardware_update()
            return True

    def _close_serial_unlocked(self):
        """Internal helper to close serial without re-acquiring lock."""
        if self.serial_conn:
            try:
                if self.serial_conn.is_open:
                    self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None

    def _send_raw_command_unlocked(self, cmd: str) -> Optional[str]:
        """
        Sends raw ASCII command terminated with newline and reads confirmation.
        Non-blocking with a strict 0.5s read deadline to avoid freezing web requests.
        Must be called while self.lock is acquired.
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            return None

        try:
            line_to_send = (cmd.strip() + "\n").encode("utf-8")
            self.serial_conn.write(line_to_send)
            self.serial_conn.flush()

            # Non-blocking response read with strict 0.5s deadline
            response_line = ""
            start_t = time.time()
            while time.time() - start_t < 0.5:
                if self.serial_conn.in_waiting:
                    raw = self.serial_conn.readline()
                    response_line = raw.decode("utf-8", errors="ignore").strip()
                    if response_line:
                        break
                time.sleep(0.02)

            if not response_line:
                response_line = f"CMD_SENT:{cmd.strip()}"
            else:
                response_line = response_line.strip("\x00\r\n\t ")
                if not response_line:
                    response_line = f"CMD_SENT:{cmd.strip()}"

            logger.info(f"Arduino Uno TX: '{cmd.strip()}' -> RX: '{response_line}'")
            return response_line

        except serial.SerialException as se:
            logger.error(f"Serial communication failure sending '{cmd}': {se}")
            self.connection_status = "error"
            self.last_error = f"Serial TX/RX failure: {str(se)}"
            self._close_serial_unlocked()
            return None
        except Exception as e:
            logger.error(f"Unexpected error communicating with Arduino Uno: {e}")
            self.connection_status = "error"
            self.last_error = str(e)
            return None

    def send_alert(self, reason: str = "Care Check-In Inactivity Alert", alert_id: Optional[str] = None, source: str = "automatic") -> Dict[str, Any]:
        """
        Sends ALERT command to Arduino Uno to activate physical buzzer on Pin D8.
        Thread-safe, handles reconnections, logs to database, and triggers auto-timeout watchdog.
        """
        with self.lock:
            now_dt = get_now()
            formatted_time = format_timestamp(now_dt)

            self.last_activation_reason = reason
            self.last_activation_time = formatted_time
            self.last_activation_source = source
            self.active_alert_id = alert_id
            self.buzzer_status = "ON"
            self.latest_sensor_telemetry["state"] = "ALERTING"
            self.latest_sensor_telemetry["signal"] = "HIGH"

            # Attempt auto-connect if disconnected
            if not self.serial_conn or not self.serial_conn.is_open:
                self._reconnect_unlocked()

            response = None
            if self.serial_conn and self.serial_conn.is_open:
                response = self._send_raw_command_unlocked("ALERT")
                if response:
                    self.connection_status = "connected"
                    self.last_error = None
            else:
                if DEMO_MODE:
                    logger.info(f"DEMO MODE: Simulated Arduino Uno ALERT triggered ({reason}) on Pin D8.")
                else:
                    logger.warning(f"Arduino Uno not connected; alert generated in software only: {reason}")

            # Schedule automatic timeout watchdog (60 seconds)
            self._schedule_auto_timeout_unlocked()

            # Log to SQLite hardware_events table
            try:
                database.log_hardware_event(
                    event_type="alert_start",
                    alert_id=alert_id,
                    activation_source=source,
                    device_status=self.connection_status,
                    message=f"Buzzer ACTIVATED on Arduino Pin D8 ({source.upper()}): {reason}"
                )
            except Exception as e:
                logger.error(f"Failed to log hardware event: {e}")

            result = {
                "status": "success" if (self.connection_status == "connected" or DEMO_MODE) else "hardware_disconnected",
                "microcontroller": self.microcontroller,
                "buzzer_pin": self.buzzer_pin,
                "buzzer_status": "ON",
                "reason": reason,
                "source": source,
                "timestamp": formatted_time,
                "connection_status": self.connection_status,
                "arduino_response": response or ("SIMULATED_OK" if DEMO_MODE else None),
                "sensor_data": self.latest_sensor_telemetry
            }

            self._broadcast_hardware_update(result)
            return result

    def stop_alert(self, source: str = "manual", reason: str = "Alert stopped by caregiver") -> Dict[str, Any]:
        """
        Sends SAFE / STOP command to Arduino Uno to stop physical buzzer on Pin D8.
        Cancels any pending auto-timeout watchdog.
        """
        with self.lock:
            now_dt = get_now()
            formatted_time = format_timestamp(now_dt)

            self._cancel_auto_timeout_unlocked()
            self.buzzer_status = "OFF"
            self.last_activation_reason = f"Stopped: {reason}"
            self.last_activation_time = formatted_time
            self.last_activation_source = source
            self.latest_sensor_telemetry["state"] = "IDLE"
            self.latest_sensor_telemetry["signal"] = "LOW"

            response = None
            if self.serial_conn and self.serial_conn.is_open:
                response = self._send_raw_command_unlocked("SAFE")
                if response:
                    self.connection_status = "connected"
                    self.last_error = None
            else:
                if DEMO_MODE:
                    logger.info("DEMO MODE: Simulated Arduino Uno SAFE triggered. Buzzer on Pin D8 turned OFF.")

            # Log to SQLite hardware_events table
            try:
                database.log_hardware_event(
                    event_type="alert_stop",
                    alert_id=self.active_alert_id,
                    activation_source=source,
                    device_status=self.connection_status,
                    message=f"Buzzer STOPPED on Arduino Pin D8 ({source.upper()}): {reason}"
                )
            except Exception as e:
                logger.error(f"Failed to log hardware event: {e}")

            self.active_alert_id = None

            result = {
                "status": "success",
                "microcontroller": self.microcontroller,
                "buzzer_pin": self.buzzer_pin,
                "buzzer_status": "OFF",
                "reason": reason,
                "source": source,
                "timestamp": formatted_time,
                "connection_status": self.connection_status,
                "arduino_response": response or ("SIMULATED_OK" if DEMO_MODE else None),
                "sensor_data": self.latest_sensor_telemetry
            }

            self._broadcast_hardware_update(result)
            return result

    def test_buzzer(self, source: str = "manual") -> Dict[str, Any]:
        """
        Sends TEST command to Arduino Uno to ring buzzer for 3 short test beeps on Pin D8.
        Auto-reverts to OFF status afterwards.
        """
        with self.lock:
            now_dt = get_now()
            formatted_time = format_timestamp(now_dt)

            self.last_activation_reason = "Manual Hardware Test"
            self.last_activation_time = formatted_time
            self.last_activation_source = source
            self.buzzer_status = "TESTING"
            self.latest_sensor_telemetry["state"] = "TESTING"

            # Attempt auto-connect if disconnected
            if not self.serial_conn or not self.serial_conn.is_open:
                self._reconnect_unlocked()

            response = None
            if self.serial_conn and self.serial_conn.is_open:
                response = self._send_raw_command_unlocked("TEST")
            else:
                if DEMO_MODE:
                    logger.info("DEMO MODE: Simulated Arduino Uno TEST pattern executed (3 short beeps on Pin D8).")

            # Log to SQLite hardware_events table
            try:
                database.log_hardware_event(
                    event_type="test_buzzer",
                    alert_id=None,
                    activation_source=source,
                    device_status=self.connection_status,
                    message="Manual buzzer test sequence triggered by caregiver (Arduino Pin D8)"
                )
            except Exception as e:
                logger.error(f"Failed to log hardware event: {e}")

            # Revert buzzer status to OFF after short delay
            def _reset_test_state():
                with self.lock:
                    if self.buzzer_status == "TESTING":
                        self.buzzer_status = "OFF"
                        self.latest_sensor_telemetry["state"] = "IDLE"
                        self.latest_sensor_telemetry["signal"] = "LOW"
                        self._broadcast_hardware_update()

            timer = threading.Timer(3.0, _reset_test_state)
            timer.daemon = True
            timer.start()

            result = {
                "status": "success",
                "microcontroller": self.microcontroller,
                "buzzer_pin": self.buzzer_pin,
                "buzzer_status": "TESTING",
                "message": "Buzzer test sequence triggered on Arduino Pin D8 (3 short beeps).",
                "timestamp": formatted_time,
                "connection_status": self.connection_status,
                "arduino_response": response or ("SIMULATED_OK" if DEMO_MODE else None),
                "sensor_data": self.latest_sensor_telemetry
            }

            self._broadcast_hardware_update(result)
            return result

    def _reconnect_unlocked(self):
        """Attempts reconnect without locking (caller must hold lock)."""
        port = self.configured_port or self.auto_detect_port()
        if port and SERIAL_AVAILABLE:
            try:
                self.serial_conn = serial.Serial(
                    port=port,
                    baudrate=self.baudrate,
                    timeout=0.5,
                    write_timeout=1.0
                )
                time.sleep(1.5)
                self.connection_status = "connected"
                self.active_port_name = port
                self.last_error = None
                logger.info(f"Reconnected to Arduino Uno on {port}")
            except Exception as e:
                self.connection_status = "disconnected"
                self.last_error = str(e)

    def _schedule_auto_timeout_unlocked(self):
        """Schedules watchdog timer to auto-turn off buzzer after max duration."""
        self._cancel_auto_timeout_unlocked()

        def _timeout_handler():
            logger.info(f"Watchdog: Arduino Buzzer reached maximum duration ({self.max_duration}s). Turning off.")
            self.stop_alert(source="automatic", reason=f"Auto-stopped after {self.max_duration}s maximum safety duration")

        self._auto_timeout_timer = threading.Timer(float(self.max_duration), _timeout_handler)
        self._auto_timeout_timer.daemon = True
        self._auto_timeout_timer.start()

    def _cancel_auto_timeout_unlocked(self):
        """Cancels any running auto-timeout timer."""
        if self._auto_timeout_timer:
            try:
                self._auto_timeout_timer.cancel()
            except Exception:
                pass
            self._auto_timeout_timer = None

    def get_connection_status(self) -> Dict[str, Any]:
        """Returns comprehensive real-time status of the Arduino Uno hardware subsystem."""
        with self.lock:
            # Active check if serial connection is still alive
            if self.serial_conn:
                if not self.serial_conn.is_open:
                    self.connection_status = "disconnected"
                else:
                    self.connection_status = "connected"
            else:
                if self.connection_status != "error":
                    self.connection_status = "disconnected"

            available_ports = self.list_ports()
            port_names = [p["port"] for p in available_ports]

            effective_status = self.connection_status
            display_port = self.active_port_name or self.configured_port or (port_names[0] if port_names else "None detected")

            return {
                "status": "success",
                "microcontroller": self.microcontroller,
                "buzzer_pin": self.buzzer_pin,
                "operating_voltage": self.operating_voltage,
                "connectivity": self.connectivity,
                "wifi_enabled": False,  # Arduino Uno has no built-in Wi-Fi; uses USB Serial exclusively
                "connection_status": effective_status,
                "port": display_port,
                "configured_port": self.configured_port,
                "active_port": self.active_port_name,
                "available_ports": port_names,
                "ports_details": available_ports,
                "baudrate": self.baudrate,
                "buzzer_status": self.buzzer_status,
                "last_activation_reason": self.last_activation_reason,
                "last_activation_time": self.last_activation_time or "No recent activations",
                "last_activation_source": self.last_activation_source,
                "last_error": self.last_error,
                "max_duration_seconds": self.max_duration,
                "sensor_data": self.latest_sensor_telemetry,
                "sensor_telemetry": self.latest_sensor_telemetry,
                "demo_mode": DEMO_MODE
            }

    def _broadcast_hardware_update(self, extra_data: Optional[Dict[str, Any]] = None):
        """Pushes real-time hardware status changes through the existing SSE stream."""
        try:
            payload = {
                "type": "hardware_status",
                "microcontroller": self.microcontroller,
                "buzzer_pin": self.buzzer_pin,
                "connection_status": self.connection_status,
                "port": self.active_port_name or self.configured_port,
                "buzzer_status": self.buzzer_status,
                "last_activation_reason": self.last_activation_reason,
                "last_activation_time": self.last_activation_time,
                "last_activation_source": self.last_activation_source,
                "last_error": self.last_error,
                "sensor_data": self.latest_sensor_telemetry
            }
            if extra_data:
                payload.update(extra_data)
            webhook_handler.broadcast_event(payload)
        except Exception as e:
            logger.debug(f"Could not broadcast hardware SSE update: {e}")

    # Aliases for backward compatibility
    connect_esp32 = connect_arduino
    disconnect_esp32 = disconnect_arduino


# Global singleton instance
arduino_controller = ArduinoController()

# Backward compatibility alias
esp32_controller = arduino_controller
