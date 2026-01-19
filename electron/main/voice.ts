import { ipcMain, BrowserWindow, globalShortcut, screen } from 'electron';

let voiceServicePort = 5002;
let activeVoiceWindow: BrowserWindow | null = null;
let mainWindowRef: BrowserWindow | null = null;

// Helper function to open the voice panel
function openVoicePanel() {
  if (activeVoiceWindow) {
    activeVoiceWindow.focus();
    return;
  }

  if (!mainWindowRef) {
    console.warn('Voice panel: main window reference not set');
    return;
  }

  activeVoiceWindow = new BrowserWindow({
    width: 320,
    height: 200,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: true,
    minimizable: false,
    maximizable: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: mainWindowRef.webContents.getWebPreferences().preload,
    },
  });

  // Position in bottom-right corner
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;
  activeVoiceWindow.setPosition(width - 340, height - 220);

  // Load voice panel UI
  if (process.env.VITE_DEV_SERVER_URL) {
    activeVoiceWindow.loadURL(
      `${process.env.VITE_DEV_SERVER_URL}#/voice-panel`
    );
  } else {
    activeVoiceWindow.loadFile('dist/index.html', { hash: '/voice-panel' });
  }

  activeVoiceWindow.on('closed', () => {
    activeVoiceWindow = null;
  });
}

export function setupVoiceHandlers(mainWindow: BrowserWindow) {
  // Store reference to main window for use in helper function
  mainWindowRef = mainWindow;

  // Set voice service port
  ipcMain.handle('voice-set-port', (_, port: number) => {
    voiceServicePort = port;
    return true;
  });

  // Get voice service URL
  ipcMain.handle('voice-get-url', () => {
    return `ws://localhost:${voiceServicePort}/voice/stream`;
  });

  // Create floating voice panel window
  ipcMain.handle('voice-open-panel', () => {
    openVoicePanel();
  });

  // Close voice panel
  ipcMain.handle('voice-close-panel', () => {
    if (activeVoiceWindow) {
      activeVoiceWindow.close();
      activeVoiceWindow = null;
    }
  });

  // Pop out to separate window
  ipcMain.handle('voice-pop-out', () => {
    if (activeVoiceWindow) {
      activeVoiceWindow.setSize(400, 500);
      activeVoiceWindow.setAlwaysOnTop(false);
      activeVoiceWindow.setResizable(true);
      activeVoiceWindow.center();
    }
  });

  // Pop back to floating overlay
  ipcMain.handle('voice-pop-in', () => {
    if (activeVoiceWindow) {
      const display = screen.getPrimaryDisplay();
      const { width, height } = display.workAreaSize;

      activeVoiceWindow.setSize(320, 200);
      activeVoiceWindow.setAlwaysOnTop(true);
      activeVoiceWindow.setPosition(width - 340, height - 220);
    }
  });

  // Register global shortcut for voice toggle
  const registered = globalShortcut.register(
    'CommandOrControl+Shift+V',
    () => {
      if (activeVoiceWindow) {
        activeVoiceWindow.close();
      } else {
        openVoicePanel();
      }
    }
  );

  if (!registered) {
    console.warn(
      'Failed to register voice toggle shortcut - may be in use by another application'
    );
  }
}

// Cleanup function to unregister shortcuts and close voice window
export function cleanupVoiceHandlers() {
  globalShortcut.unregister('CommandOrControl+Shift+V');
  if (activeVoiceWindow) {
    activeVoiceWindow.close();
    activeVoiceWindow = null;
  }
  mainWindowRef = null;
}
