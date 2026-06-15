#pragma once

#include <Arduino.h>
// ESP32 family:
#include <helpers/ESP32Board.h>
// nRF52 family (use instead):
// #include <helpers/NRF52Board.h>

// Board class TEMPLATE — rename file to <BoardName>Board.h and the class to
// <BoardName>Board. Extend ESP32Board or NRF52Board to inherit BLE/WiFi,
// deep-sleep and RTC plumbing. Override only what is board-specific.

#ifndef PIN_VBAT_READ
  #define PIN_VBAT_READ TODO
#endif
#ifndef ADC_MULTIPLIER
  #define ADC_MULTIPLIER TODO
#endif

class <BoardName>Board : public ESP32Board {   // or : public NRF52Board
public:
  // Optional: gate peripheral power, configure ADC, handle wake source here.
  // void begin() override { ESP32Board::begin(); /* board init */ }

  uint16_t getBattMilliVolts() override {
    analogReadResolution(10);
    uint32_t raw = 0;
    for (int i = 0; i < 8; i++) raw += analogRead(PIN_VBAT_READ);
    raw = raw / 8;
    return (ADC_MULTIPLIER * (3.3 / 1024.0) * raw) * 1000;
  }

  const char* getManufacturerName() const override {
    return "<BoardName>";
  }
};
