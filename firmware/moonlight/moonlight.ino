/*
 * MOONLIGHT - Arduino Leonardo firmware
 * =====================================
 * HID input device driven over USB serial (115200).
 *
 * Wire protocol (see moonlight/input.py, ProtocolEncoder):
 *   K <combo>     press + release combo          -> OK
 *   D <combo>     hold combo down                -> OK
 *   U <combo>     release combo                  -> OK
 *   M <dx> <dy>   relative mouse move            -> OK
 *                 (each axis must be -4000..4000) -> ERR otherwise
 *   A <x> <y>     absolute mouse move            -> OK
 *                 (each axis 0..32767, maps to the screen the OS
 *                 assigns to absolute pointers)  -> ERR otherwise
 *   C L | C R     mouse click (left/right)       -> OK
 *   P             ping                           -> PONG
 *   X             release all inputs             -> OK
 *
 * Empty, malformed or unknown lines             -> ERR
 *
 * Hardware:
 *   - Kill switch on pin 2 (active LOW, pulled up): when LOW,
 *     immediately release all keys and mouse buttons.
 *   - Status LED on pin 13: solid on while idle, one short blink per
 *     valid command, fast blink on ERR.
 *   - Watchdog: if no commands arrive for 10 s, release all inputs.
 *
 * Key tokens (case-insensitive, '+' separated):
 *   ctrl, alt, shift, meta (win/cmd), a-z, 0-9, f1-f24,
 *   tab, enter, space, backspace, delete, insert, home, end,
 *   pgup, pgdn, up, down, left, right, esc
 */

#include <Keyboard.h>
#include <Mouse.h>
#include <HID.h>
#include <string.h>

// ------------------------------------------------------------ config -------

#define SERIAL_BAUD           115200UL

#define KILL_SWITCH_PIN       2     // active LOW (pressed = LOW)
#define STATUS_LED_PIN        13    // on-board LED

#define WATCHDOG_TIMEOUT_MS   10000UL  // release all after 10 s silence
#define MAX_MOUSE_DELTA       4000     // per-axis bound for M commands
#define HID_MOUSE_STEP        127      // per-axis bound for one HID report
#define ABS_MOUSE_MAX         32767    // per-axis bound for A commands
#define ABS_MOUSE_REPORT_ID   7        // Mouse.h uses 1, Keyboard.h uses 2

#define LINE_BUF_SIZE         96       // max characters per wire line
#define MAX_ARGS              8        // max tokens per command
#define MAX_HELD_KEYS         32       // max keys tracked for D/U

// USB HID usage IDs (HID Usage Tables, "Keyboard/Keypad Page" 0x07).
// Hard-coded here so the firmware is independent of which Keyboard library
// flavor (core or pluggable) is installed.
#define HID_LCTRL     0x80
#define HID_LSHIFT    0x81
#define HID_LALT      0x82
#define HID_LGUI      0x83
#define HID_UP        0xDA
#define HID_DOWN      0xD9
#define HID_LEFT      0xD8
#define HID_RIGHT     0xD7
#define HID_BKSP      0xB2
#define HID_TAB       0xB3
#define HID_RETURN    0xB0
#define HID_ESC       0xB1
#define HID_INSERT    0xD1
#define HID_DELETE    0xD4
#define HID_PGUP      0xD3
#define HID_PGDN      0xD6
#define HID_HOME      0xD2
#define HID_END       0xD5
#define HID_SPACE     ' '    // Keyboard.h takes ASCII below 0x80, not HID usage codes
#define HID_A         'a'    // 'a' .. 'z'
#define HID_1         '1'    // '1' .. '9'
#define HID_0         '0'
#define HID_F1        0xC2   // HID_F1 .. HID_F12 (0xC2..0xCD)
#define HID_F13       0xF0   // HID_F13 .. HID_F24 (0xF0..0xFB)

#define KEY_TAP_HOLD_MS       30UL     // K tap hold; 5 ms was too short for the client
#define MOUSE_CLICK_HOLD_MS   5UL      // min press duration for C clicks

