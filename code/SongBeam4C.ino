#include <SPI.h>
#include <SD.h>
#include <SerialFlash.h>

#include <Audio.h>
#ifdef MTP_INTERFACE
#include <MTP_Teensy.h>
#endif
#include <Wire.h>
#include <TimeLib.h>
#include <TimeAlarms.h>
#include <string.h>
#include "CaptureRing.h"
#include "UsbFrame.h"
#include "WavFormat.h"

extern "C" uint32_t set_arm_clock(uint32_t frequency);
extern "C" volatile uint8_t usb_configuration;

char postfix[5]=".wav";
char devname[5]="R01_";


//write wav
unsigned long ChunkSize = 0L;
unsigned long Subchunk1Size = 16;
unsigned int AudioFormat = 1;
unsigned int numChannels = 4;
unsigned long sampleRate = 44100;
unsigned int bitsPerSample = 16;
unsigned long byteRate = sampleRate*numChannels*(bitsPerSample/8);// samplerate x channels x (bitspersample / 8)
unsigned int blockAlign = numChannels*bitsPerSample/8;
unsigned long Subchunk2Size = 0L;
unsigned long recByteSaved = 0L;
unsigned long NumSamples = 0L;

// The audio interrupt copies complete interleaved blocks into RAM2.  The SD
// writer can then stall without holding AudioRecordQueue blocks or losing the
// four channels' alignment.  384 KiB provides about 1.11 s at four channels.
static DMAMEM __attribute__((aligned(32))) uint8_t captureStorage[384 * 1024];

class AudioCaptureRing : public AudioStream {
 public:
  AudioCaptureRing() : AudioStream(4, inputQueueArray),
                       ring(captureStorage, sizeof(captureStorage)) {}

  void begin(uint16_t channels) {
    AudioNoInterrupts();
    clearPending();
    ring.reset(channels);
    missingInputBlocks = 0;
    channels_ = channels;
    enabled_ = true;
    AudioInterrupts();
  }

  void end() {
    AudioNoInterrupts();
    enabled_ = false;
    for (unsigned ch = 0; ch < channels_; ++ch)
      if (pendingCount[ch] > 0) ++missingInputBlocks;
    clearPending();
    AudioInterrupts();
  }

  void update() override {
    for (unsigned ch = 0; ch < 4; ++ch) {
      audio_block_t* block = receiveReadOnly(ch);
      if (!block) continue;
      if (!enabled_ || ch >= channels_) {
        release(block);
      } else if (pendingCount[ch] == 4) {
        ++missingInputBlocks;
        release(block);
      } else {
        pending[ch][pendingCount[ch]++] = block;
      }
    }
    if (!enabled_) return;
    for (unsigned ch = 0; ch < channels_; ++ch)
      if (pendingCount[ch] == 0) return;

    const uint8_t* inputs[4] = {};
    for (unsigned ch = 0; ch < channels_; ++ch)
      inputs[ch] = reinterpret_cast<const uint8_t*>(pending[ch][0]->data);
    ring.push(inputs);
    for (unsigned ch = 0; ch < channels_; ++ch) {
      release(pending[ch][0]);
      for (unsigned i = 1; i < pendingCount[ch]; ++i)
        pending[ch][i - 1] = pending[ch][i];
      --pendingCount[ch];
    }
  }

  songbeam::CaptureRing ring;
  volatile uint32_t missingInputBlocks = 0;

 private:
  void clearPending() {
    for (unsigned ch = 0; ch < 4; ++ch) {
      for (unsigned i = 0; i < pendingCount[ch]; ++i) release(pending[ch][i]);
      pendingCount[ch] = 0;
    }
  }
  audio_block_t* inputQueueArray[4];
  audio_block_t* pending[4][4] = {};
  uint8_t pendingCount[4] = {};
  uint16_t channels_ = 4;
  bool enabled_ = false;
};

AudioInputI2S audioInput;
AudioInputI2S2 audioInput2;
AudioCaptureRing capture;
AudioConnection patchCord1(audioInput, 0, capture, 0);
AudioConnection patchCord2(audioInput, 1, capture, 1);
AudioConnection patchCord3(audioInput2, 0, capture, 2);
AudioConnection patchCord4(audioInput2, 1, capture, 3);

