// Etch VOICE ARM: Raspberry Pi Pico hears a keyword on the MAX4466 mic and moves an SO-101 arm.
// UNTESTED ON HARDWARE. Read ../README.md before powering the arm.
//
// Board nets (from the Etch schematic):
//   GP0  (pin 1)  SERVO_TX   UART0 TX into 74HC126 gate 1
//   GP1  (pin 2)  SERVO_RX   UART0 RX from 74HC126 gate 2
//   GP2  (pin 4)  TX_EN      gate 1 enable (high = Pico drives the bus)
//   GP3  (pin 5)  RX_EN      gate 2 enable (high = bus goes to the Pico)
//   GP15 (pin 20) LED_LISTEN D1, lit while a keyword is being heard
//   GP26 (pin 31) MIC_OUT    MAX4466 output, ADC0
//
// Core: earlephilhower/arduino-pico. Libraries: FTServo (lib/FTServo, vendored) and the Edge Impulse
// Arduino library you export from your own keyword project (see README, the model is not included).

#include <SCServo.h>

// The Edge Impulse keyword library is not in this download (you train and export it, see README step 3).
// Until it is installed the sketch still builds: a STUB below makes the arm answer keywords typed over USB
// serial (punch, jab, wave, rest at 115200 baud), so the board, bus and poses can be tested first.
#if __has_include(<etch_voice_arm_inferencing.h>)
#include <etch_voice_arm_inferencing.h>  // name of the exported Edge Impulse Arduino library
#define ETCH_HAVE_MODEL 1
#else
#define ETCH_HAVE_MODEL 0  // STUB: no keyword model yet, keywords come from the serial monitor
#endif

static const uint8_t PIN_TX_EN = 2, PIN_RX_EN = 3, PIN_LED = 15, PIN_MIC = 26;
static const float MIN_CONFIDENCE = 0.80f;

// The 74HC126 hand-off: Feetech's SCS layer calls rFlushSCS() before every packet it sends and
// wFlushSCS() after it, so those two hooks are where the bus direction flips.
class HalfDuplexSTS : public SMS_STS {
 protected:
  void rFlushSCS() override {
    digitalWrite(PIN_RX_EN, LOW);
    digitalWrite(PIN_TX_EN, HIGH);
    SMS_STS::rFlushSCS();
  }
  void wFlushSCS() override {
    pSerial->flush();  // wait for the last stop bit before letting go of the bus
    digitalWrite(PIN_TX_EN, LOW);
    digitalWrite(PIN_RX_EN, HIGH);
  }
};

HalfDuplexSTS bus;

// SO-101 servo IDs 1..6: shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper.
// Positions are raw STS3215 ticks (0..4095, 2048 = centre). PLACEHOLDERS: calibrate them on your arm
// (LeRobot's calibration gives the real centre of each joint) before you run the punch at full speed.
static u8 IDS[6] = {1, 2, 3, 4, 5, 6};
struct Pose { const char *name; s16 pos[6]; u16 speed; };
static const Pose POSES[] = {
  {"rest",  {2048, 1100, 3000, 2600, 2048, 2048}, 600},
  {"jab",   {2048, 1700, 2300, 2048, 2048, 1700}, 2400},
  {"punch", {2048, 2000, 1900, 2048, 2048, 1700}, 3000},
  {"wave",  {2048, 1500, 2100, 1700, 2048, 2048}, 1500},
};

static void move_to(const Pose &p) {
  s16 pos[6]; u16 spd[6]; u8 acc[6];
  for (int i = 0; i < 6; i++) { pos[i] = p.pos[i]; spd[i] = p.speed; acc[i] = 50; }
  bus.SyncWritePosEx(IDS, 6, pos, spd, acc);
}

static const Pose *pose_named(const char *n) {
  for (auto &p : POSES) if (strcmp(p.name, n) == 0) return &p;
  return nullptr;
}

static void do_keyword(const char *kw) {
  const Pose *p = pose_named(kw);
  if (!p) return;
  move_to(*p);
  if (strcmp(kw, "wave") == 0) {  // swing the wrist a few times, then settle
    Pose w = *p;
    for (int i = 0; i < 3; i++) {
      w.pos[3] = 1500; move_to(w); delay(350);
      w.pos[3] = 1900; move_to(w); delay(350);
    }
  } else if (strcmp(kw, "rest") != 0) {
    delay(600);
    move_to(POSES[0]);
  }
}

#if ETCH_HAVE_MODEL
// One second of 16 kHz audio from the MAX4466, centred and scaled to roughly -1..1.
static float audio[EI_CLASSIFIER_RAW_SAMPLE_COUNT];

static void record_window() {
  const uint32_t period_us = 1000000UL / EI_CLASSIFIER_FREQUENCY;
  uint32_t t = micros();
  for (size_t i = 0; i < EI_CLASSIFIER_RAW_SAMPLE_COUNT; i++) {
    while ((uint32_t)(micros() - t) < period_us) {}
    t += period_us;
    audio[i] = (analogRead(PIN_MIC) - 2048) / 2048.0f;  // 12-bit ADC, mic biased at VCC/2
  }
}

static int get_audio(size_t offset, size_t length, float *out) {
  memcpy(out, audio + offset, length * sizeof(float));
  return 0;
}
#endif

void setup() {
  pinMode(PIN_TX_EN, OUTPUT);
  pinMode(PIN_RX_EN, OUTPUT);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_TX_EN, LOW);
  digitalWrite(PIN_RX_EN, HIGH);
  analogReadResolution(12);
  Serial.begin(115200);
  Serial1.setTX(0);
  Serial1.setRX(1);
  Serial1.begin(1000000);  // STS3215 default baud
  bus.pSerial = &Serial1;
  delay(500);
  move_to(POSES[0]);
}

#if !ETCH_HAVE_MODEL
// STUB loop: type a keyword and press enter in the serial monitor. Replaced by the model loop below once the
// Edge Impulse library is installed.
void loop() {
  static char word[16];
  static size_t n = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      word[n] = 0;
      if (n && pose_named(word)) {
        Serial.print("keyword (typed, no model yet): ");
        Serial.println(word);
        digitalWrite(PIN_LED, HIGH);
        do_keyword(word);
        digitalWrite(PIN_LED, LOW);
      } else if (n) {
        Serial.println("type punch, jab, wave or rest");
      }
      n = 0;
    } else if (n < sizeof(word) - 1) {
      word[n++] = c;
    }
  }
}
#else
void loop() {
  record_window();
  signal_t sig;
  sig.total_length = EI_CLASSIFIER_RAW_SAMPLE_COUNT;
  sig.get_data = &get_audio;
  ei_impulse_result_t res = {0};
  if (run_classifier(&sig, &res, false) != EI_IMPULSE_OK) return;

  const char *best = nullptr;
  float best_v = 0;
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    if (res.classification[i].value > best_v) {
      best_v = res.classification[i].value;
      best = res.classification[i].label;
    }
  }
  if (best && best_v >= MIN_CONFIDENCE && pose_named(best)) {
    digitalWrite(PIN_LED, HIGH);
    do_keyword(best);
    digitalWrite(PIN_LED, LOW);
  }
}
#endif
