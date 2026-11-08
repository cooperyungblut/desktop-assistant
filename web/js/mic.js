// Speech input in two modes, both handing back one audio clip:
// - hold:   record while a key is held (V).
// - toggle: click the mic button to start; click again to finish, or just
//           stop talking and it finishes by itself after a few seconds.

const MIN_MS = 300;      // shorter than this is an accidental tap
const TAIL_MS = 200;     // keep recording briefly after release so the last word isn't clipped

// Toggle mode's silence detection.
export const SILENCE_MS = 2000;   // this long without speech after you've spoken ends the clip
const NO_SPEECH_MS = 8000;        // never spoke at all: give up and send nothing
const MAX_MS = 60000;             // hard cap on one clip
const SPEECH_RMS = 0.02;          // loudness that counts as talking (noise suppression is on)
const CHECK_MS = 50;

export class PushToTalk {
  constructor({ onWaiting, onStart, onStop, onClip, onError }) {
    Object.assign(this, { onWaiting, onStart, onStop, onClip, onError });
    this.stream = null;
    this.recorder = null;
    this.holding = false;
    this.mode = null;       // "hold" or "toggle" while recording
    this._monitor = null;   // silence-check interval in toggle mode
  }

  get recording() { return this.holding; }

  // Mic button: start, or finish if already recording.
  toggle() {
    if (this.holding) this.release();
    else this.press("toggle");
  }

  async press(mode = "hold") {
    if (this.holding) return;
    this.holding = true;
    this.mode = mode;
    this.heardSpeech = false;
    if (!this.stream) this.onWaiting?.(); // first use: permission prompt / device start
    try {
      // Ask for the mic once and keep it open, so later presses start instantly.
      // 127.0.0.1 counts as a secure origin, so the browser allows this.
      this.stream ??= await navigator.mediaDevices.getUserMedia({
        // autoGainControl off: Chromium's AGC can move the Windows mic volume slider.
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      });
    } catch (err) {
      this.holding = false;
      this.onError?.(`Microphone unavailable: ${err.message}`);
      return;
    }
    if (!this.holding) { // released while the permission prompt was open
      this.onStop?.();
      return;
    }

    const chunks = [];
    const recorder = new MediaRecorder(this.stream);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    // (mode is the argument; this.mode is cleared on release)
    recorder.onstop = () => {
      this.onStop?.();
      if (this.releasedAt - started < MIN_MS) return; // how long the key was held
      if (mode === "toggle" && !this.heardSpeech) return; // clicked on, never spoke
      this.onClip?.(new Blob(chunks, { type: recorder.mimeType }));
    };
    const started = performance.now();
    recorder.start();
    this.recorder = recorder;
    if (mode === "toggle") this._watchForSilence(started);
    this.onStart?.(mode);
  }

  release() {
    if (!this.holding) return;
    this.holding = false;
    this.mode = null;
    clearInterval(this._monitor);
    this._monitor = null;
    this.releasedAt = performance.now();
    const recorder = this.recorder;
    this.recorder = null;
    if (recorder?.state === "recording") setTimeout(() => recorder.stop(), TAIL_MS);
  }

  // Toggle mode: finish once you've been quiet for SILENCE_MS after speaking.
  _watchForSilence(started) {
    this._ctx ??= new AudioContext();
    if (this._ctx.state === "suspended") this._ctx.resume();
    if (!this._analyser) {
      this._analyser = this._ctx.createAnalyser();
      this._analyser.fftSize = 1024;
      this._ctx.createMediaStreamSource(this.stream).connect(this._analyser);
      this._samples = new Float32Array(this._analyser.fftSize);
    }
    let lastSpeech = started;
    // An interval, not requestAnimationFrame: it keeps running if the tab is hidden.
    this._monitor = setInterval(() => {
      const now = performance.now();
      if (rms(this._analyser, this._samples) > SPEECH_RMS) {
        this.heardSpeech = true;
        lastSpeech = now;
      }
      const quiet = now - lastSpeech;
      if ((this.heardSpeech && quiet > SILENCE_MS) || quiet > NO_SPEECH_MS || now - started > MAX_MS) {
        this.release();
      }
    }, CHECK_MS);
  }
}

function rms(analyser, samples) {
  analyser.getFloatTimeDomainData(samples);
  let sum = 0;
  for (const s of samples) sum += s * s;
  return Math.sqrt(sum / samples.length);
}

export function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
