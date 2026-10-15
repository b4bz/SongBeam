#include "UsbFrame.h"
#include <cassert>
#include <cstdint>
#include <cstring>

static uint32_t read32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) |
         (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

int main() {
  const uint8_t known[] = "123456789";
  assert(songbeam::crc32(known, 9) == 0xcbf43926U);

  uint8_t payload[songbeam::kUsbFramePayloadBytes];
  for (size_t i = 0; i < sizeof(payload); ++i) payload[i] = uint8_t(i);
  uint8_t header[songbeam::kUsbFrameHeaderBytes];
  songbeam::usbFrameHeader(header, songbeam::kUsbFrameAudio, 128,
                           0x12345678U, 60000, 3,
                           songbeam::crc32(payload, sizeof(payload)));
  assert(std::memcmp(header, "SB4C", 4) == 0);
  assert(header[4] == 1 && header[5] == songbeam::kUsbFrameAudio);
  assert(header[6] == 128 && header[7] == 0);
  assert(read32(header + 8) == 0x12345678U);
  assert(read32(header + 12) == 60000);
  assert(read32(header + 16) == 3);
  assert(read32(header + 20) == songbeam::crc32(payload, sizeof(payload)));
  return 0;
}
