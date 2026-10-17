// Fixed-size PCM capture with no speaker output. Used on Safari, Chrome and Android.
class MagSpeechCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.used = 0;
  }
  process(inputs, outputs) {
    const chans = inputs[0] || [];
    const frames = chans[0]?.length || 0;
    for (let i = 0; i < frames; i++) {
      let v = 0;
      for (const c of chans) v += c[i];
      this.buffer[this.used++] = chans.length ? v / chans.length : 0;
      if (this.used === this.buffer.length) {
        this.port.postMessage(this.buffer, [this.buffer.buffer]);
        this.buffer = new Float32Array(2048);
        this.used = 0;
      }
    }
    // Explicit silence: connect to destination so graph pulls without feedback.
    for (const output of outputs)
      for (const channel of output) channel.fill(0);
    return true;
  }
}
registerProcessor('mag-speech-capture', MagSpeechCapture);