enum class UsbMode : uint8_t { Idle, Raw4, Stereo2, Card };
UsbMode usbMode = UsbMode::Idle;
char usbCommand[48] = {};
uint8_t usbCommandLength = 0;
uint32_t usbSequence = 0;
uint32_t usbStreamStartMillis = 0;
uint32_t usbSentFrames = 0;
bool usbWasConfigured = false;
bool sdReady = false;
bool mtpFilesystemAdded = false;
bool cardModeNeedsReset = false;
int autoRecordOnBoot = 1;
uint8_t usbStereoBuffer[songbeam::kUsbStereoPayloadBytes] = {};


int recDay[7]={1,1,1,1,1,1,1};


int mode = 0;  // 0=stopped, 1=recording, 2=playing
FsFile frec;
bool recordingFailed = false;
uint32_t failedRecordings = 0;
uint32_t maxRingDepth = 0;
elapsedMillis  msecs;

int filecount=0;

int recmins=15;
unsigned int tsamplemillis = 60000*recmins;

int starttimehour=7;
int starttimemin=0;

time_t begintime=0;
time_t endtime=0;
time_t offtime=0;

int ontime=36*60; //number of seconds before power off. 34*60 is with TPL51110 set with real 100Ohm.
int resistChoice=0;

//THIS IS THE DEFAULT ontime for a 100kOhm resistor - this is for the new machines!

int recordPeriodMins=180;

//int hiclock=450000000;
// Use the fast clock while moving four synchronized channels to SD.  The
// previous 24 MHz setting is retained for idle time to limit battery draw.
constexpr uint32_t captureClock = 150000000;
constexpr uint32_t idleClock = 24000000;
constexpr unsigned int audioPoolBlocks = 64;
constexpr int writeBatchBlocks = 8;
uint32_t recordStartMillis = 0;
uint32_t preparationMillis = 0;
uint32_t worstWriteMicros = 0;
uint32_t sdWriteCalls = 0;

int PIN_POWER=32;

#define SDCARD_CS_PIN    BUILTIN_SDCARD

void serviceUsb();
void stopUsbMode(bool sendEndFrame);


void setup() {

  set_arm_clock(idleClock);

  
  Serial.begin(9600);

  for (int i=0; i<10; i++){
    delay(50);
    if (Serial){
      i=10;
    }
    //Serial.println(i);
  }

  setSyncProvider(getTeensy3Time);

  //setTime(7,59,0,1,2,11);

  pinMode(LED_BUILTIN, OUTPUT);

  
  AudioMemory(audioPoolBlocks);
#ifdef MTP_INTERFACE
  MTP.begin();
#endif

  sdReady = SD.sdfs.begin(SdioConfig(FIFO_SDIO));
  if (sdReady) {
    Serial.println("reading config file");
    readconfig();
  } else {
    Serial.println("SD unavailable: USB streaming remains possible");
  }
  if (numChannels != 2 && numChannels != 4) numChannels = 4;
  if (recmins < 1 || recmins > 120) recmins = 15;
  if (recordPeriodMins < 1 || recordPeriodMins > 1440) recordPeriodMins = 180;
  if (starttimehour < 0 || starttimehour > 23) starttimehour = 7;
  if (starttimemin < 0 || starttimemin > 59) starttimemin = 0;
  blockAlign = numChannels * (bitsPerSample / 8);
  byteRate = sampleRate * blockAlign;

  if (numChannels==2){
    PIN_POWER=32;
  }
  
  pinMode(PIN_POWER, OUTPUT);
  digitalWrite(PIN_POWER, LOW);
  if (year() < 2020 || year() > 2100) {
    Serial.println("Invalid RTC: recording disabled");
  }

  Serial.println(devname);
  Serial.println(starttimemin);
  Serial.println(starttimehour);
  Serial.println(recmins);
  Serial.println(recordPeriodMins);
  Serial.println(numChannels);
  for (int i=0; i<7; i++){Serial.println(recDay[i]);}

  if (resistChoice==1){
     ontime=285*6; //number of seconds before power off. 285*6 is with TPL51110 set with 91Ohm
     //Most of the first 10 had these resistors
  }
  else if (resistChoice==2){
     ontime=34*60; //number of seconds before power off. 34*60 is with TPL51110 set with 100Ohm.
     //A couple of the first 10 had these resistors.
  }


  tsamplemillis = 60000*recmins;
  
  tmElements_t tm;
  breakTime(now(), tm);
  tm.Second=0;
  tm.Minute=starttimemin;
  tm.Hour=starttimehour;

  Serial.println("WEEKDAY:");
  Serial.println(tm.Wday);

  
  begintime=makeTime(tm);
  endtime=begintime+60*recordPeriodMins;
  
 time_t x=now();
 offtime=x+ontime;
  offtime=offtime-(recmins+1)*60;

  Serial.println(x);
  Serial.println(begintime);
  Serial.println(endtime);
  
  //set_arm_clock(hiclock);
  delay(1000);
}

