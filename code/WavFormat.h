// SPDX-License-Identifier: MIT
// Small, hardware-independent PCM helpers shared with host regression tests.
#ifndef SONGBEAM_WAV_FORMAT_H
#define SONGBEAM_WAV_FORMAT_H

#include <stdint.h>
#include <string.h>

namespace songbeam {
inline void put16(uint8_t* out, unsigned offset, uint16_t value) {
  out[offset] = value & 0xff;
  out[offset + 1] = (value >> 8) & 0xff;
}

inline void put32(uint8_t* out, unsigned offset, uint32_t value) {
  for (unsigned i = 0; i < 4; ++i) out[offset + i] = (value >> (8 * i)) & 0xff;
}

inline void wavHeader(uint8_t out[44], uint32_t dataBytes,
                      uint32_t sampleRate, uint16_t channels) {
  memset(out, 0, 44);
  memcpy(out, "RIFF", 4);
  put32(out, 4, dataBytes + 36);
  memcpy(out + 8, "WAVEfmt ", 8);
  put32(out, 16, 16);
  put16(out, 20, 1);
  put16(out, 22, channels);
  put32(out, 24, sampleRate);
  put32(out, 28, sampleRate * channels * 2);
  put16(out, 32, channels * 2);
  put16(out, 34, 16);
  memcpy(out + 36, "data", 4);
  put32(out, 40, dataBytes);
}

inline void interleave128(const uint8_t* const in[4], uint16_t channels,
                          uint8_t out[1024]) {
  for (unsigned frame = 0; frame < 128; ++frame) {
    for (unsigned channel = 0; channel < channels; ++channel) {
      unsigned src = frame * 2;
      unsigned dst = (frame * channels + channel) * 2;
      out[dst] = in[channel][src];
      out[dst + 1] = in[channel][src + 1];
    }
  }
}
}  // namespace songbeam
#endif
