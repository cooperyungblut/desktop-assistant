// Push-to-talk: record while a key or button is held, then hand back the clip.

const MIN_MS = 300;      // shorter than this is an accidental tap
const TAIL_MS = 200;     // keep recording briefly after release so the last word isn't clipped

export class PushToTalk {
  constructor({ onWaiting, onStart, onStop, onClip, onError }) {
    Object.assign(this, { onWaiting, onStart, onStop, onClip, onError });
    this.stream = null;
    this.recorder = null;
    this.holding = false;
  }

  async press() {
    if (this.holding) return;
    this.holding = true;
    if (!this.stream) this.onWaiting?.(); // first use: permission prompt / device start
    try {
      // Ask for the mic once and keep it open, so later presses start instantly.
      // 127.0.0.1 counts as a secure origin, so the browser allows this.
      this.stream ??= await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
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
    recorder.onstop = () => {
      this.onStop?.();
      if (this.releasedAt - started < MIN_MS) return; // how long the key was held
      this.onClip?.(new Blob(chunks, { type: recorder.mimeType }));
    };
    const started = performance.now();
    recorder.start();
    this.recorder = recorder;
    this.onStart?.();
  }

  release() {
    if (!this.holding) return;
    this.holding = false;
    this.releasedAt = performance.now();
    const recorder = this.recorder;
    this.recorder = null;
    if (recorder?.state === "recording") setTimeout(() => recorder.stop(), TAIL_MS);
  }
}

export function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