/*
void loop(){
  digitalWrite(LED_BUILTIN, HIGH);   // turn the LED on (HIGH is the voltage level)
  delay(1000);               // wait for a second
  digitalWrite(LED_BUILTIN, LOW);    // turn the LED off by making the voltage LOW
  delay(1000);  
}
*/

/*
void loops(){
  int x=now();
  Serial.print("New loop: ");
  Serial.println(x);
  recordSimple(tsamplemillis);
}
*/
void loop() {
  if (usb_configuration) {
    usbWasConfigured = true;
    serviceUsb();
    return;
  }
  if (usbWasConfigured) {
    if (usbMode == UsbMode::Card) cardModeNeedsReset = true;
    stopUsbMode(false);
    usbWasConfigured = false;
  }
  set_arm_clock(idleClock);
  if (cardModeNeedsReset || !sdReady || year() < 2020 || year() > 2100) {
    digitalWrite(LED_BUILTIN, HIGH);
    delay(250);
    digitalWrite(LED_BUILTIN, LOW);
    delay(750);
    return;
  }
  const time_t x = now();
  if (x >= offtime) {
    digitalWrite(PIN_POWER, HIGH);
    delay(1000);
    return;
  }
  if (autoRecordOnBoot) {
    numChannels = 4;
    const uint32_t remainingMillis = uint32_t(offtime - x) * 1000U;
    if (remainingMillis > 10000U)
      recordSimple(remainingMillis < tsamplemillis ? remainingMillis : tsamplemillis);
    else {
      digitalWrite(PIN_POWER, HIGH);
      delay(1000);
    }
    return;
  }
  const int day = weekday(x) - 1;
  if (recDay[day] == 0 || x >= endtime) {
    digitalWrite(PIN_POWER, HIGH);
    delay(1000);
  } else if (x > begintime) {
    recordSimple(tsamplemillis);
  } else {
    delay(1000);
  }
}

void stopUsbMode(bool sendEndFrame) {
  if (usbMode == UsbMode::Raw4 || usbMode == UsbMode::Stereo2) {
    capture.end();
    const uint32_t undelivered = capture.ring.available();
    const uint32_t lost = capture.ring.droppedBlocks() +
                          capture.missingInputBlocks + undelivered;
    if (sendEndFrame && Serial.dtr()) {
      uint8_t header[songbeam::kUsbFrameHeaderBytes];
      songbeam::usbFrameHeader(header, songbeam::kUsbFrameEnd, 0,
                               usbSequence, millis() - usbStreamStartMillis,
                               lost, 0);
      Serial.write(header, sizeof(header));
    }
  }
  usbMode = UsbMode::Idle;
  set_arm_clock(idleClock);
}

