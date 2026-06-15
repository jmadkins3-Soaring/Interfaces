#include <Arduino.h>
#include "target.h"

// MeshCore target implementation TEMPLATE.
//
// Radio Module constructor depends on the transceiver:
//   SX126x / LLCC68 / LR11x0 : new Module(NSS, DIO1, RESET, BUSY, spi)
//   SX127x  / RFM95          : new Module(NSS, DIO0, RESET, DIO1, spi)
// Keep the line that matches your -D USE_<RADIO> selection and delete the other.

<BoardName>Board board;

#if defined(P_LORA_SCLK)
  static SPIClass spi;
  // SX126x / LLCC68 / LR11x0:
  RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, spi);
  // SX127x / RFM95 (use instead):
  // RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_0, P_LORA_RESET, P_LORA_DIO_1, spi);
#else
  RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
#endif

WRAPPER_CLASS radio_driver(radio, board);

// ESP32 targets use ESP32RTCClock; nRF52 targets typically use VolatileRTCClock
// or a board RTC. Match the base your variant extends.
ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
EnvironmentSensorManager sensors;

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);

#if defined(P_LORA_SCLK)
  return radio.std_init(&spi);
#else
  return radio.std_init();
#endif
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}
