const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;
const FORMAT_EXT = {
  wav: 'wav',
  mp3: 'mp3',
  aac: 'm4a',
  mp4: 'mp4',
};

const state = {
  file: null,
  duration: null,
  originalUrl: null,
  enhancedUrl: null,
  mode: 'both',
  syncTimer: null,
  sizes: {},
};

const dom = {
  video: document.querySelector('.hero-video'),
  fallback: document.querySelector('.video-fallback'),
  particles: document.getElementById('particles'),
  dropZone: document.getElementById('dropZone'),
  dropZoneCopy: document.getElementById('dropZoneCopy'),
  fileInput: document.getElementById('fileInput'),
  fileMeta: document.getElementById('fileMeta'),
  enhanceButton: document.getElementById('enhanceButton'),
  results: document.getElementById('results'),
  originalPlayer: document.getElementById('originalPlayer'),
  enhancedPlayer: document.getElementById('enhancedPlayer'),
  originalDetails: document.getElementById('originalDetails'),
  enhancedDetails: document.getElementById('enhancedDetails'),
  abToggle: document.getElementById('abToggle'),
  downloadRow: document.getElementById('downloadRow'),
  toastStack: document.getElementById('toastStack'),
};

const downloadButtons = Array.from(document.querySelectorAll('.download-button'));

init();

function init() {
  setupVideoFallback();
  setupParticles();
  setupDropZone();
  setupButtons();
  setupToggle();
}

function setupVideoFallback() {
  if (!dom.video) {
    document.body.classList.add('no-video');
    return;
  }

  dom.video.addEventListener('error', () => {
    document.body.classList.add('no-video');
  });
}

function setupParticles() {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reducedMotion || !dom.particles) {
    return;
  }

  for (let index = 0; index < 20; index += 1) {
    const particle = document.createElement('div');
    particle.className = 'particle';

    const size = 4 + Math.random() * 10;
    const left = Math.random() * 100;
    const delay = (Math.random() * 8).toFixed(2);
    const duration = 10 + Math.random() * 10;
    const drift = ((Math.random() - 0.5) * 120).toFixed(0);

    particle.style.width = `${size}px`;
    particle.style.height = `${size}px`;
    particle.style.left = `${left}%`;
    particle.style.setProperty('--delay', `-${delay}s`);
    particle.style.setProperty('--duration', `${duration.toFixed(2)}s`);
    particle.style.setProperty('--drift', `${drift}px`);

    dom.particles.appendChild(particle);
  }
}

function setupDropZone() {
  ['dragenter', 'dragover'].forEach((eventName) => {
    dom.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dom.dropZone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    dom.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dom.dropZone.classList.remove('dragover');
    });
  });

  dom.dropZone.addEventListener('drop', (event) => {
    const [file] = event.dataTransfer.files;
    if (file) {
      handleFile(file);
    }
  });

  dom.dropZone.addEventListener('click', () => dom.fileInput.click());
  dom.dropZone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      dom.fileInput.click();
    }
  });

  dom.fileInput.addEventListener('change', () => {
    const [file] = dom.fileInput.files;
    if (file) {
      handleFile(file);
    }
  });
}

function setupButtons() {
  dom.enhanceButton.addEventListener('click', enhanceSelectedFile);

  downloadButtons.forEach((button) => {
    button.addEventListener('click', async () => {
      if (!state.file) {
        return;
      }
      const format = button.dataset.format;
      const response = await requestDownload(format, false);
      if (response) {
        const fileName = `studiovoice.${FORMAT_EXT[format]}`;
        triggerDownload(response.blob, fileName);
        updateDownloadSize(format, response.blob.size);
      }
    });
  });
}

function setupToggle() {
  dom.abToggle.addEventListener('click', () => {
    if (!state.originalUrl || !state.enhancedUrl) {
      return;
    }

    state.mode = state.mode === 'a' ? 'both' : state.mode === 'both' ? 'b' : 'a';
    applyPlaybackMode();
  });
}

async function handleFile(file) {
  const mime = file.type || '';
  const isAudio = mime.startsWith('audio/');
  const isMp4 = mime === 'video/mp4' || (file.name || '').toLowerCase().endsWith('.mp4');

  if (!isAudio && !isMp4) {
    showToast('Please choose an audio file or an MP4 video.');
    return;
  }

  if (file.size > MAX_UPLOAD_BYTES) {
    showToast('File is too large. Please choose a file under 200 MB.');
    return;
  }

  state.file = file;
  state.duration = await readDuration(file);

  const sizeLabel = formatBytes(file.size);
  const durationLabel = Number.isFinite(state.duration) ? formatDuration(state.duration) : 'Unknown duration';
  dom.fileMeta.textContent = `${file.name} • ${sizeLabel} • ${durationLabel}`;
  dom.enhanceButton.disabled = false;
}