void handleUsbCommand(const char* command) {
  if ((usbMode == UsbMode::Raw4 || usbMode == UsbMode::Stereo2) &&
      strcmp(command, "STOP") != 0) return;
  if (usbMode == UsbMode::Card && strcmp(command, "STATUS") != 0 &&
      strncmp(command, "TIME ", 5) != 0) {
    Serial.println("ERR card mode ends only after safe host eject and power cycle");
    return;
  }
  if (strcmp(command, "STATUS") == 0) {
    Serial.print("SB FW MTP1 SD="); Serial.print(sdReady ? 1 : 0);
    Serial.print(" RTC="); Serial.print(year() >= 2020 && year() <= 2100 ? 1 : 0);
    Serial.print(" FAILURES="); Serial.print(failedRecordings);
    Serial.print(" MODE="); Serial.println(static_cast<int>(usbMode));
  } else if (strcmp(command, "STREAM 4") == 0) {
    stopUsbMode(false);
    set_arm_clock(captureClock);
    capture.begin(4);
    AudioMemoryUsageMaxReset();
    usbSequence = 0;
    usbSentFrames = 0;
    usbStreamStartMillis = millis();
    usbMode = UsbMode::Raw4;
    Serial.println("OK STREAM4 44100 4 S16LE");
  } else if (strcmp(command, "STREAM 2") == 0) {
    stopUsbMode(false);
    set_arm_clock(captureClock);
    capture.begin(4);
    AudioMemoryUsageMaxReset();
    usbSequence = 0;
    usbSentFrames = 0;
    usbStreamStartMillis = millis();
    usbMode = UsbMode::Stereo2;
    Serial.println("OK STREAM2 44100 2 S16LE");
  } else if (strcmp(command, "CARD") == 0) {
#ifdef MTP_INTERFACE
    if (!sdReady) { Serial.println("ERR SD unavailable"); return; }
    stopUsbMode(false);
    if (!mtpFilesystemAdded) {
      if (!MTP.addFilesystem(SD, "SONGBEAM")) {
        Serial.println("ERR MTP storage setup failed"); return;
      }
      mtpFilesystemAdded = true;
    }
    usbMode = UsbMode::Card;
    MTP.reset();
    Serial.println("OK CARD MTP");
#else
    Serial.println("ERR MTP not in this build");
#endif
  } else if (strcmp(command, "STOP") == 0) {
    const bool wasStream = usbMode == UsbMode::Raw4 || usbMode == UsbMode::Stereo2;
    stopUsbMode(true);
    if (!wasStream) Serial.println("OK STOP");
  } else if (strcmp(command, "RECORD 1") == 0) {
    if (!sdReady || year() < 2020 || year() > 2100) {
      Serial.println("ERR SD or RTC unavailable");
      return;
    }
    stopUsbMode(false);
    numChannels = 4;
    Serial.println("OK RECORD 1");
    recordSimple(tsamplemillis);  // one bounded file, then USB-controlled idle
    Serial.println(recordingFailed ? "ERR RECORD INCOMPLETE" : "OK RECORD DONE");
  } else if (strncmp(command, "TIME ", 5) == 0) {
    char* end = nullptr;
    const unsigned long epoch = strtoul(command + 5, &end, 10);
    if (!end || *end || epoch < 1577836800UL || epoch > 4102444800UL) {
      Serial.println("ERR invalid UTC epoch");
      return;
    }
    Teensy3Clock.set(epoch);
    setTime(epoch);
    Serial.println("OK TIME");
  } else if (usbMode == UsbMode::Idle) {
    Serial.println("ERR unknown command");
  }
}

