// AudioWorklet of the dictation. Whatever rate the microphone runs at, out
// come 100 ms frames of 16 kHz mono PCM (16 bit) with their level. While the
// microphone stays open between two dictations the last moments are kept, so
// a word begun just before the button is not cut off.
class MagCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    const ratio = sampleRate / 16000;          // input samples per output sample
    const cutoff = 0.46 / Math.max(1, ratio);  // of the input rate: below both Nyquist limits
    this.ratio = ratio;
    this.half = Math.ceil(12 * Math.max(1, ratio));
    this.phases = 32;
    // Windowed-sinc low-pass, one row per fraction of a sample.
    this.kernel = [];
    for (let p = 0; p < this.phases; p++) {
      const row = new Float32Array(2 * this.half);
      let sum = 0;
      for (let k = 0; k < row.length; k++) {
        const d = k - this.half + 1 - p / this.phases, x = 2 * Math.PI * cutoff * d;
        const window = 0.5 + 0.5 * Math.cos(Math.PI * Math.min(1, Math.abs(d) / this.half));
        row[k] = (Math.abs(x) < 1e-9 ? 1 : Math.sin(x) / x) * window;
        sum += row[k];
      }
      for (let k = 0; k < row.length; k++) row[k] /= sum;
      this.kernel.push(row);
    }
    this.input = new Float32Array(this.half * 2 + 8192);
    this.filled = this.half;                   // silence in front, so the first sample has a past
    this.at = this.half;                       // where in `input` the next output sample sits
    this.frame = new Int16Array(1600);
    this.count = 0;
    this.recording = false;
    this.before = [];                          // the last frames while not recording
    this.port.onmessage = (e) => {
      if (e.data === 'start') {
        this.recording = true;
        for (const frame of this.before) this.port.postMessage({ pcm: frame.buffer, level: 0 }, [frame.buffer]);
        this.before = [];
      } else if (e.data === 'stop') {
        if (this.recording && this.count) this.emit(this.frame.slice(0, this.count));
        this.count = 0;
        this.recording = false;
        this.port.postMessage({ stopped: true });
      }
    };
  }

  emit(frame) {
    let energy = 0;
    for (let i = 0; i < frame.length; i++) energy += frame[i] * frame[i];
    const level = Math.sqrt(energy / Math.max(1, frame.length)) / 32768;
    if (this.recording) this.port.postMessage({ pcm: frame.buffer, level }, [frame.buffer]);
    else {
      this.before.push(frame);
      if (this.before.length > 4) this.before.shift();
    }
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || !channel.length) return true;
    if (this.filled + channel.length > this.input.length) { // cannot happen at 128-sample blocks; never overflow
      this.filled = this.half;
      this.at = this.half;
    }
    this.input.set(channel, this.filled);
    this.filled += channel.length;
    while (this.at + this.half < this.filled) {
      let whole = Math.floor(this.at), phase = Math.round((this.at - whole) * this.phases);
      if (phase === this.phases) { phase = 0; whole++; }
      const row = this.kernel[phase], from = whole - this.half + 1;
      let value = 0;
      for (let k = 0; k < row.length; k++) value += this.input[from + k] * row[k];
      this.frame[this.count++] = Math.max(-32768, Math.min(32767, Math.round(value * 32767)));
      if (this.count === this.frame.length) {
        this.emit(this.frame);
        this.frame = new Int16Array(1600);
        this.count = 0;
      }
      this.at += this.ratio;
    }
    // Keep only what the next output samples still need.
    const used = Math.floor(this.at) - this.half;
    if (used > 0) {
      this.input.copyWithin(0, used, this.filled);
      this.filled -= used;
      this.at -= used;
    }
    return true;
  }
}
registerProcessor('mag-capture', MagCapture);
