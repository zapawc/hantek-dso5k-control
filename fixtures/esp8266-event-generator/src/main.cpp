#include <Arduino.h>
#include <ESP8266WiFi.h>

namespace {
constexpr uint8_t channel1Pin = 14;
constexpr uint8_t channel2Pin = 12;
constexpr uint8_t triggerPin = 13;
constexpr uint32_t channel1 = 1U << channel1Pin;
constexpr uint32_t channel2 = 1U << channel2Pin;
constexpr uint32_t channels = channel1 | channel2;
constexpr uint32_t trigger = 1U << triggerPin;
constexpr uint32_t outputs = channels | trigger;
constexpr uint16_t slotUs = 1000;
constexpr uint16_t ticksPerUs = 5;  // 80 MHz APB clock divided by 16.
constexpr uint8_t eventBits = 16;
constexpr uint32_t diagnosticGapUs = 22000;
constexpr uint16_t diagnosticRepetitions = 150;

struct Step {
    uint32_t set;
    uint32_t clear;
    uint16_t durationUs;
};

Step steps[5 + 2 * eventBits];
volatile uint8_t nextStep = 0;
volatile bool active = false;
volatile uint16_t completedId = 0;
volatile uint16_t activeId = 0;
volatile uint16_t repetitionsLeft = 0;
volatile bool diagnostic = false;
uint16_t lastAcceptedId = 0;

void IRAM_ATTR advanceEvent() {
    if (!active) {
        return;
    }
    if (nextStep == sizeof(steps) / sizeof(steps[0])) {
        GPOC = outputs;
        if (repetitionsLeft > 1) {
            --repetitionsLeft;
            nextStep = 0;
            timer1_write(diagnosticGapUs * ticksPerUs);
            return;
        }
        if (!diagnostic) {
            completedId = activeId;
        }
        active = false;
        return;
    }

    const Step& step = steps[nextStep++];
    GPOC = step.clear;
    GPOS = step.set;
    timer1_write(step.durationUs * ticksPerUs);
}

void startEvent(uint16_t id, bool repeat) {
    steps[0] = {trigger, 0, 500};
    steps[1] = {0, trigger, 500};
    steps[2] = {channels, 0, 600};
    steps[3] = {0, channel1, 200};
    steps[4] = {0, channel2, 200};
    unsigned next = 5;
    for (int bit = eventBits - 1; bit >= 0; --bit) {
        const uint16_t highUs = id & (1U << bit) ? 400 : 200;
        steps[next++] = {channels, 0, highUs};
        steps[next++] = {0, channels, static_cast<uint16_t>(slotUs - highUs)};
    }

    lastAcceptedId = id;
    noInterrupts();
    activeId = id;
    repetitionsLeft = repeat ? diagnosticRepetitions : 1;
    diagnostic = repeat;
    nextStep = 0;
    active = true;
    timer1_enable(TIM_DIV16, TIM_EDGE, TIM_SINGLE);
    advanceEvent();
    interrupts();
}

void stopEvent() {
    noInterrupts();
    timer1_disable();
    GPOC = outputs;
    active = false;
    interrupts();
}

void reportStatus(const char* command) {
    noInterrupts();
    const bool running = active;
    const auto completed = completedId;
    const auto levels = GPI;
    interrupts();
    Serial.printf(
        "{\"command\":\"%s\",\"ok\":true,\"active\":%s,\"last_accepted_id\":%u,"
        "\"last_completed_id\":%u,\"high_mask\":%lu}\n",
        command, running ? "true" : "false", static_cast<unsigned>(lastAcceptedId),
        static_cast<unsigned>(completed), static_cast<unsigned long>(levels & outputs));
}

void reportError(const char* error) {
    Serial.printf("{\"ok\":false,\"error\":\"%s\"}\n", error);
}

uint16_t parseId(const char* digits) {
    uint32_t id = 0;
    for (; *digits; ++digits) {
        if (*digits < '0' || *digits > '9') {
            return 0;
        }
        id = id * 10 + (*digits - '0');
        if (id > 65535) {
            return 0;
        }
    }
    return static_cast<uint16_t>(id);
}

void handleCommand(const char* line) {
    if (strcmp(line, "INFO") == 0) {
        Serial.println(F("{\"command\":\"INFO\",\"ok\":true,\"firmware\":\"acq12-esp8266\","
                         "\"version\":\"0.2.0\",\"protocol\":1}"));
    } else if (strcmp(line, "STATUS") == 0) {
        reportStatus("STATUS");
    } else if (strcmp(line, "IDLE") == 0 || strcmp(line, "STOP") == 0) {
        stopEvent();
        reportStatus(line);
    } else if (strncmp(line, "EVENT ", 6) == 0 || strncmp(line, "PATTERN ", 8) == 0) {
        const bool repeat = line[0] == 'P';
        const auto id = parseId(line + (repeat ? 8 : 6));
        if (id == 0) {
            reportError("invalid_id");
        } else if (id <= lastAcceptedId) {
            reportError("stale_id");
        } else if (active) {
            reportError("busy");
        } else {
            startEvent(id, repeat);
            Serial.printf("{\"command\":\"%s\",\"ok\":true,\"event_id\":%u}\n",
                          repeat ? "PATTERN" : "EVENT", static_cast<unsigned>(id));
        }
    } else {
        reportError("invalid_command");
    }
}
}  // namespace

void setup() {
    GPOC = outputs;
    pinMode(channel1Pin, OUTPUT);
    pinMode(channel2Pin, OUTPUT);
    pinMode(triggerPin, OUTPUT);
    timer1_attachInterrupt(advanceEvent);
    stopEvent();
    WiFi.persistent(false);
    WiFi.mode(WIFI_OFF);
    Serial.begin(115200);
}

void loop() {
    static char line[24];
    static uint8_t length = 0;
    static bool invalid = false;
    for (uint8_t received = 0; received < 32 && Serial.available(); ++received) {
        const int value = Serial.read();
        if (value == '\n') {
            if (invalid) {
                reportError("invalid_line");
            } else {
                if (length && line[length - 1] == '\r') {
                    --length;
                }
                line[length] = '\0';
                if (length) {
                    handleCommand(line);
                }
            }
            length = 0;
            invalid = false;
        } else if ((value >= 32 && value <= 126) || value == '\r') {
            if (length < sizeof(line) - 1) {
                line[length++] = static_cast<char>(value);
            } else {
                invalid = true;
            }
        } else {
            invalid = true;
        }
    }
}
