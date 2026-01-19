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

  const playAudio = async (blob: Blob) => {
    const arrayBuffer = await blob.arrayBuffer();
    const audioContext = new AudioContext();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);
    source.start();
  };

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
            onTaskSubmitted?.(msg.prompt);
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
  }, [projectId, authToken, onTaskSubmitted]);

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
      wsRef.current.send(JSON.stringify({ type: 'stop' }));
      wsRef.current.close();
      wsRef.current = null;
    }
    stopMicrophone();
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