void serviceUsb() {
  for (unsigned i = 0; i < 32 && Serial.available() > 0; ++i) {
    const int received = Serial.read();
    if (received == '\r') continue;
    if (received == '\n') {
      usbCommand[usbCommandLength] = '\0';
      handleUsbCommand(usbCommand);
      usbCommandLength = 0;
    } else if (received >= 32 && received <= 126 &&
               usbCommandLength < sizeof(usbCommand) - 1) {
      usbCommand[usbCommandLength++] = static_cast<char>(received);
    } else {
      usbCommandLength = 0;
    }
  }
  if (usbMode == UsbMode::Card || usbMode == UsbMode::Idle) {
#ifdef MTP_INTERFACE
    MTP.loop();
#endif
    if (usbMode == UsbMode::Idle) delay(1);
    return;
  }
  if (!Serial.dtr()) {
    stopUsbMode(false);
    return;
  }
  for (unsigned i = 0; i < 8 && capture.ring.available() > 0; ++i) {
    const bool stereo = usbMode == UsbMode::Stereo2;
    const size_t payloadBytes = stereo ? songbeam::kUsbStereoPayloadBytes :
                                         songbeam::kUsbFramePayloadBytes;
    if (Serial.availableForWrite() < int(songbeam::kUsbFrameHeaderBytes + payloadBytes)) break;
    const uint8_t* payload = capture.ring.front();
    if (stereo) {
      songbeam::stereoPairSum128(payload, usbStereoBuffer);
      payload = usbStereoBuffer;
    }
    uint8_t header[songbeam::kUsbFrameHeaderBytes];
    songbeam::usbFrameHeader(header, stereo ? songbeam::kUsbFrameStereo :
                                             songbeam::kUsbFrameAudio, 128,
                             usbSequence, millis() - usbStreamStartMillis,
                             capture.ring.droppedBlocks() + capture.missingInputBlocks,
                             songbeam::crc32(payload, payloadBytes));
    const size_t sentHeader = Serial.write(header, sizeof(header));
    const size_t sentPayload = Serial.write(payload, payloadBytes);
    if (sentHeader != sizeof(header) || sentPayload != payloadBytes) {
      stopUsbMode(false);  // host will reject the truncated frame
      return;
    }
    capture.ring.consume(1);
    ++usbSequence;
    usbSentFrames += 128;
  }
}

void recordSimple(unsigned long ts){
  set_arm_clock(captureClock);
  delay(500);
  if (numChannels==2){
    char* fn=startRecording2Chan();
    if (fn) {
      // Starting the timer before preAllocate() shortened real recordings by
      // the card's variable allocation latency (1.14 s in the bench run).
      elapsedMillis recordingTime = 0;
      while(recordingTime<ts && !recordingFailed && mode == 1) continueRecording2Chan();
      stopRecording2Chan(fn);
    }
  }
  else{
    char* fn=startRecording();  
    if (fn) {
      elapsedMillis recordingTime = 0;
      while(recordingTime<ts && !recordingFailed && mode == 1) continueRecording();
      stopRecording(fn);
    }
  }
  set_arm_clock(idleClock);
  delay(1000);
}


char* startRecordingChannels(unsigned int channels) {
  static char filename[48];
  const uint32_t prepareStart = millis();
  const char* base = makeFilename();
  size_t stemLength = strlen(base) - strlen(postfix);
  recordingFailed = false;
  mode = 0;
  for (unsigned int suffix = 0; suffix < 100; suffix++) {
    if (suffix == 0) snprintf(filename, sizeof(filename), "%s", base);
    else snprintf(filename, sizeof(filename), "%.*s_%02u%s", (int)stemLength, base, suffix, postfix);
    if (!SD.exists(filename)) break;
    if (suffix == 99) { Serial.println("No unique recording filename"); recordingFailed = true; return nullptr; }
  }
  frec = SD.sdfs.open(filename, O_RDWR | O_CREAT | O_EXCL);
  if (!frec) { Serial.println("Recording open failed"); recordingFailed = true; return nullptr; }
  const uint64_t expected = 44ULL + (uint64_t)channels * 2ULL * sampleRate * 60ULL * (uint64_t)recmins;
  if (expected > 0xFFFFFFFFULL || !frec.preAllocate(expected) || !frec.seek(0)) {
    Serial.println("Recording allocation failed");
    frec.close();
    recordingFailed = true;
    return nullptr;
  }
  byte header[44] = {0};
  if (frec.write(header, sizeof(header)) != sizeof(header)) {
    Serial.println("WAV header reserve failed");
    frec.close();
    recordingFailed = true;
    return nullptr;
  }
  capture.begin(channels);
  AudioMemoryUsageMaxReset();
  recordStartMillis = millis();
  preparationMillis = recordStartMillis - prepareStart;
  worstWriteMicros = 0;
  sdWriteCalls = 0;
  mode = 1;
  recByteSaved = 0;
  maxRingDepth = 0;
  return filename;
}

