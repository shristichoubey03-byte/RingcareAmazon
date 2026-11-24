/*
 * =====================================================================================
 * RingCare – ESP32 USB Hardware Buzzer Controller
 * Firmware for ESP32-WROOM-32 Development Board
 * 
 * Hardware Connections:
 * - Active Buzzer Signal (I/O) -> ESP32 GPIO 23
 * - Active Buzzer GND          -> ESP32 GND
 * - Active Buzzer VCC          -> ESP32 3.3V (or 5V/VIN for 5V active buzzers)
 * 
 * Serial Protocol (115200 baud, 8N1, Newline terminated):
 * - Commands from Flask Backend:
 *     "ALERT\n"   -> Triggers alarm pattern (alternating pulsing beeps), auto-timeout 60s
 *     "SAFE\n"    -> Instantly deactivates buzzer and returns to IDLE
 *     "STOP\n"    -> Alias for SAFE
 *     "TEST\n"    -> Emits 3 quick distinctive beeps, then returns to IDLE
 *     "STATUS\n"  -> Returns current operational state
 * 
 * - Responses to Flask Backend:
 *     "OK:ALERT_STARTED"
 *     "OK:STOPPED"
 *     "OK:TEST_STARTED"
 *     "STATUS:IDLE" / "STATUS:ALERTING" / "STATUS:TESTING"
 *     "TIMEOUT:AUTO_STOPPED"
 * =====================================================================================
 */

#include <Arduino.h>

// Hardware Pin Configuration
const int BUZZER_PIN = 23;            // Active buzzer signal connected to GPIO 23
const int ONBOARD_LED = 2;            // Optional: ESP32 onboard blue LED (GPIO 2) for visual feedback

// Safety Timing Configurations
const unsigned long MAX_ALERT_DURATION_MS = 60000;  // 60 seconds maximum ringing before auto-stop
const unsigned long BEEP_ON_INTERVAL_MS   = 350;    // Beep duration in ALERT mode
const unsigned long BEEP_OFF_INTERVAL_MS  = 200;    // Pause duration in ALERT mode

// Operational States
enum BuzzerState {
  STATE_IDLE,
  STATE_ALERTING,
  STATE_TESTING
};

BuzzerState currentState = STATE_IDLE;
unsigned long alertStartTime = 0;
unsigned long lastToggleTime = 0;
bool buzzerOutputLevel = false;

// Testing Sequence Tracking
int testBeepCount = 0;
const int MAX_TEST_BEEPS = 3;
const unsigned long TEST_BEEP_DURATION_MS = 150;
const unsigned long TEST_PAUSE_DURATION_MS = 150;

// Serial Command Buffer
String inputBuffer = "";

void setBuzzer(bool state) {
  buzzerOutputLevel = state;
  digitalWrite(BUZZER_PIN, state ? HIGH : LOW);
  digitalWrite(ONBOARD_LED, state ? HIGH : LOW);
}

void startAlert() {
  currentState = STATE_ALERTING;
  alertStartTime = millis();
  lastToggleTime = millis();
  setBuzzer(true);
  Serial.println("OK:ALERT_STARTED");
}

void stopBuzzer(const char* reason = "OK:STOPPED") {
  currentState = STATE_IDLE;
  setBuzzer(false);
  Serial.println(reason);
}

void startTest() {
  currentState = STATE_TESTING;
  testBeepCount = 0;
  lastToggleTime = millis();
  setBuzzer(true);
  Serial.println("OK:TEST_STARTED");
}

void processCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd.length() == 0) return;

  if (cmd == "ALERT") {
    startAlert();
  } else if (cmd == "SAFE" || cmd == "STOP") {
    stopBuzzer("OK:STOPPED");
  } else if (cmd == "TEST") {
    startTest();
  } else if (cmd == "STATUS") {
    if (currentState == STATE_ALERTING) {
      Serial.println("STATUS:ALERTING");
    } else if (currentState == STATE_TESTING) {
      Serial.println("STATUS:TESTING");
    } else {
      Serial.println("STATUS:IDLE");
    }
  } else {
    Serial.print("ERROR:UNKNOWN_COMMAND:");
    Serial.println(cmd);
  }
}

void setup() {
  // Initialize Serial Communication at 115200 baud
  Serial.begin(115200);
  
  // Configure GPIO Pins
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(ONBOARD_LED, OUTPUT);
  
  // Ensure buzzer starts completely silent
  setBuzzer(false);
  
  // Allow power rail to stabilize
  delay(100);
  
  Serial.println("\n--- RingCare ESP32 Hardware Controller Initialized ---");
  Serial.println("READY:BUZZER_GPIO_23:115200");
}

void loop() {
  // 1. Read incoming serial commands from Flask Backend non-blockingly
  while (Serial.available() > 0) {
    char inChar = (char)Serial.read();
    if (inChar == '\n' || inChar == '\r') {
      if (inputBuffer.length() > 0) {
        processCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      // Prevent buffer overflow
      if (inputBuffer.length() < 64) {
        inputBuffer += inChar;
      }
    }
  }

  unsigned long currentMillis = millis();

  // 2. Non-blocking state machine for Alerting
  if (currentState == STATE_ALERTING) {
    // Check auto-stop safety timeout (e.g. 60 seconds)
    if (currentMillis - alertStartTime >= MAX_ALERT_DURATION_MS) {
      stopBuzzer("TIMEOUT:AUTO_STOPPED");
      return;
    }

    // Pulsing beeping pattern for high attention without deafening
    if (buzzerOutputLevel) {
      if (currentMillis - lastToggleTime >= BEEP_ON_INTERVAL_MS) {
        setBuzzer(false);
        lastToggleTime = currentMillis;
      }
    } else {
      if (currentMillis - lastToggleTime >= BEEP_OFF_INTERVAL_MS) {
        setBuzzer(true);
        lastToggleTime = currentMillis;
      }
    }
  }

  // 3. Non-blocking state machine for Test Sequence (3 short beeps)
  else if (currentState == STATE_TESTING) {
    if (buzzerOutputLevel) {
      if (currentMillis - lastToggleTime >= TEST_BEEP_DURATION_MS) {
        setBuzzer(false);
        lastToggleTime = currentMillis;
        testBeepCount++;
        if (testBeepCount >= MAX_TEST_BEEPS) {
          currentState = STATE_IDLE;
          Serial.println("OK:TEST_COMPLETED");
        }
      }
    } else {
      if (currentMillis - lastToggleTime >= TEST_PAUSE_DURATION_MS) {
        if (testBeepCount < MAX_TEST_BEEPS) {
          setBuzzer(true);
          lastToggleTime = currentMillis;
        }
      }
    }
  }
}
