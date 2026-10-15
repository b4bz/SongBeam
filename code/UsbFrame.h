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
constexpr size_t kUsbStereoPayloadBytes = 128 * 2 * 2;
constexpr uint8_t kUsbFrameVersion = 1;
constexpr uint8_t kUsbFrameAudio = 0;
constexpr uint8_t kUsbFrameEnd = 1;
constexpr uint8_t kUsbFrameStereo = 2;

// Preview stereo: average raw channels 0+1 and 2+3. This preserves headroom,
// but is not delay-aligned beamforming and may color off-axis sound.
inline void stereoPairSum128(const uint8_t in[kUsbFramePayloadBytes],
                             uint8_t out[kUsbStereoPayloadBytes]) {
  for (unsigned frame = 0; frame < 128; ++frame) {
    const unsigned src = frame * 8;
    const auto sample = [&](unsigned ch) {
      const uint16_t bits = uint16_t(in[src + ch * 2]) |
                            (uint16_t(in[src + ch * 2 + 1]) << 8);
      return int32_t(int16_t(bits));
    };
    const int16_t left = int16_t((sample(0) + sample(1)) / 2);
    const int16_t right = int16_t((sample(2) + sample(3)) / 2);
    put16(out, frame * 4, uint16_t(left));
    put16(out, frame * 4 + 2, uint16_t(right));
  }
}

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