char* startRecording() { return startRecordingChannels(4); }
char* startRecording2Chan() { return startRecordingChannels(2); }

void writeInterleavedBlocks(unsigned int channels, unsigned int requestedBlocks) {
  const uint32_t blocks = capture.ring.contiguous(requestedBlocks);
  if (blocks == 0) return;
  const size_t wanted = blocks * capture.ring.blockBytes();
  const uint32_t writeStart = micros();
  const size_t written = frec.write(capture.ring.front(), wanted);
  const uint32_t writeMicros = micros() - writeStart;
  if (writeMicros > worstWriteMicros) worstWriteMicros = writeMicros;
  ++sdWriteCalls;
  recByteSaved += written - written % (channels * 2U);
  if (written == wanted) capture.ring.consume(blocks);
  if (written != wanted) { recordingFailed = true; Serial.println("Short SD audio write"); }
}

void continueRecording() {
  const uint32_t depth = capture.ring.available();
  if (depth > maxRingDepth) maxRingDepth = depth;
  if (depth >= writeBatchBlocks) writeInterleavedBlocks(4, writeBatchBlocks);
}

void continueRecording2Chan() {
  const uint32_t depth = capture.ring.available();
  if (depth > maxRingDepth) maxRingDepth = depth;
  if (depth >= writeBatchBlocks) writeInterleavedBlocks(2, writeBatchBlocks);
}

void stopRecordingChannels(unsigned int channels, const char* filename) {
  capture.end();
  if (mode != 1) return;
  const uint32_t captureMillis = millis() - recordStartMillis;
  while (!recordingFailed && capture.ring.available() > 0)
    writeInterleavedBlocks(channels, writeBatchBlocks);
  const uint32_t dropped = capture.ring.droppedBlocks();
  const uint32_t missing = capture.missingInputBlocks;
  if (dropped || missing) {
    recordingFailed = true;
    Serial.println("Capture ring overflow or missing microphone block");
  }
  const uint64_t expectedFrames = (uint64_t)sampleRate * captureMillis / 1000;
  const uint64_t savedFrames = recByteSaved / (channels * 2U);
  if (savedFrames + sampleRate / 10 < expectedFrames) {
    recordingFailed = true;
    Serial.println("Audio duration short: possible dropped capture blocks");
  }
  if (!frec.seek(44ULL + recByteSaved) || !frec.truncate()) recordingFailed = true;
  if (!writeOutHeader() || !frec.sync()) recordingFailed = true;
  frec.close();
  if (recordingFailed) {
    ++failedRecordings;
    digitalWrite(LED_BUILTIN, HIGH);
    char failedName[64];
    snprintf(failedName, sizeof(failedName), "%s.failed", filename);
    FsFile marker = SD.sdfs.open(failedName, O_WRONLY | O_CREAT | O_EXCL);
    if (marker) {
      marker.print("INCOMPLETE SONG BEAM WAV\nCaptureMs="); marker.println(captureMillis);
      marker.print("SavedFrames="); marker.println((uint32_t)savedFrames);
      marker.print("DroppedRingBlocks="); marker.println(dropped);
      marker.print("MissingInputBlocks="); marker.println(missing);
      marker.print("WorstSDWriteUs="); marker.println(worstWriteMicros);
      marker.sync(); marker.close();
    }
    Serial.println("Recording incomplete: inspect WAV and .failed marker");
  }
  Serial.print("Max audio memory blocks: "); Serial.println(AudioMemoryUsageMax());
  Serial.print("Max ring depth: "); Serial.println(maxRingDepth);
  Serial.print("Capture ring capacity: "); Serial.println(capture.ring.capacity());
  Serial.print("Dropped ring blocks: "); Serial.println(dropped);
  Serial.print("Missing input blocks: "); Serial.println(missing);
  Serial.print("Preparation ms: "); Serial.println(preparationMillis);
  Serial.print("Capture ms: "); Serial.println(captureMillis);
  Serial.print("Saved frames: "); Serial.println((uint32_t)savedFrames);
  Serial.print("SD write calls: "); Serial.println(sdWriteCalls);
  Serial.print("Worst SD write us: "); Serial.println(worstWriteMicros);
  if (AudioMemoryUsageMax() >= audioPoolBlocks) Serial.println("Audio pool saturated");
  mode = 0;
}