#define CMD_BLINK_DURATION_MS 120UL    // LED pulse per valid command
#define ERR_BLINK_DURATION_MS 600UL    // LED fast-blink window on ERR
#define ERR_BLINK_PERIOD_MS   70UL     // half-period of the error blink

// ----------------------------------------------------- absolute mouse -----
// A second HID pointer with absolute X/Y (a "digitizer-like" mouse). The OS
// warps the cursor straight to the reported spot, no acceleration. Buttons
// stay on the stock relative Mouse, which clicks wherever the cursor is.

static const uint8_t absMouseDescriptor[] PROGMEM = {
  0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, ABS_MOUSE_REPORT_ID,
  0x09, 0x01, 0xA1, 0x00,
  0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01,
  0x95, 0x03, 0x75, 0x01, 0x81, 0x02,                 // 3 buttons (unused)
  0x95, 0x01, 0x75, 0x05, 0x81, 0x03,                 // padding
  0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
  0x16, 0x00, 0x00, 0x26, 0xFF, 0x7F,                 // 0..32767
  0x75, 0x10, 0x95, 0x02, 0x81, 0x02,                 // X, Y absolute
  0xC0, 0xC0
};

// Registered from a static constructor, like Mouse_, so the descriptor is
// in place before USB enumeration.
struct AbsMouseRegistrar {
  AbsMouseRegistrar() {
    static HIDSubDescriptor node(absMouseDescriptor, sizeof(absMouseDescriptor));
    HID().AppendDescriptor(&node);
  }
};
static AbsMouseRegistrar absMouseRegistrar;

static void absMouseMove(int x, int y) {
  uint8_t report[5] = {
    0, (uint8_t)(x & 0xFF), (uint8_t)(x >> 8), (uint8_t)(y & 0xFF), (uint8_t)(y >> 8)
  };
  HID().SendReport(ABS_MOUSE_REPORT_ID, report, sizeof(report));
}

// ------------------------------------------------------------ state --------

static uint32_t lastCommandMs = 0;    // watchdog clock
static char     lineBuf[LINE_BUF_SIZE];
static int      lineLen = 0;
static bool     lineOverflow = false; // line longer than buffer -> discard
static bool     skipNextLF = false;   // swallow LF right after CR

static uint8_t  heldKeys[MAX_HELD_KEYS];
static int      heldKeyCount = 0;

static uint32_t errBlinkUntil = 0;    // error blink active until this time
static uint32_t cmdBlinkUntil = 0;    // command pulse active until this time
static uint32_t ledNextToggle = 0;    // next toggle time (error blink)
static int      ledLevel = 1;         // 1 = ON, 0 = OFF (idle = solid ON)

// ------------------------------------------------------- small helpers -----

// True when `now` has reached/passed `until`, wraparound-safe (millis()).
static bool expired(uint32_t now, uint32_t until) {
  return (int32_t)(now - until) >= 0;
}

// Strict integer parse: optional sign, then digits only. Rejects anything
// Python's int() would reject (empty, garbage, trailing chars).
static bool parseIntStrict(const char* s, int& out) {
  if (!s || !*s) return false;
  bool neg = false;
  if (*s == '-')      { neg = true;  s++; }
  else if (*s == '+') {             s++; }
  if (!*s || *s < '0' || *s > '9') return false;
  long v = 0;
  while (*s) {
    if (*s < '0' || *s > '9') return false;
    v = v * 10 + (*s - '0');
    if (v > 100000L) return false; // overflow guard
    s++;
  }
  if (neg) v = -v;
  out = (int)v;
  return true;
}

// ------------------------------------------------------ held-key tracking --

static bool isHeld(uint8_t key) {
  for (int i = 0; i < heldKeyCount; i++) {
    if (heldKeys[i] == key) return true;
  }
  return false;
}

static void addHeld(uint8_t key) {
  if (!isHeld(key) && heldKeyCount < MAX_HELD_KEYS) {
    heldKeys[heldKeyCount++] = key;
  }
}

static void removeHeld(uint8_t key) {
  for (int i = 0; i < heldKeyCount; i++) {
    if (heldKeys[i] == key) {
      heldKeys[i] = heldKeys[--heldKeyCount];
      return;
    }
  }
}

