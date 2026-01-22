import { useState, useEffect, useCallback, useRef } from 'react';

interface VoiceMessage {
  id: string;
  type: 'user' | 'agent';
  text: string;
  timestamp: Date;
}

interface UseVoiceSessionOptions {
  projectId: string;
  authToken?: string;
  onTaskSubmitted?: (prompt: string) => void;
}

export function useVoiceSession({ projectId, authToken, onTaskSubmitted }: UseVoiceSessionOptions) {
  const [isConnected, setIsConnected] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);

  // Audio playback context (separate from mic capture context)
  const playbackContextRef = useRef<AudioContext | null>(null);
  const audioQueueRef = useRef<AudioBufferSourceNode[]>([]);
  const nextPlayTimeRef = useRef<number>(0);

  // Note: We don't use a "bargedIn" flag to block audio - that approach doesn't
  // work because audio blobs can arrive before AgentStartedSpeaking. Instead,
  // we just clear the queue on UserStartedSpeaking and let new audio play.

  // Store callbacks in refs to avoid recreating connect when they change
  const onTaskSubmittedRef = useRef(onTaskSubmitted);
  onTaskSubmittedRef.current = onTaskSubmitted;

  // Get or create playback audio context
  const getPlaybackContext = useCallback(() => {
    if (!playbackContextRef.current || playbackContextRef.current.state === 'closed') {
      // Deepgram TTS output is 24kHz
      playbackContextRef.current = new AudioContext({ sampleRate: 24000 });
    }
    return playbackContextRef.current;
  }, []);

  // Stop all playing audio and clear queue (used for barge-in)
  const stopAllAudio = useCallback(() => {
    audioQueueRef.current.forEach(source => {
      try { source.stop(); } catch { /* ignore */ }
    });
    audioQueueRef.current = [];
    nextPlayTimeRef.current = 0;
  }, []);

  // Play raw PCM audio (linear16 @ 24kHz from Deepgram)
  const playAudio = useCallback(async (blob: Blob) => {
    try {
      const audioContext = getPlaybackContext();

      // Resume context if suspended (browsers require user interaction)
      if (audioContext.state === 'suspended') {
        await audioContext.resume();
      }

      const arrayBuffer = await blob.arrayBuffer();

      // Convert raw PCM (Int16) to Float32 for Web Audio API
      const int16Data = new Int16Array(arrayBuffer);
      const float32Data = new Float32Array(int16Data.length);

      for (let i = 0; i < int16Data.length; i++) {
        // Convert Int16 (-32768 to 32767) to Float32 (-1.0 to 1.0)
        float32Data[i] = int16Data[i] / 32768;
      }

      // Create audio buffer from raw PCM data
      const audioBuffer = audioContext.createBuffer(1, float32Data.length, 24000);
      audioBuffer.copyToChannel(float32Data, 0);

      // Create source node and schedule playback
      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContext.destination);

      // Schedule audio to play in sequence (avoid overlapping)
      const currentTime = audioContext.currentTime;
      const startTime = Math.max(currentTime, nextPlayTimeRef.current);
      source.start(startTime);

      // Update next play time
      nextPlayTimeRef.current = startTime + audioBuffer.duration;

      // Track for cleanup
      audioQueueRef.current.push(source);
      source.onended = () => {
        const idx = audioQueueRef.current.indexOf(source);
        if (idx > -1) audioQueueRef.current.splice(idx, 1);
      };
    } catch (e) {
      console.error('Error playing audio:', e);
    }
  }, [getPlaybackContext]);

  const connect = useCallback(async () => {
    try {
      // Get voice service URL from Electron
      const url = await window.ipcRenderer.invoke('voice-get-url');
      const wsUrl = `${url}?project_id=${projectId}${authToken ? `&auth_token=${authToken}` : ''}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setError(null);
      };

      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          try {
            const msg = JSON.parse(event.data);

            if (msg.type === 'user_transcript') {
              setMessages(prev => [...prev, {
                id: crypto.randomUUID(),
                type: 'user',
                text: msg.text,
                timestamp: new Date(),
              }]);
            } else if (msg.type === 'agent_transcript') {
              setMessages(prev => [...prev, {
                id: crypto.randomUUID(),
                type: 'agent',
                text: msg.text,
                timestamp: new Date(),
              }]);
            } else if (msg.type === 'task_submitted') {
              onTaskSubmittedRef.current?.(msg.prompt);
            } else if (msg.type === 'user_started_speaking') {
              // Barge-in: user interrupted agent, stop all audio immediately
              // Don't block future audio - just clear current queue
              stopAllAudio();
            }
            // Note: We don't need to handle agent_started_speaking for audio
            // because we're not using a flag to block audio
          } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
          }
        } else if (event.data instanceof Blob) {
          // Audio data - play it
          playAudio(event.data);
        }
      };

      ws.onerror = () => {
        setError('Connection error');
      };

      ws.onclose = () => {
        setIsConnected(false);
        setIsListening(false);
      };

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect');
    }
  }, [projectId, authToken]);

  const stopMicrophone = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(track => track.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setIsListening(false);
  }, []);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'stop' }));
      }
      wsRef.current.close();
      wsRef.current = null;
    }
    stopMicrophone();

    // Stop any playing audio and close playback context
    audioQueueRef.current.forEach(source => {
      try { source.stop(); } catch { /* ignore */ }
    });
    audioQueueRef.current = [];
    nextPlayTimeRef.current = 0;
    if (playbackContextRef.current && playbackContextRef.current.state !== 'closed') {
      playbackContextRef.current.close();
      playbackContextRef.current = null;
    }

    setIsConnected(false);
    setIsListening(false);
  }, [stopMicrophone]);

  const startMicrophone = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        }
      });

      mediaStreamRef.current = stream;
      audioContextRef.current = new AudioContext({ sampleRate: 16000 });

      const source = audioContextRef.current.createMediaStreamSource(stream);
      // TODO: Migrate to AudioWorkletNode - ScriptProcessorNode is deprecated
      // See: https://developer.mozilla.org/en-US/docs/Web/API/ScriptProcessorNode
      const processor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          const pcmData = new Int16Array(inputData.length);

          for (let i = 0; i < inputData.length; i++) {
            pcmData[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
          }

          wsRef.current.send(pcmData.buffer);
        }
      };

      source.connect(processor);
      processor.connect(audioContextRef.current.destination);

      setIsListening(true);
    } catch (err) {
      setError('Microphone access denied');
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  return {
    isConnected,
    isListening,
    messages,
    error,
    connect,
    disconnect,
    startMicrophone,
    stopMicrophone,
  };
}