void stopRecording(char* fn) { stopRecordingChannels(4, fn); }
void stopRecording2Chan(char* fn) { stopRecordingChannels(2, fn); }


bool writeOutHeader() {
  byte header[44];
  songbeam::wavHeader(header, recByteSaved, sampleRate, numChannels);
  return frec.seek(0) && frec.write(header, sizeof(header)) == sizeof(header);
}

char *makeFilename(){ 
  static char filename[40];
  snprintf(filename, sizeof(filename), "%s_%04d_%02d_%02d_%02d_%02d_%02d%s", devname, year(), month(), day(), hour(), minute(), second(), postfix);
  return filename;  
}

time_t getTeensy3Time()
{
  return Teensy3Clock.get();
}

void readconfig(){
  const size_t LINE_DIM = 50;
  char line[LINE_DIM];
  size_t n;
  FsFile file;
  for (int i=0; i<7; i++){recDay[i]=0;}
  if (file.open("config.txt", O_READ)) {
    int x=0;
    
    while ((n = file.fgets(line, sizeof(line))) > 0) {
      line[strcspn(line, "\r\n")] = 0;
      if (x>0){
        int a=1;
        String str=String(a);
        if (x==1){
          strncpy(devname, line, sizeof(devname)-1);
          devname[sizeof(devname)-1] = '\0';
          //devname=line;
        }
        else if (x==2){
          starttimehour=atoi(line);
        }
        else if (x==3){
          starttimemin=atoi(line);
        }
        else if (x==4){
          recordPeriodMins=atoi(line);
        }
        else if (x==5){
          recmins=atoi(line);
        }
        else if (x==6){
          recDay[0]=atoi(line) != 0;
        }
        else if (x==7){
          recDay[1]=atoi(line) != 0;
        }
        else if (x==8){
          recDay[2]=atoi(line) != 0;
        }
        else if (x==9){
          recDay[3]=atoi(line) != 0;
        }
        else if (x==10){
          recDay[4]=atoi(line) != 0;
        }
        else if (x==11){
          recDay[5]=atoi(line) != 0;
        }
        else if (x==12){
          recDay[6]=atoi(line) != 0;
        }
        else if (x==13){
          numChannels=atoi(line);
        }
        else if (x==14){
          resistChoice=atoi(line);
        }
        else if (x==15){
          autoRecordOnBoot=atoi(line) != 0;
        }

        x=0;
      }
      
      if (strcmp(line, "DeviceID:")==0){x=1;}
      else if (strcmp(line, "RecordStartHrs:")==0){x=2;}
      else if (strcmp(line, "RecordStartMins:")==0){x=3;}
      else if (strcmp(line, "RecordLengthMins:")==0){x=4;}
      else if (strcmp(line, "FileLengthMins:")==0){x=5;}
      else if (strcmp(line, "RecSun:")==0){x=6;}
      else if (strcmp(line, "RecMon:")==0){x=7;}
      else if (strcmp(line, "RecTue:")==0){x=8;}
      else if (strcmp(line, "RecWed:")==0){x=9;}
      else if (strcmp(line, "RecThu:")==0){x=10;}
      else if (strcmp(line, "RecFri:")==0){x=11;}
      else if (strcmp(line, "RecSat:")==0){x=12;} 
      else if (strcmp(line, "NumChan:")==0){x=13;}
      else if (strcmp(line, "Resist:")==0){x=14;}
      else if (strcmp(line, "AutoRecordOnBoot:")==0){x=15;}

  }
  file.close();
  delay(50);
  }

  
}

/*
LICENSE:  Creative Commons Attribution 4.0 International License
          Royal Holloway University of London
          Robert Lachlan & Lies Zandberg
*/
