// SPDX-License-Identifier: MIT
// Single-producer (audio interrupt), single-consumer (SD/USB loop) PCM ring.
#ifndef SONGBEAM_CAPTURE_RING_H
#define SONGBEAM_CAPTURE_RING_H

#include <stddef.h>
#include <stdint.h>

#include "WavFormat.h"

namespace songbeam {

class CaptureRing {
 public:
  CaptureRing(uint8_t* storage, uint32_t storageBytes)
      : storage_(storage), storageBytes_(storageBytes) {}

  void reset(uint16_t channels) {
    channels_ = channels;
    blockBytes_ = 128U * channels * 2U;
    capacity_ = storageBytes_ / blockBytes_;
    head_ = tail_ = 0;
    droppedBlocks_ = 0;
  }

  bool push(const uint8_t* const inputs[4]) {
    if (capacity_ < 2) return false;
    uint32_t next = head_ + 1;
    if (next == capacity_) next = 0;
    if (next == tail_) {
      ++droppedBlocks_;
      return false;
    }
    interleave128(inputs, channels_, storage_ + head_ * blockBytes_);
    __sync_synchronize();
    head_ = next;
    return true;
  }

  uint32_t available() const {
    const uint32_t head = head_, tail = tail_;
    return head >= tail ? head - tail : capacity_ - tail + head;
  }

  uint32_t contiguous(uint32_t limit) const {
    uint32_t count = available();
    if (count > capacity_ - tail_) count = capacity_ - tail_;
    return count < limit ? count : limit;
  }

  const uint8_t* front() const { return storage_ + tail_ * blockBytes_; }

  void consume(uint32_t count) {
    if (count > available()) count = available();
    uint32_t next = tail_ + count;
    if (next >= capacity_) next -= capacity_;
    __sync_synchronize();
    tail_ = next;
  }

  uint32_t droppedBlocks() const { return droppedBlocks_; }
  uint32_t capacity() const { return capacity_ > 0 ? capacity_ - 1 : 0; }
  uint32_t blockBytes() const { return blockBytes_; }

 private:
  uint8_t* storage_;
  uint32_t storageBytes_;
  uint32_t capacity_ = 0;
  uint32_t blockBytes_ = 0;
  uint16_t channels_ = 0;
  volatile uint32_t head_ = 0;
  volatile uint32_t tail_ = 0;
  volatile uint32_t droppedBlocks_ = 0;
};

}  // namespace songbeam
#endif
