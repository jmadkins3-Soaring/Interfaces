#pragma once

#include <Arduino.h>
#include <helpers/ESP32Board.h>

// Board class for Heltec WiFi LoRa 32 (V1).
//
// The V1 is a plain ESP32 (no Vext power-gate, no ADC_CTRL gate like the V3),
// so this is deliberately the *minimal* ESP32 board class: a battery read on
// the resistor-divider pin and a manufacturer string. It extends ESP32Board,
// which already provides BLE/WiFi bring-up, deep sleep and millis-based RTC
// fallback used elsewhere in MeshCore.

#ifndef PIN_VBAT_READ
  #define PIN_VBAT_READ 13
#endif
#ifndef ADC_MULTIPLIER
  #define ADC_MULTIPLIER 3.2
#endif

class HeltecV1Board : public ESP32Board {
public:
  uint16_t getBattMilliVolts() override {
    analogReadResolution(10);
    uint32_t raw = 0;
    for (int i = 0; i < 8; i++) {
      raw += analogRead(PIN_VBAT_READ);
    }
    raw = raw / 8;
    return (ADC_MULTIPLIER * (3.3 / 1024.0) * raw) * 1000;
  }

  const char* getManufacturerName() const override {
    return "Heltec V1";
  }
};