// Release every tracked key and both mouse buttons (safety path).
static void releaseAll() {
  for (int i = 0; i < heldKeyCount; i++) {
    Keyboard.release(heldKeys[i]);
  }
  heldKeyCount = 0;
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
}

// ----------------------------------------------------------- key mapping ---

// Map one combo token to a Keyboard keycode, or -1 if unknown.
static int mapTokenToKey(const char* tok) {
  if (!tok || !*tok) return -1;

  size_t len = strlen(tok);
  if (len > 16) return -1;

  char lower[17];
  for (size_t i = 0; i < len; i++) {
    char c = tok[i];
    lower[i] = (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
  }
  lower[len] = '\0';

  // single letters and digits
  if (len == 1) {
    if (lower[0] >= 'a' && lower[0] <= 'z') return HID_A + (lower[0] - 'a');
    if (lower[0] >= '1' && lower[0] <= '9') return HID_1 + (lower[0] - '1');
    if (lower[0] == '0')                    return HID_0;
    return -1;
  }

  // F1..F24
  if (lower[0] == 'f') {
    int n = atoi(lower + 1);
    if (n >= 1 && n <= 12)  return HID_F1  + (n - 1);
    if (n >= 13 && n <= 24) return HID_F13 + (n - 13);
    return -1;
  }

  if (!strcmp(lower, "ctrl"))                              return HID_LCTRL;
  if (!strcmp(lower, "alt"))                               return HID_LALT;
  if (!strcmp(lower, "shift"))                             return HID_LSHIFT;
  if (!strcmp(lower, "meta") || !strcmp(lower, "win") ||
      !strcmp(lower, "cmd"))                               return HID_LGUI;
  if (!strcmp(lower, "tab"))                               return HID_TAB;
  if (!strcmp(lower, "enter") || !strcmp(lower, "return")) return HID_RETURN;
  if (!strcmp(lower, "space"))                             return HID_SPACE;
  if (!strcmp(lower, "backspace") || !strcmp(lower, "back"))
                                                          return HID_BKSP;
  if (!strcmp(lower, "delete") || !strcmp(lower, "del"))   return HID_DELETE;
  if (!strcmp(lower, "insert") || !strcmp(lower, "ins"))   return HID_INSERT;
  if (!strcmp(lower, "home"))                              return HID_HOME;
  if (!strcmp(lower, "end"))                               return HID_END;
  if (!strcmp(lower, "pgup"))                              return HID_PGUP;
  if (!strcmp(lower, "pgdn") || !strcmp(lower, "pgdown"))  return HID_PGDN;
  if (!strcmp(lower, "up"))                                return HID_UP;
  if (!strcmp(lower, "down"))                              return HID_DOWN;
  if (!strcmp(lower, "left"))                              return HID_LEFT;
  if (!strcmp(lower, "right"))                             return HID_RIGHT;
  if (!strcmp(lower, "esc") || !strcmp(lower, "escape"))   return HID_ESC;

  return -1;
}

// Apply (press or release) every '+'-separated key in tokens[start..end).
// Unknown tokens are ignored (the wire contract only requires OK on
// non-empty arguments).
static void applyCombo(char* const tokens[], int start, int end, bool press) {
  for (int i = start; i < end; i++) {
    char partBuf[LINE_BUF_SIZE];
    strncpy(partBuf, tokens[i], sizeof(partBuf) - 1);
    partBuf[sizeof(partBuf) - 1] = '\0';

    char* save = 0;
    for (char* part = strtok_r(partBuf, "+", &save);
         part != 0;
         part = strtok_r(0, "+", &save)) {
      int key = mapTokenToKey(part);
      if (key < 0) continue;

      if (press) {
        if (!isHeld((uint8_t)key)) {
          Keyboard.press((uint8_t)key);
          addHeld((uint8_t)key);
        }
      } else {
        if (isHeld((uint8_t)key)) {
          Keyboard.release((uint8_t)key);
          removeHeld((uint8_t)key);
        }
      }
    }
  }
}

// Tap combo: press every key (alt+q, shift+f1: all down together), hold
// KEY_TAP_HOLD_MS, release in reverse. Keys that were already held (via D)
// are NOT released by the tap - only keys pressed by this command are.
static void tapCombo(char* const tokens[], int start, int end) {
  uint8_t pressed[MAX_HELD_KEYS];
  int count = 0;
  for (int i = start; i < end; i++) {
    char partBuf[LINE_BUF_SIZE];
    strncpy(partBuf, tokens[i], sizeof(partBuf) - 1);
    partBuf[sizeof(partBuf) - 1] = '\0';

    char* save = 0;
    for (char* part = strtok_r(partBuf, "+", &save);
         part != 0;
         part = strtok_r(0, "+", &save)) {
      int key = mapTokenToKey(part);
      if (key < 0) continue;

      uint8_t k = (uint8_t)key;
      if (!isHeld(k) && count < MAX_HELD_KEYS) {
        Keyboard.press(k);
        addHeld(k);
        pressed[count++] = k;
      }
    }
  }
  delay(KEY_TAP_HOLD_MS);
  while (count > 0) {
    uint8_t k = pressed[--count];
    Keyboard.release(k);
    removeHeld(k);
  }
}

static void mouseClick(char side) {
  uint8_t button = (side == 'L') ? MOUSE_LEFT : MOUSE_RIGHT;
  Mouse.press(button);
  delay(MOUSE_CLICK_HOLD_MS);
  Mouse.release(button);
}

// ---------------------------------------------------------- LED handling ---

static void signalCommandLed() {
  cmdBlinkUntil = millis() + CMD_BLINK_DURATION_MS;
}

static void signalErrorLed() {
  errBlinkUntil = millis() + ERR_BLINK_DURATION_MS;
  ledNextToggle = 0; // toggle immediately on the next pass
}

// Solid ON when idle; one short OFF pulse per valid command; fast blink
// during the error window.
static void updateLed() {
  uint32_t now = millis();
  int desired;

  if (!expired(now, errBlinkUntil)) {
    if (ledNextToggle == 0 || !expired(now, ledNextToggle)) {
      ledNextToggle = 0;
    } else {
      ledLevel = !ledLevel;
      ledNextToggle = now + ERR_BLINK_PERIOD_MS;
    }
    desired = ledLevel;
  } else if (!expired(now, cmdBlinkUntil)) {
    // OFF for the first half of the pulse, ON for the second
    desired = (now < cmdBlinkUntil - CMD_BLINK_DURATION_MS / 2) ? 0 : 1;
  } else {
    desired = 1; // idle: solid on
  }

  if (desired != ledLevel) {
    ledLevel = desired;
    digitalWrite(STATUS_LED_PIN, ledLevel ? HIGH : LOW);
  }
}

// ------------------------------------------------------ command handling ---

// Split `line` on whitespace in place; returns the token count (capped at
// maxTokens, excess tokens are scanned but not stored).
static int tokenize(char* line, char** tokens, int maxTokens) {
  int n = 0;
  char* p = line;
  while (*p) {
    while (*p == ' ' || *p == '\t') p++;
    if (!*p) break;
    if (n < maxTokens) {
      tokens[n++] = p;
    }
    while (*p && *p != ' ' && *p != '\t') p++;
    if (*p) *p++ = '\0';
  }
  return n;
}

// Process one complete wire line: act on it and send exactly one reply.
static void processLine(char* line) {
  char* tokens[MAX_ARGS];
  int n = tokenize(line, tokens, MAX_ARGS);

  bool ok = false;
  const char* replyStr = "ERR";

  if (n >= 1 && tokens[0][1] == '\0') { // command is a single letter
    char cmd = tokens[0][0];

    switch (cmd) {
      case 'K': // tap combo
      case 'D': // hold combo
      case 'U': // release combo
        if (n >= 2) {
          if (cmd == 'K')      tapCombo(tokens, 1, n);
          else if (cmd == 'D') applyCombo(tokens, 1, n, true);
          else                 applyCombo(tokens, 1, n, false);
          ok = true;
        }
        break;

      case 'M': { // relative mouse move, split into HID-sized reports
        if (n == 3) {
          int dx = 0, dy = 0;
          if (parseIntStrict(tokens[1], dx) &&
              parseIntStrict(tokens[2], dy) &&
              dx >= -MAX_MOUSE_DELTA && dx <= MAX_MOUSE_DELTA &&
              dy >= -MAX_MOUSE_DELTA && dy <= MAX_MOUSE_DELTA) {
            while (dx != 0 || dy != 0) {
              int sx = constrain(dx, -HID_MOUSE_STEP, HID_MOUSE_STEP);
              int sy = constrain(dy, -HID_MOUSE_STEP, HID_MOUSE_STEP);
              Mouse.move(sx, sy, 0);
              dx -= sx;
              dy -= sy;
            }
            ok = true;
          }
        }
        break;
      }

      case 'A': { // absolute mouse move
        if (n == 3) {
          int x = 0, y = 0;
          if (parseIntStrict(tokens[1], x) && parseIntStrict(tokens[2], y) &&
              x >= 0 && x <= ABS_MOUSE_MAX && y >= 0 && y <= ABS_MOUSE_MAX) {
            absMouseMove(x, y);
            ok = true;
          }
        }
        break;
      }

      case 'C': { // mouse click
        if (n == 2 && strlen(tokens[1]) == 1) {
          char side = tokens[1][0];
          if (side == 'L' || side == 'R') {
            mouseClick(side);
            ok = true;
          }
        }
        break;
      }

      case 'P': // ping
        if (n == 1) {
          replyStr = "PONG";
          ok = true;
        }
        break;

      case 'X': // release all
        if (n == 1) {
          releaseAll();
          ok = true;
        }
        break;

      default:
        break; // unknown command -> ERR
    }
  }

  if (ok && replyStr[0] != 'P') replyStr = "OK";
  Serial.println(replyStr);

  // Any line counts as host activity for the watchdog.
  lastCommandMs = millis();
  if (ok) signalCommandLed();
  else    signalErrorLed();
}

static void replyErrEmpty() {
  Serial.println("ERR");
  lastCommandMs = millis();
  signalErrorLed();
}

// ------------------------------------------------------ serial line reader -

static void handleSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\r' || c == '\n') {
      bool isPairLF = (c == '\n' && skipNextLF);
      skipNextLF = (c == '\r'); // a LF right after CR is part of the same end-of-line
      if (isPairLF) continue;

      if (lineOverflow) {
        lineOverflow = false;
        lineLen = 0;
        replyErrEmpty(); // line too long
      } else if (lineLen > 0) {
        lineBuf[lineLen] = '\0';
        processLine(lineBuf);
        lineLen = 0;
      } else {
        replyErrEmpty(); // empty line -> ERR per wire contract
      }
    } else if (!lineOverflow) {
      if (lineLen < LINE_BUF_SIZE - 1) {
        lineBuf[lineLen++] = c;
      } else {
        lineOverflow = true; // keep draining until newline, then ERR
      }
    }
  }
}

// ---------------------------------------------------------- safety checks --

static void checkKillSwitch() {
  if (digitalRead(KILL_SWITCH_PIN) == LOW) {
    releaseAll();
    lastCommandMs = millis(); // inputs are released; refresh watchdog clock
  }
}

static void checkWatchdog() {
  uint32_t now = millis();
  if (!expired(now, lastCommandMs + WATCHDOG_TIMEOUT_MS)) {
    return;
  }
  releaseAll();
  lastCommandMs = now; // release once, then wait for fresh commands
}

// ---------------------------------------------------------- Arduino setup -

void setup() {
  Serial.begin(SERIAL_BAUD);
  Keyboard.begin();
  Mouse.begin();

  pinMode(KILL_SWITCH_PIN, INPUT_PULLUP);
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH); // idle: solid on

  releaseAll(); // start from a clean HID state
  lastCommandMs = millis();

  delay(300); // give the USB stack time to enumerate
}

void loop() {
  handleSerial();
  checkKillSwitch();
  checkWatchdog();
  updateLed();
  delay(1); // ~1 kHz poll rate; plenty for this protocol
}
