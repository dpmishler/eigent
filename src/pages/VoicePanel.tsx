import React, { useEffect, useMemo } from 'react';
import { useVoiceSession } from '@/hooks/useVoiceSession';
import { Mic, MicOff, X, Maximize2, Minimize2 } from 'lucide-react';

export default function VoicePanel() {
  // Get project ID from URL hash params (passed from main window)
  const projectId = useMemo(() => {
    const hash = window.location.hash;
    const match = hash.match(/projectId=([^&]+)/);
    return match ? match[1] : null;
  }, []);

  const [isExpanded, setIsExpanded] = React.useState(false);

  const {
    isConnected,
    isListening,
    messages,
    error,
    connect,
    disconnect,
    startMicrophone,
    stopMicrophone,
  } = useVoiceSession({
    projectId: projectId || '',
    onTaskSubmitted: (prompt) => {
      // Could dispatch to chat store here
      console.log('Task submitted:', prompt);
    },
  });

  // Auto-connect when panel opens
  useEffect(() => {
    if (projectId) {
      connect().then(() => startMicrophone());
    }
    return () => disconnect();
  }, [projectId, connect, disconnect, startMicrophone]);

  const handleClose = () => {
    disconnect();
    window.ipcRenderer.invoke('voice-close-panel');
  };

  const handleToggleExpand = () => {
    if (isExpanded) {
      window.ipcRenderer.invoke('voice-pop-in');
    } else {
      window.ipcRenderer.invoke('voice-pop-out');
    }
    setIsExpanded(!isExpanded);
  };

  const handleToggleMic = () => {
    if (isListening) {
      stopMicrophone();
    } else {
      startMicrophone();
    }
  };

  // Get last few messages for compact view
  const recentMessages = messages.slice(-3);

  return (
    <div className="flex flex-col h-full bg-surface-primary rounded-xl border border-border-secondary overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface-secondary border-b border-border-secondary drag-region">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-sm font-medium text-text-primary">Voice Active</span>
        </div>
        <div className="flex items-center gap-1 no-drag">
          <button
            onClick={handleToggleExpand}
            className="p-1 hover:bg-surface-hover rounded"
          >
            {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          <button
            onClick={handleClose}
            className="p-1 hover:bg-surface-hover rounded"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {error && (
          <div className="text-red-500 text-sm">{error}</div>
        )}
        {recentMessages.map((msg) => (
          <div
            key={msg.id}
            className={`text-sm ${
              msg.type === 'user'
                ? 'text-text-secondary italic'
                : 'text-text-primary'
            }`}
          >
            {msg.type === 'user' ? 'You: ' : 'Agent: '}
            {msg.text}
          </div>
        ))}
      </div>

      {/* Controls */}
      <div className="flex items-center justify-center gap-2 p-3 border-t border-border-secondary">
        <button
          onClick={handleToggleMic}
          className={`p-3 rounded-full ${
            isListening
              ? 'bg-red-500 hover:bg-red-600'
              : 'bg-surface-secondary hover:bg-surface-hover'
          }`}
        >
          {isListening ? <MicOff size={20} /> : <Mic size={20} />}
        </button>
      </div>
    </div>
  );
}
