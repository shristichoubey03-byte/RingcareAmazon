/*
 * =====================================================================================
 * RingCare – Arduino Uno (ATmega328P) USB Hardware Buzzer Controller
 * Firmware for Arduino Uno Microcontroller (ATmega328P @ 16 MHz, 5V)
 * 
 * Hardware Connections:
 * - Active Buzzer Signal (I/O) -> Arduino Uno Digital Pin D8
 * - Active Buzzer GND          -> Arduino Uno GND
 * - Active Buzzer VCC          -> Arduino Uno 5V Power Pin
 * - Onboard Status Indicator   -> Arduino Uno Pin 13 (LED_BUILTIN)
 * 
 * Serial Protocol (115200 baud, 8N1, Newline terminated):
 * - Commands from Flask Backend:
 *     "ALERT\n"   -> Triggers alarm pattern on Pin D8 (pulsing beeps), auto-timeout 60s
 *     "SAFE\n"    -> Instantly deactivates buzzer and returns to IDLE
 *     "STOP\n"    -> Alias for SAFE
 *     "TEST\n"    -> Emits 3 quick distinctive beeps on Pin D8, then returns to IDLE
 *     "STATUS\n"  -> Returns current operational state and sensor telemetry
 * 
 * - Responses to Flask Backend:
 *     "OK:ALERT_STARTED,PIN=D8"
 *     "OK:STOPPED,PIN=D8"
 *     "OK:TEST_STARTED,PIN=D8"
 *     "STATUS:IDLE,PIN=D8,VCC=5V" / "STATUS:ALERTING,PIN=D8" / "STATUS:TESTING,PIN=D8"
 *     "TIMEOUT:AUTO_STOPPED,PIN=D8"
 * 
 * Note: Arduino Uno does not have built-in Wi-Fi. All communication is handled
 * via USB Serial over the virtual COM port (ATmega16U2 / CH340).
 * =====================================================================================
 */

#include <Arduino.h>

// Hardware Pin Configuration
const int BUZZER_PIN = 8;             // Active buzzer signal connected to Digital Pin D8
const int STATUS_LED = 13;            // Arduino Uno onboard LED (Pin 13 / LED_BUILTIN)

// Safety Timing Configurations
const unsigned long MAX_ALERT_DURATION_MS = 60000;  // 60 seconds maximum ringing before auto-stop
const unsigned long BEEP_ON_INTERVAL_MS   = 350;    // Beep duration in ALERT mode (ms)
const unsigned long BEEP_OFF_INTERVAL_MS  = 200;    // Pause duration in ALERT mode (ms)

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

// Testing Sequence Tracking (3 short beeps)
int testBeepCount = 0;
const int MAX_TEST_BEEPS = 3;
const unsigned long TEST_BEEP_DURATION_MS = 150;
const unsigned long TEST_PAUSE_DURATION_MS = 150;

// Serial Command Buffer
String inputBuffer = "";

void setBuzzer(bool state) {
  buzzerOutputLevel = state;
  digitalWrite(BUZZER_PIN, state ? HIGH : LOW);
  digitalWrite(STATUS_LED, state ? HIGH : LOW);
}

void startAlert() {
  currentState = STATE_ALERTING;
  alertStartTime = millis();
  lastToggleTime = millis();
  setBuzzer(true);
  Serial.println("OK:ALERT_STARTED,PIN=D8");
}

void stopBuzzer(const char* reason = "OK:STOPPED,PIN=D8") {
  currentState = STATE_IDLE;
  setBuzzer(false);
  Serial.println(reason);
}

void startTest() {
  currentState = STATE_TESTING;
  testBeepCount = 0;
  lastToggleTime = millis();
  setBuzzer(true);
  Serial.println("OK:TEST_STARTED,PIN=D8");
}

void processCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd.length() == 0) return;

  if (cmd == "ALERT") {
    startAlert();
  } else if (cmd == "SAFE" || cmd == "STOP") {
    stopBuzzer("OK:STOPPED,PIN=D8");
  } else if (cmd == "TEST") {
    startTest();
  } else if (cmd == "STATUS") {
    if (currentState == STATE_ALERTING) {
      Serial.println("STATUS:ALERTING,PIN=D8");
    } else if (currentState == STATE_TESTING) {
      Serial.println("STATUS:TESTING,PIN=D8");
    } else {
      Serial.println("STATUS:IDLE,PIN=D8,VCC=5V");
    }
  } else {
    Serial.print("ERROR:UNKNOWN_COMMAND:");
    Serial.println(cmd);
  }
}

void setup() {
  // Initialize Serial Communication at 115200 baud
  Serial.begin(115200);

  // Configure Digital Output Pins
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);

  // Ensure buzzer starts completely silent
  setBuzzer(false);

  // Allow power rail and ATmega328P clocks to stabilize
  delay(100);

  Serial.println("\n--- RingCare Arduino Uno (ATmega328P) Hardware Controller Initialized ---");
  Serial.println("READY:BUZZER_PIN_D8:115200:VCC_5V");
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
      // Prevent buffer overflow on 2KB SRAM of ATmega328P
      if (inputBuffer.length() < 32) {
        inputBuffer += inChar;
      }
    }
  }

  unsigned long currentMillis = millis();

  // 2. Non-blocking state machine for Alerting
  if (currentState == STATE_ALERTING) {
    // Check auto-stop safety timeout (60 seconds)
    if (currentMillis - alertStartTime >= MAX_ALERT_DURATION_MS) {
      stopBuzzer("TIMEOUT:AUTO_STOPPED,PIN=D8");
      return;
    }

    // Pulsing beeping pattern for high attention
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
          Serial.println("OK:TEST_COMPLETED,PIN=D8");
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
