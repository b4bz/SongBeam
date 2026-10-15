// SPDX-License-Identifier: MIT
#ifndef SONGBEAM_USB_FRAME_H
#define SONGBEAM_USB_FRAME_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "WavFormat.h"

namespace songbeam {

constexpr size_t kUsbFrameHeaderBytes = 24;
constexpr size_t kUsbFramePayloadBytes = 128 * 4 * 2;
constexpr uint8_t kUsbFrameVersion = 1;
constexpr uint8_t kUsbFrameAudio = 0;
constexpr uint8_t kUsbFrameEnd = 1;

inline uint32_t crc32(const uint8_t* data, size_t size) {
  uint32_t crc = 0xffffffffU;
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (unsigned bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1U)));
    }
  }
  return ~crc;
}

inline void usbFrameHeader(uint8_t out[kUsbFrameHeaderBytes], uint8_t kind,
                           uint16_t frames, uint32_t sequence,
                           uint32_t captureMillis, uint32_t droppedBlocks,
                           uint32_t payloadCrc) {
  memcpy(out, "SB4C", 4);
  out[4] = kUsbFrameVersion;
  out[5] = kind;
  put16(out, 6, frames);
  put32(out, 8, sequence);
  put32(out, 12, captureMillis);
  put32(out, 16, droppedBlocks);
  put32(out, 20, payloadCrc);
}

}  // namespace songbeam
#endif
