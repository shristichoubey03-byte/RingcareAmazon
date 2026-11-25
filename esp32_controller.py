"""
Backward compatibility bridge: re-exports arduino_controller as esp32_controller.
RingCare now uses Arduino Uno (ATmega328P) with buzzer on Digital Pin D8 via USB Serial.
"""

from arduino_controller import ArduinoController, arduino_controller, esp32_controller

__all__ = ["ArduinoController", "arduino_controller", "esp32_controller"]