function readDuration(file) {
  return new Promise((resolve) => {
    const objectUrl = URL.createObjectURL(file);
    const audio = new Audio(objectUrl);

    audio.addEventListener('loadedmetadata', () => {
      const duration = audio.duration;
      URL.revokeObjectURL(objectUrl);
      resolve(duration);
    });

    audio.addEventListener('error', () => {
      URL.revokeObjectURL(objectUrl);
      resolve(null);
    });
  });
}

async function enhanceSelectedFile() {
  if (!state.file) {
    showToast('Choose an audio file first.');
    return;
  }

  setProcessing(true);
  try {
    showResultsShell();
    const response = await requestEnhancement('wav');
    if (!response) {
      return;
    }

    setPlayerSource(dom.originalPlayer, ensureOriginalObjectUrl());
    state.enhancedUrl = URL.createObjectURL(response.blob);
    setPlayerSource(dom.enhancedPlayer, state.enhancedUrl);

    dom.originalDetails.textContent = `${formatBytes(state.file.size)} • ${formatDuration(state.duration)}`;
    dom.enhancedDetails.textContent = `${formatBytes(response.blob.size)} • WAV`;

    dom.downloadRow.hidden = false;
    updateDownloadSize('wav', response.blob.size);
    dom.abToggle.textContent = 'Play both';
    state.mode = 'both';
    applyPlaybackMode(true);
  } catch (error) {
    showToast(error.message || 'Something went wrong.');
  } finally {
    setProcessing(false);
  }
}

async function requestEnhancement(format) {
  const formData = buildFormData();
  const response = await fetch(`/api/enhance${format ? `?format=${format}` : ''}`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  return { blob: await response.blob() };
}

async function requestDownload(format) {
  const formData = buildFormData();
  const response = await fetch(`/api/enhance?format=${format}`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    showToast(await readErrorMessage(response));
    return null;
  }

  return { blob: await response.blob() };
}

function buildFormData() {
  const formData = new FormData();
  formData.append('file', state.file, state.file.name);
  return formData;
}

function setProcessing(isProcessing) {
  dom.enhanceButton.disabled = isProcessing;
  if (isProcessing) {
    dom.enhanceButton.innerHTML = '<span class="button-loader" aria-hidden="true"><span></span><span></span><span></span></span>';
  } else {
    dom.enhanceButton.innerHTML = '<span class="button-label">Enhance with Studio Voice</span>';
  }
}

function showResultsShell() {
  dom.results.hidden = false;
}

function ensureOriginalObjectUrl() {
  if (!state.originalUrl) {
    state.originalUrl = URL.createObjectURL(state.file);
  }
  return state.originalUrl;
}

function setPlayerSource(player, url) {
  player.src = url;
  player.load();
}

function applyPlaybackMode(resetTimes = false) {
  clearSyncTimer();

  if (!state.originalUrl || !state.enhancedUrl) {
    return;
  }

  if (state.mode === 'a') {
    dom.abToggle.textContent = 'Play Enhanced only';
    dom.enhancedPlayer.pause();
    if (resetTimes) {
      dom.enhancedPlayer.currentTime = dom.originalPlayer.currentTime;
    }
    dom.originalPlayer.play();
    return;
  }

  if (state.mode === 'b') {
    dom.abToggle.textContent = 'Play A only';
    dom.originalPlayer.pause();
    if (resetTimes) {
      dom.originalPlayer.currentTime = dom.enhancedPlayer.currentTime;
    }
    dom.enhancedPlayer.play();
    return;
  }

  dom.abToggle.textContent = 'Play both';
  if (resetTimes) {
    const startTime = Math.min(dom.originalPlayer.currentTime || 0, dom.enhancedPlayer.currentTime || 0);
    dom.originalPlayer.currentTime = startTime;
    dom.enhancedPlayer.currentTime = startTime;
  }
  Promise.all([dom.originalPlayer.play(), dom.enhancedPlayer.play()]).catch(() => {});
  state.syncTimer = setInterval(() => {
    const time = dom.originalPlayer.currentTime;
    if (Number.isFinite(time) && Math.abs(dom.enhancedPlayer.currentTime - time) > 0.05) {
      dom.enhancedPlayer.currentTime = time;
    }
  }, 100);
}

function clearSyncTimer() {
  if (state.syncTimer) {
    clearInterval(state.syncTimer);
    state.syncTimer = null;
  }
}

function updateDownloadSize(format, size) {
  state.sizes[format] = size;
  const sizeSlot = document.getElementById(`size-${format}`);
  if (sizeSlot) {
    sizeSlot.textContent = formatBytes(size);
  }
}

function triggerDownload(blob, fileName) {
  const downloadUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = downloadUrl;
  anchor.download = fileName;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
}

async function readErrorMessage(response) {
  try {
    const data = await response.json();
    return data.detail || 'Request failed.';
  } catch {
    return 'Request failed.';
  }
}

function showToast(message) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  dom.toastStack.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) {
    return '0 B';
  }

  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) {
    return 'Unknown duration';
  }

  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
}
