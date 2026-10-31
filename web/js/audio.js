// Plays sentences of speech in order and reports a loudness level each frame,
// which the avatar uses as its mouth opening (amplitude lip sync).

export class SpeechPlayer {
  constructor({ onLevel, onSentence, onIdle }) {
    this.onLevel = onLevel;
    this.onSentence = onSentence;
    this.onIdle = onIdle;
    this.queue = [];
    this.playing = false;
    this.ctx = null;
    this.source = null;
    this._level = 0;
    this._generation = 0; // bumps on stop() so stale playback can't resume
  }

  // Browsers only allow audio after a user gesture, which is one more reason
  // the conversation starts on a click instead of on its own.
  unlock() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.connect(this.ctx.destination);
      this.samples = new Float32Array(this.analyser.fftSize);
      this._meter();
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
  }

  enqueue(text, wavBase64) {
    this.queue.push({ text, wavBase64 });
    if (!this.playing) this._next();
  }

  stop() {
    this._generation++;
    this.queue = [];
    try { this.source?.stop(); } catch { /* already stopped */ }
    this.source = null;
    this.playing = false;
    this.onIdle?.();
  }

  get busy() { return this.playing || this.queue.length > 0; }

  async _next() {
    const item = this.queue.shift();
    if (!item) { this.playing = false; this.onIdle?.(); return; }
    this.playing = true;
    const gen = this._generation;
    this.onSentence?.(item.text);
    if (item.wavBase64 && this.ctx) {
      await this._play(item.wavBase64);
    } else {
      await this._fakeSpeak(item.text); // text-only mode: animate the mouth anyway
    }
    if (gen === this._generation) this._next();
  }

  async _play(b64) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const buffer = await this.ctx.decodeAudioData(bytes.buffer);
    return new Promise((resolve) => {
      const src = this.ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.analyser);
      src.onended = () => {
        if (this.source === src) this.source = null;
        this._level = 0;
        this.onLevel(0);
        resolve();
      };
      this.source = src;
      src.start();
    });
  }

  _fakeSpeak(text) {
    const ms = Math.max(800, text.length * 55);
    const start = performance.now();
    return new Promise((resolve) => {
      const step = () => {
        const t = performance.now() - start;
        if (t >= ms || !this.playing) { this.onLevel(0); resolve(); return; }
        this.onLevel(0.35 + 0.35 * Math.sin(t / 55));
        requestAnimationFrame(step);
      };
      step();
    });
  }

  _meter() {
    requestAnimationFrame(() => this._meter());
    if (!this.source) return;
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (const s of this.samples) sum += s * s;
    const rms = Math.sqrt(sum / this.samples.length);
    // Map loudness to mouth opening, with a noise floor, then smooth it so the
    // jaw doesn't jitter. Open fast, close a bit slower, like a real mouth.
    const target = Math.min(1, Math.max(0, (rms - 0.015) * 9));
    const k = target > this._level ? 0.6 : 0.3;
    this._level += (target - this._level) * k;
    this.onLevel(this._level);
  }
}
