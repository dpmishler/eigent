import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { app } from 'electron';
import log from 'electron-log';
import * as http from 'http';
import { findAvailablePort } from './init';
import { getBinaryPath, getVenvPath, getUvEnv } from './utils/process';

let voiceProcess: ChildProcess | null = null;
let voicePort: number | null = null;

/**
 * Get the path to the voice service directory
 */
function getVoicePath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'voice');
  } else {
    return path.join(app.getAppPath(), 'voice');
  }
}

/**
 * Start the voice service as a child process
 * @returns The port number the voice service is running on
 */
export async function startVoiceService(): Promise<number> {
  log.info('[VOICE SERVICE] Starting voice service...');

  // Find available port starting from 5002
  voicePort = await findAvailablePort(5002);
  log.info(`[VOICE SERVICE] Found available port: ${voicePort}`);

  const voicePath = getVoicePath();
  const uvPath = await getBinaryPath('uv');
  const currentVersion = app.getVersion();
  const uvEnv = getUvEnv(currentVersion);

  log.info(`[VOICE SERVICE] Voice path: ${voicePath}`);
  log.info(`[VOICE SERVICE] UV path: ${uvPath}`);

  const env = {
    ...process.env,
    ...uvEnv,
    VOICE_SERVICE_PORT: String(voicePort),
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
  };

  return new Promise((resolve, reject) => {
    voiceProcess = spawn(
      uvPath,
      ['run', 'python', 'main.py'],
      {
        cwd: voicePath,
        env: env,
        detached: process.platform !== 'win32',
        stdio: ['ignore', 'pipe', 'pipe'],
      }
    );

    log.info(`[VOICE SERVICE] Process spawned with PID: ${voiceProcess.pid}`);

    let started = false;
    let healthCheckInterval: NodeJS.Timeout | null = null;

    const startTimeout = setTimeout(() => {
      if (!started) {
        if (healthCheckInterval) clearInterval(healthCheckInterval);
        killVoiceProcess();
        reject(new Error('Voice service failed to start within timeout'));
      }
    }, 30000);

    const initialDelay = setTimeout(() => {
      if (!started) {
        log.info('[VOICE SERVICE] Starting health check polling...');
        pollHealthEndpoint();
      }
    }, 1000);

    const pollHealthEndpoint = (): void => {
      let attempts = 0;
      const maxAttempts = 120;
      const intervalMs = 250;

      healthCheckInterval = setInterval(() => {
        attempts++;
        const healthUrl = `http://127.0.0.1:${voicePort}/health`;

        const req = http.get(healthUrl, { timeout: 1000 }, (res) => {
          if (res.statusCode === 200) {
            log.info(`[VOICE SERVICE] Health check passed after ${attempts} attempts`);
            started = true;
            clearTimeout(startTimeout);
            clearTimeout(initialDelay);
            if (healthCheckInterval) clearInterval(healthCheckInterval);
            resolve(voicePort!);
          } else if (attempts >= maxAttempts) {
            log.error(`[VOICE SERVICE] Health check failed with status ${res.statusCode}`);
            started = true;
            clearTimeout(startTimeout);
            clearTimeout(initialDelay);
            if (healthCheckInterval) clearInterval(healthCheckInterval);
            killVoiceProcess();
            reject(new Error(`Voice service health check failed: HTTP ${res.statusCode}`));
          }
        });

        req.on('error', () => {
          if (attempts >= maxAttempts) {
            log.error(`[VOICE SERVICE] Health check failed after ${attempts} attempts`);
            started = true;
            clearTimeout(startTimeout);
            clearTimeout(initialDelay);
            if (healthCheckInterval) clearInterval(healthCheckInterval);
            killVoiceProcess();
            reject(new Error('Voice service health check failed: unable to connect'));
          }
        });

        req.on('timeout', () => {
          req.destroy();
        });
      }, intervalMs);
    };

    voiceProcess.stdout?.on('data', (data) => {
      const msg = data.toString().trimEnd();
      log.info(`[VOICE SERVICE] ${msg}`);
    });

    voiceProcess.stderr?.on('data', (data) => {
      const msg = data.toString().trimEnd();
      if (msg.toLowerCase().includes('error') || msg.toLowerCase().includes('traceback')) {
        log.error(`[VOICE SERVICE] ${msg}`);
      } else {
        log.info(`[VOICE SERVICE] ${msg}`);
      }

      if (msg.includes('Address already in use') || msg.includes('bind() failed')) {
        if (!started) {
          started = true;
          clearTimeout(startTimeout);
          clearTimeout(initialDelay);
          if (healthCheckInterval) clearInterval(healthCheckInterval);
          killVoiceProcess();
          reject(new Error(`Voice service port ${voicePort} is already in use`));
        }
      }
    });

    voiceProcess.on('error', (err) => {
      log.error(`[VOICE SERVICE] Process error: ${err.message}`);
      if (!started) {
        started = true;
        clearTimeout(startTimeout);
        clearTimeout(initialDelay);
        if (healthCheckInterval) clearInterval(healthCheckInterval);
        reject(new Error(`Failed to spawn voice service: ${err.message}`));
      }
    });

    voiceProcess.on('close', (code, signal) => {
      log.info(`[VOICE SERVICE] Process closed with code ${code}, signal ${signal}`);
      clearTimeout(startTimeout);
      clearTimeout(initialDelay);
      if (healthCheckInterval) clearInterval(healthCheckInterval);

      if (!started) {
        reject(new Error(`Voice service exited prematurely with code ${code}`));
      }
    });
  });
}

/**
 * Kill the voice process and its children
 */
function killVoiceProcess(): void {
  if (!voiceProcess || !voiceProcess.pid) return;

  log.info(`[VOICE SERVICE] Killing process ${voiceProcess.pid}...`);
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', voiceProcess.pid.toString(), '/T', '/F']);
    } else {
      try {
        process.kill(-voiceProcess.pid, 'SIGTERM');
        setTimeout(() => {
          try {
            process.kill(-voiceProcess!.pid!, 'SIGKILL');
          } catch (e) {
            // Process already gone
          }
        }, 1000);
      } catch (e) {
        voiceProcess.kill('SIGKILL');
      }
    }
  } catch (e) {
    log.error(`[VOICE SERVICE] Failed to kill process: ${e}`);
  }
}

/**
 * Stop the voice service
 */
export function stopVoiceService(): void {
  log.info('[VOICE SERVICE] Stopping voice service...');

  if (voiceProcess) {
    voiceProcess.removeAllListeners();
    killVoiceProcess();
    voiceProcess = null;
  }

  voicePort = null;
  log.info('[VOICE SERVICE] Voice service stopped');
}

/**
 * Get the current voice service port
 * @returns The port number or null if not running
 */
export function getVoicePort(): number | null {
  return voicePort;
}

/**
 * Check if the voice service is running
 * @returns True if the voice service is running
 */
export function isVoiceServiceRunning(): boolean {
  return voiceProcess !== null && !voiceProcess.killed;
}
