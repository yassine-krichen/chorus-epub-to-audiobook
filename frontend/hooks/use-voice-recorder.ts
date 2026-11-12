"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  MAX_DURATION_S,
  analyzeRecording,
  mixToMono,
  type RecordingAnalysis,
} from "@/lib/voices/recording-quality";

/** The recording is decoded at this rate, so the file we upload is small and consistent. */
const TARGET_SAMPLE_RATE = 24_000;
/** Recording stops by itself a little before the server's own limit. */
const AUTO_STOP_S = 30;

export type RecorderState = "idle" | "requesting" | "recording" | "processing" | "recorded" | "error";

export interface Recording {
  samples: Float32Array;
  sampleRate: number;
  /** For the playback control; revoked when a new recording starts or the dialog closes. */
  objectUrl: string;
  analysis: RecordingAnalysis;
}

interface VoiceRecorder {
  state: RecorderState;
  elapsedS: number;
  /** 0 to 1, how loud the microphone is right now. */
  level: number;
  recording: Recording | null;
  error: string | null;
  supported: boolean;
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
}

function describeMicrophoneError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "The browser blocked the microphone. Allow microphone access for this site (the icon in the address bar) and try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return "No microphone was found. Plug one in and try again.";
  }
  if (name === "NotReadableError") {
    return "The microphone is in use by another app. Close it and try again.";
  }
  return "We could not start the microphone. Check it is connected and try again.";
}

/** Records the microphone with MediaRecorder, meters the level, and decodes the result so its
 * quality can be checked before anything is uploaded. */
export function useVoiceRecorder(): VoiceRecorder {
  const [state, setState] = useState<RecorderState>("idle");
  const [elapsedS, setElapsedS] = useState(0);
  const [level, setLevel] = useState(0);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const meterContextRef = useRef<AudioContext | null>(null);
  const timerRef = useRef<number | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const objectUrlRef = useRef<string | null>(null);

  const supported =
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia);

  const releaseMicrophone = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void meterContextRef.current?.close().catch(() => undefined);
    meterContextRef.current = null;
    setLevel(0);
  }, []);

  const revokeUrl = useCallback(() => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
    }
    recorderRef.current = null;
    releaseMicrophone();
    revokeUrl();
    setRecording(null);
    setError(null);
    setElapsedS(0);
    setState("idle");
  }, [releaseMicrophone, revokeUrl]);

  // Leaving the page must never leave the microphone on.
  useEffect(() => reset, [reset]);

  const finish = useCallback(
    async (blob: Blob) => {
      setState("processing");
      try {
        const context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
        const decoded = await context.decodeAudioData(await blob.arrayBuffer());
        void context.close().catch(() => undefined);
        const channels = Array.from({ length: decoded.numberOfChannels }, (_, i) =>
          decoded.getChannelData(i)
        );
        const samples = mixToMono(channels);
        revokeUrl();
        objectUrlRef.current = URL.createObjectURL(blob);
        setRecording({
          samples,
          sampleRate: decoded.sampleRate,
          objectUrl: objectUrlRef.current,
          analysis: analyzeRecording(samples, decoded.sampleRate),
        });
        setState("recorded");
      } catch {
        setError("We could not read that recording. Please record it again.");
        setState("error");
      }
    },
    [revokeUrl]
  );

  const start = useCallback(async () => {
    if (!supported) {
      setError("This browser cannot record audio. Try a recent Chrome, Edge, Firefox or Safari.");
      setState("error");
      return;
    }
    reset();
    setState("requesting");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: true,
        },
      });
    } catch (microphoneError) {
      setError(describeMicrophoneError(microphoneError));
      setState("error");
      return;
    }
    streamRef.current = stream;

    // A live level bar tells people the microphone works before they have read a word.
    const meterContext = new AudioContext();
    meterContextRef.current = meterContext;
    const analyser = meterContext.createAnalyser();
    analyser.fftSize = 1024;
    meterContext.createMediaStreamSource(stream).connect(analyser);
    const buffer = new Float32Array(analyser.fftSize);

    chunksRef.current = [];
    const recorder = new MediaRecorder(stream);
    recorderRef.current = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const type = recorder.mimeType || "audio/webm";
      releaseMicrophone();
      void finish(new Blob(chunksRef.current, { type }));
    };
    recorder.start();

    const startedAt = performance.now();
    setElapsedS(0);
    setState("recording");
    timerRef.current = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buffer);
      let sum = 0;
      for (const sample of buffer) sum += sample * sample;
      setLevel(Math.min(1, Math.sqrt(sum / buffer.length) * 6));
      const seconds = (performance.now() - startedAt) / 1000;
      setElapsedS(seconds);
      if (seconds >= Math.min(AUTO_STOP_S, MAX_DURATION_S) && recorder.state === "recording") {
        recorder.stop();
      }
    }, 100);
  }, [supported, reset, releaseMicrophone, finish]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state === "recording") recorder.stop();
  }, []);

  return { state, elapsedS, level, recording, error, supported, start, stop, reset };
}
