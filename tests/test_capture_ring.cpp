#include "CaptureRing.h"
#include <array>
#include <cassert>
#include <cstdint>

int main() {
  std::array<uint8_t, 3 * 1024> storage{};
  songbeam::CaptureRing ring(storage.data(), storage.size());
  ring.reset(4);
  assert(ring.capacity() == 2);
  uint8_t input[4][256] = {};
  for (unsigned ch = 0; ch < 4; ++ch)
    for (unsigned frame = 0; frame < 128; ++frame)
      input[ch][frame * 2] = uint8_t(ch * 10 + frame % 10);
  const uint8_t* channels[4] = {input[0], input[1], input[2], input[3]};
  assert(ring.push(channels));
  assert(ring.push(channels));
  assert(!ring.push(channels));
  assert(ring.droppedBlocks() == 1);
  assert(ring.available() == 2);
  assert(ring.contiguous(8) == 2);
  assert(ring.front()[0] == 0 && ring.front()[2] == 10 &&
         ring.front()[4] == 20 && ring.front()[6] == 30);
  ring.consume(1);
  assert(ring.contiguous(8) == 1);
  assert(ring.push(channels));  // head wraps without overwriting unread data
  assert(ring.available() == 2);
  ring.consume(1);
  assert(ring.contiguous(8) == 1);
  ring.consume(1);
  assert(ring.available() == 0);
  ring.reset(2);
  assert(ring.capacity() == 5);
  assert(ring.blockBytes() == 512);

  // The physical card stalled for 587 ms. At 44.1 kHz, 203 complete
  // 128-frame blocks can arrive during that pause. The RAM2 ring must hold
  // them without dropping any channel; a longer pause must fail honestly.
  static std::array<uint8_t, 384 * 1024> fieldBuffer{};
  songbeam::CaptureRing fieldRing(fieldBuffer.data(), fieldBuffer.size());
  fieldRing.reset(4);
  for (unsigned i = 0; i < 203; ++i) assert(fieldRing.push(channels));
  assert(fieldRing.droppedBlocks() == 0);
  assert(fieldRing.available() == 203);
  for (unsigned i = 203; i < fieldRing.capacity(); ++i)
    assert(fieldRing.push(channels));
  assert(!fieldRing.push(channels));
  assert(fieldRing.droppedBlocks() == 1);
  return 0;
}
