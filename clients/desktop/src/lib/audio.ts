/** Click-to-toggle mic capture → 16 kHz mono PCM WAV (TUI parity). */

const TARGET_RATE = 16000;
const MAX_SECONDS = 30;
const MIN_SECONDS = 0.35;

export type CaptureStopReason = "stop" | "cancel";

export class AudioCapture {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private chunks: Float32Array[] = [];
  private startedAt = 0;
  private recording = false;

  get isRecording(): boolean {
    return this.recording;
  }

  async start(): Promise<void> {
    if (this.recording) return;
    this.chunks = [];
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
      video: false,
    });
    const ctx = new AudioContext();
    this.context = ctx;
    this.source = ctx.createMediaStreamSource(this.stream);
    // ScriptProcessor is deprecated but widely available in WebView2; buffer 4096.
    this.processor = ctx.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (ev) => {
      if (!this.recording) return;
      const input = ev.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(input));
      const elapsed = (performance.now() - this.startedAt) / 1000;
      if (elapsed >= MAX_SECONDS) {
        void this.stop("stop");
      }
    };
    this.source.connect(this.processor);
    const mute = ctx.createGain();
    mute.gain.value = 0;
    this.processor.connect(mute);
    mute.connect(ctx.destination);
    this.startedAt = performance.now();
    this.recording = true;
  }

  async stop(reason: CaptureStopReason): Promise<Uint8Array | null> {
    if (!this.recording && this.chunks.length === 0) {
      this.cleanup();
      return null;
    }
    this.recording = false;
    const elapsed = (performance.now() - this.startedAt) / 1000;
    const sampleRate = this.context?.sampleRate ?? 48000;
    const merged = mergeFloat32(this.chunks);
    this.cleanup();
    if (reason === "cancel") return null;
    if (elapsed < MIN_SECONDS || merged.length === 0) {
      throw new Error("Recording too short.");
    }
    const down = downsample(merged, sampleRate, TARGET_RATE);
    return encodeWavPcm16(down, TARGET_RATE);
  }

  private cleanup() {
    try {
      this.processor?.disconnect();
    } catch {
      /* ignore */
    }
    try {
      this.source?.disconnect();
    } catch {
      /* ignore */
    }
    this.processor = null;
    this.source = null;
    if (this.context) {
      void this.context.close();
      this.context = null;
    }
    if (this.stream) {
      for (const t of this.stream.getTracks()) t.stop();
      this.stream = null;
    }
    this.chunks = [];
  }
}

function mergeFloat32(chunks: Float32Array[]): Float32Array {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

function downsample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const newLen = Math.floor(input.length / ratio);
  const out = new Float32Array(newLen);
  for (let i = 0; i < newLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j++) {
      sum += input[j];
      count += 1;
    }
    out[i] = count > 0 ? sum / count : 0;
  }
  return out;
}

function encodeWavPcm16(samples: Float32Array, sampleRate: number): Uint8Array {
  const dataLen = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLen);
  const view = new DataView(buffer);
  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + dataLen, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, dataLen, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Uint8Array(buffer);
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}
