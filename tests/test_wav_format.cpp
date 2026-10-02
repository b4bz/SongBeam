#include "WavFormat.h"
#include <cassert>
#include <cstdint>
#include <cstring>

static uint32_t le32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) |
         (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

int main() {
  uint8_t header[44];
  songbeam::wavHeader(header, 1024, 44100, 4);
  assert(std::memcmp(header, "RIFF", 4) == 0);
  assert(std::memcmp(header + 8, "WAVEfmt ", 8) == 0);
  assert(le32(header + 4) == 1060);
  assert(le32(header + 24) == 44100);
  assert(le32(header + 28) == 352800);
  assert(header[22] == 4 && header[32] == 8);
  assert(le32(header + 40) == 1024);
  uint8_t channels[4][256] = {};
  for (unsigned frame = 0; frame < 128; ++frame) {
    for (unsigned ch = 0; ch < 4; ++ch) {
      uint16_t value = 1000 * ch + frame;
      channels[ch][frame * 2] = value & 0xff;
      channels[ch][frame * 2 + 1] = value >> 8;
    }
  }
  const uint8_t* inputs[4] = {channels[0], channels[1], channels[2], channels[3]};
  uint8_t output[1024] = {};
  songbeam::interleave128(inputs, 4, output);
  for (unsigned frame = 0; frame < 128; ++frame) {
    for (unsigned ch = 0; ch < 4; ++ch) {
      unsigned offset = 8 * frame + 2 * ch;
      uint16_t actual = output[offset] | (uint16_t(output[offset + 1]) << 8);
      assert(actual == 1000 * ch + frame);
    }
  }
  songbeam::wavHeader(header, 512, 44100, 2);
  assert(header[22] == 2 && header[32] == 4);
  assert(le32(header + 40) == 512);
  return 0;
}
