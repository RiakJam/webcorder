const $ = (id) => document.getElementById(id);
const state = { source: 'screen', viewer: 'preview', camera: localStorage.getItem('webcorder-camera') || '', cameraFirst: localStorage.getItem('webcorder-camera-first') === 'true', status: null, busy: false, previewConnected: false, cameraConnected: false, localUrl: null, localSize: null, webUrl: null, webKind: null, webSize: null, installPrompt: null };

async function request(path, body) {
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function note(message, bad = false) { $('message').textContent = message; $('message').style.color = bad ? '#ffb1b1' : ''; }
function clock(seconds) { return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`; }
function resizePlayer() {
  if (state.source === 'screen') return;
  const source = state.source === 'local' ? state.localSize : state.webSize;
  const width = source?.width || 1280;
  const height = source?.height || 720;
  const stageRatio = width / (height * 2);
  const panel = document.querySelector('.main-panel');
  const style = getComputedStyle(panel);
  const availableWidth = panel.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const stage = $('playerStage');
  const transport = document.querySelector('.transport');
  const bottom = document.querySelector('.bottom-line');
  const availableHeight = Math.max(120, window.innerHeight - stage.getBoundingClientRect().top
    - transport.getBoundingClientRect().height - bottom.getBoundingClientRect().height
    - parseFloat(style.paddingBottom) - 58);
  const naturalWidth = Math.max(160, Math.min(width, 760));
  const fittedWidth = Math.min(naturalWidth, availableWidth, availableHeight * stageRatio);
  const playerWidth = Math.max(80, fittedWidth);
  stage.style.setProperty('--normal-width', `${Math.floor(playerWidth)}px`);
  stage.style.setProperty('--normal-aspect', `${width} / ${height * 2}`);
  stage.style.setProperty('--reaction-stage-ratio', String(stageRatio));
  stage.style.setProperty('--source-width', `${Math.max(160, width)}px`);
}
async function setSource(source) {
  if (state.busy) return false;
  if (source !== state.source && state.status?.mode === 'record') {
    note('Stop the recording before changing sources.', true);
    return false;
  }
  if (source !== state.source && state.status?.mode === 'preview') {
    state.busy = true;
    try { updateStatus(await request('/api/stop', {})); }
    catch (error) { note(error.message, true); state.busy = false; return false; }
    state.busy = false;
  }
  if (state.source === 'web' && source !== 'web') {
    $('webFrame').src = 'about:blank';
    $('webVideoPlayer').pause();
    $('webVideoPlayer').removeAttribute('src');
    $('webVideoPlayer').load();
    state.webUrl = null; state.webKind = null; state.webSize = null;
    $('webLinkState').textContent = 'No web video loaded';
    $('externalLinkBtn').hidden = true;
  }
  state.source = source;
  document.body.classList.toggle('reaction-mode', source !== 'screen');
  document.body.classList.remove('show-settings');
  $('settingsBtn').setAttribute('aria-expanded', 'false');
  $('workspaceTitle').textContent = source === 'screen' ? 'Screen recorder' : 'Reaction studio';
  document.querySelectorAll('.source-tab').forEach(button => { const active = button.dataset.mode === source; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); });
  for (const mode of ['screen','local','web']) $(`${mode}Options`).hidden = mode !== source;
  $('previewTab').hidden = source !== 'screen';
  $('playerTab').hidden = source === 'screen';
  $('localPlayer').hidden = source !== 'local' || !state.localUrl;
  $('webVideoPlayer').hidden = source !== 'web' || state.webKind !== 'video';
  $('webFrame').hidden = source !== 'web' || state.webKind !== 'frame';
  if (source !== 'local') $('localPlayer').pause();
  if (source !== 'web') $('webVideoPlayer').pause();
  $('playerEmpty').hidden = source === 'local' ? !!state.localUrl : source === 'web' ? !$('webFrame').hidden || !$('webVideoPlayer').hidden : false;
  $('playerEmpty').querySelector('p').textContent = source === 'web' ? 'Paste a link' : 'Choose a file';
  setViewer(source === 'screen' ? 'preview' : 'player');
  if (state.status) updateStatus(state.status);
  resizePlayer();
  if (source !== 'screen' && $('cameraSelect').value && state.status?.mode === 'idle') await begin('preview');
  return true;
}
function setViewer(viewer) {
  state.viewer = viewer;
  $('previewStage').hidden = viewer !== 'preview';
  $('playerStage').hidden = viewer !== 'player';
  for (const mode of ['preview','player']) { const button = $(`${mode}Tab`); const active = mode === viewer; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); }
}
async function refreshCameras() {
  const previous = $('cameraSelect').value || state.camera;
  const data = await request('/api/cameras');
  const options = [new Option('No camera', '')];
  for (const camera of data.cameras) options.push(new Option(camera.name, camera.name));
  if (previous && !data.cameras.some(camera => camera.name === previous)) options.push(new Option(`${previous} (waiting for USB)`, previous));
  $('cameraSelect').replaceChildren(...options);
  $('cameraSelect').value = previous;
  state.camera = previous;
  $('cameraState').textContent = previous ? (data.cameras.some(camera => camera.name === previous) ? 'Camera ready.' : 'Waiting for USB camera.') : 'No camera selected.';
}
function selectedSettings(mode) {
  const settings = { mode, source: state.source, camera: $('cameraSelect').value, background: $('backgroundSelect').value,
    mic: $('micToggle').checked, desktopAudio: $('audioToggle').checked, fps: Number($('fpsSelect').value) };
  if (mode === 'record' && state.source !== 'screen') {
    const video = $('playerMedia').getBoundingClientRect();
    const camera = $('cameraDock').getBoundingClientRect();
    const left = Math.min(video.left, camera.left);
    const top = Math.min(video.top, camera.top);
    const right = Math.max(video.right, camera.right);
    const bottom = Math.max(video.bottom, camera.bottom);
    const scale = window.devicePixelRatio || 1;
    const screenX = window.screenX + (document.fullscreenElement ? 0 : (window.outerWidth - window.innerWidth) / 2);
    const screenY = window.screenY + (document.fullscreenElement ? 0 : window.outerHeight - window.innerHeight);
    settings.captureRect = {x: Math.round((screenX + left) * scale), y: Math.round((screenY + top) * scale),
      width: Math.floor((right-left)*scale/2)*2, height: Math.floor((bottom-top)*scale/2)*2};
  }
  return settings;
}
function connectPreview() {
  if (state.previewConnected) return;
  state.previewConnected = true;
  $('previewImage').src = `/api/preview?${Date.now()}`;
  $('previewImage').hidden = false;
  $('previewEmpty').hidden = true;
  $('cameraDragHint').hidden = !$('cameraSelect').value;
}
function connectCamera() {
  if (state.cameraConnected) return;
  state.cameraConnected = true;
  $('cameraBubbleImage').src = `/api/camera-preview?${Date.now()}`;
}
async function begin(mode) {
  if (state.busy) return;
  state.busy = true;
  note(mode === 'record' ? 'Starting recording…' : 'Starting preview…');
  try {
    if (mode === 'record' && state.source !== 'screen' && document.fullscreenElement !== $('playerStage')) {
      await $('playerStage').requestFullscreen();
    }
    const data = await request('/api/start', selectedSettings(mode));
    updateStatus(data);
    if (state.source !== 'screen') {
      setViewer('player');
      if ($('cameraSelect').value) connectCamera();
      if (state.source === 'local' && $('localPlayer').src && $('localPlayer').paused) await $('localPlayer').play().catch(() => {});
    }
    else setViewer('preview');
    note(mode === 'record' ? (state.source === 'screen' ? 'Recording desktop.' : 'Recording player.') : 'Preview on · no file saved.');
  } catch (error) {
    if (mode === 'record' && state.source !== 'screen' && document.fullscreenElement === $('playerStage')) await document.exitFullscreen().catch(() => {});
    note(error.message, true);
  }
  finally { state.busy = false; if (state.status) updateStatus(state.status); }
}
async function stop() {
  if (state.busy) return;
  const wasRecording = state.status?.mode === 'record';
  state.busy = true; note(wasRecording ? 'Finalizing MP4…' : 'Stopping preview…');
  try {
    const data = await request('/api/stop', {});
    updateStatus(data);
    if (wasRecording && state.source !== 'screen' && document.fullscreenElement === $('playerStage')) await document.exitFullscreen();
    const message = wasRecording && data.output ? `Saved: ${data.output}` : 'Preview stopped.';
    if (wasRecording && state.source !== 'screen' && $('cameraSelect').value) {
      updateStatus(await request('/api/start', selectedSettings('preview')));
    }
    note(message);
  }
  catch (error) { note(error.message, true); }
  finally { state.busy = false; if (state.status) updateStatus(state.status); }
}
function updateStatus(data) {
  state.status = data;
  const active = data.mode === 'preview' || data.mode === 'record';
  $('timer').textContent = data.mode === 'record' ? clock(data.elapsed || 0) : '—';
  $('liveBadge').innerHTML = `<span></span> ${data.mode === 'record' ? 'RECORDING' : data.mode === 'preview' ? 'PREVIEW ONLY' : 'READY'}`;
  $('liveBadge').classList.toggle('recording', data.mode === 'record');
  $('recordingState').textContent = data.mode === 'record' ? 'Recording in progress' : data.mode === 'preview' ? 'No file being saved' : 'Ready to record';
  $('stopBtn').textContent = data.mode === 'record' ? 'Stop & save' : 'Stop preview';
  $('recordBtn').disabled = data.mode === 'record' || state.busy;
  $('playerStopBtn').hidden = data.mode !== 'record' || state.source === 'screen';
  $('fullscreenBtn').hidden = state.source === 'screen' || data.mode === 'record' || document.fullscreenElement === $('playerStage');
  $('stopBtn').disabled = !active || state.busy;
  $('previewBtn').disabled = data.mode === 'preview' || data.mode === 'record' || state.busy;
  if (active && state.source === 'screen') connectPreview();
  const showCamera = active && state.source !== 'screen' && !!$('cameraSelect').value;
  $('cameraBubble').hidden = !showCamera;
  $('cameraDockText').hidden = showCamera;
  $('cameraDockText').textContent = $('cameraSelect').value ? 'Camera off · Preview' : 'Select camera';
  if (showCamera) connectCamera();
  if (data.cameraState === 'disconnected' || data.cameraState === 'waiting') {
    $('cameraState').textContent = 'Camera disconnected · waiting for USB.';
  } else if (data.cameraState === 'connected') {
    $('cameraState').textContent = 'Camera connected.';
  }
  if (data.error) note(data.error, true);
  if (data.latestFile) {
    $('downloadLink').href = `/recordings/${encodeURIComponent(data.latestFile)}`;
    $('downloadLink').hidden = false;
  }
}
async function chooseLocal(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (state.localUrl) URL.revokeObjectURL(state.localUrl);
  state.localUrl = URL.createObjectURL(file);
  state.localSize = null;
  $('localPlayer').src = state.localUrl;
  $('localChosen').textContent = file.name;
  $('playerEmpty').hidden = true;
  $('localPlayer').hidden = false;
  if (!await setSource('local')) return;
  $('localPlayer').play().catch(() => note('Press play to start the local video.'));
}
function openWebLink() {
  const raw = $('webUrl').value.trim();
  let url;
  try { url = new URL(raw); if (!['http:', 'https:'].includes(url.protocol)) throw new Error(); }
  catch { note('Enter a complete http:// or https:// video link.', true); return; }
  const hostname = url.hostname.toLowerCase();
  state.webUrl = url.toString();
  state.webSize = {width:1280, height:720};
  $('webFrame').src = 'about:blank'; $('webFrame').hidden = true;
  $('webVideoPlayer').pause(); $('webVideoPlayer').removeAttribute('src'); $('webVideoPlayer').load(); $('webVideoPlayer').hidden = true;
  state.webKind = null;
  $('externalLinkBtn').hidden = true;
  if (hostname === 'netflix.com' || hostname.endsWith('.netflix.com')) {
    $('webLinkState').textContent = 'Netflix links are unsupported in the embedded player.';
    $('playerEmpty').hidden = false;
    $('playerEmpty').querySelector('p').textContent = 'This link cannot play inside Webcorder. Use its own tab if you want to record the visible desktop.';
    $('externalLinkBtn').hidden = false;
    note('This site blocks in-app playback. Webcorder will not open it automatically.', true);
    resizePlayer();
    return;
  }
  let embed = null;
  if (hostname === 'youtu.be') {
    const id = url.pathname.split('/').filter(Boolean)[0];
    if (/^[\w-]{11}$/.test(id || '')) embed = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&fs=0`;
  } else if (['youtube.com','www.youtube.com','m.youtube.com','www.youtube-nocookie.com'].includes(hostname)) {
    if (url.pathname.startsWith('/shorts/')) state.webSize = {width:720, height:1280};
    const id = url.searchParams.get('v') || url.pathname.match(/^\/(?:shorts|live|embed)\/([\w-]{11})/)?.[1];
    if (/^[\w-]{11}$/.test(id || '')) embed = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&fs=0`;
  } else if (hostname === 'vimeo.com' || hostname === 'www.vimeo.com' || hostname === 'player.vimeo.com') {
    const id = url.pathname.match(/\/(\d+)(?:\/|$)/)?.[1];
    if (id) embed = `https://player.vimeo.com/video/${id}?autoplay=1&fullscreen=0`;
  }
  if (/\.(?:mp4|webm|ogg)(?:$|\?)/i.test(url.pathname + url.search)) {
    state.webKind = 'video';
    $('webVideoPlayer').src = url.toString();
    $('webVideoPlayer').hidden = false;
    $('webVideoPlayer').play().catch(() => {});
    $('webLinkState').textContent = 'Video ready';
  } else {
    state.webKind = 'frame';
    $('webFrame').src = embed || url.toString();
    $('webFrame').hidden = false;
    $('webLinkState').textContent = embed ? 'Video ready' : 'Page ready';
    $('externalLinkBtn').hidden = false;
  }
  $('playerEmpty').hidden = true;
  setViewer('player');
  resizePlayer();
  note('Video ready.');
}
function openExternalLink() {
  if (!state.webUrl) return;
  const opened = window.open(state.webUrl, '_blank');
  if (opened) opened.opener = null;
  else note('The browser blocked the new tab.', true);
}
async function uploadBackground(event) {
  const file = event.target.files?.[0]; if (!file) return;
  try {
    const response = await fetch('/api/background', {method:'POST', body:file, headers:{'Content-Type':file.type}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Upload failed.');
    const option = new Option(file.name, data.id, true, true);
    $('backgroundSelect').add(option);
    $('backgroundSelect').value = data.id;
    note('Custom camera background selected.');
  } catch(error) { note(error.message, true); }
}
function selectBackground(value) {
  $('backgroundSelect').value = value;
  document.querySelectorAll('.background-thumb').forEach(button => button.classList.toggle('active', button.dataset.background === value));
  if (state.source !== 'screen' && state.status?.mode === 'preview' && $('cameraSelect').value) begin('preview');
}
async function poll() {
  try { const status = await request('/api/status'); updateStatus(status); $('serverStatus').textContent = '● LOCAL APP'; }
  catch { $('serverStatus').textContent = '● SERVER OFFLINE'; note('Webcorder service is offline. Start the installed launcher.',true); }
}
function setupInstall() {
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); state.installPrompt = event; $('installBtn').hidden = false; });
  $('installBtn').addEventListener('click', async () => { if (state.installPrompt) { await state.installPrompt.prompt(); state.installPrompt = null; $('installBtn').hidden = true; } });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
}
function bind() {
  $('settingsBtn').addEventListener('click', () => {
    const shown = document.body.classList.toggle('show-settings');
    $('settingsBtn').setAttribute('aria-expanded', String(shown));
  });
  document.querySelectorAll('.source-tab').forEach(button => button.addEventListener('click', () => setSource(button.dataset.mode).catch(e => note(e.message, true))));
  $('chooseScreenBtn').addEventListener('click', async () => { if (await setSource('screen')) begin('preview'); });
  $('localFile').addEventListener('change', chooseLocal);
  $('localPlayer').addEventListener('loadedmetadata', () => {
    if (!$('localPlayer').videoWidth || !$('localPlayer').videoHeight) return;
    state.localSize = {width:$('localPlayer').videoWidth, height:$('localPlayer').videoHeight};
    resizePlayer();
  });
  $('webVideoPlayer').addEventListener('loadedmetadata', () => {
    if (!$('webVideoPlayer').videoWidth || !$('webVideoPlayer').videoHeight) return;
    state.webSize = {width:$('webVideoPlayer').videoWidth, height:$('webVideoPlayer').videoHeight};
    resizePlayer();
  });
  window.addEventListener('resize', resizePlayer);
  $('openLinkBtn').addEventListener('click', openWebLink);
  $('externalLinkBtn').addEventListener('click', openExternalLink);
  $('webVideoPlayer').addEventListener('error', () => {
    if (state.source !== 'web' || state.webKind !== 'video') return;
    $('webLinkState').textContent = 'This direct video could not be played here.';
    $('externalLinkBtn').hidden = false;
    note('The video format or server is unavailable in this browser.', true);
  });
  $('cameraSelect').addEventListener('change', () => {
    state.camera = $('cameraSelect').value;
    localStorage.setItem('webcorder-camera', state.camera);
    if (state.source !== 'screen' && state.status?.mode !== 'record') begin('preview');
    else note('Camera selected. Restart preview to apply the change.');
  });
  $('refreshCamerasBtn').addEventListener('click', () => refreshCameras().catch(e => note(e.message,true)));
  $('customBackground').addEventListener('change', uploadBackground);
  $('backgroundSelect').addEventListener('change', () => selectBackground($('backgroundSelect').value));
  document.querySelectorAll('.background-thumb').forEach(button => button.addEventListener('click', () => selectBackground(button.dataset.background)));
  $('previewTab').addEventListener('click', () => setViewer('preview'));
  $('playerTab').addEventListener('click', () => setViewer('player'));
  $('previewBtn').addEventListener('click', () => begin('preview'));
  $('recordBtn').addEventListener('click', () => begin('record'));
  $('stopBtn').addEventListener('click', stop);
  $('playerStopBtn').addEventListener('click', stop);
  $('helpBtn').addEventListener('click', () => $('helpDialog').showModal());
  $('closeHelpBtn').addEventListener('click', () => $('helpDialog').close());
  $('previewImage').addEventListener('error', () => { state.previewConnected = false; setTimeout(() => { if (state.status?.mode !== 'idle') connectPreview(); },1000); });
  $('cameraBubbleImage').addEventListener('error', () => { state.cameraConnected = false; setTimeout(() => { if (state.status?.mode !== 'idle' && state.source !== 'screen') connectCamera(); },1000); });
  $('fullscreenBtn').addEventListener('click', async () => {
    try {
      await $('playerStage').requestFullscreen();
    } catch (error) { note(`Fullscreen unavailable: ${error.message}`, true); }
  });
  $('exitFullscreenBtn').addEventListener('click', () => document.exitFullscreen().catch(e => note(e.message, true)));
  $('cameraOrderBtn').addEventListener('click', () => {
    state.cameraFirst = !state.cameraFirst;
    localStorage.setItem('webcorder-camera-first', String(state.cameraFirst));
    $('playerStage').classList.toggle('camera-first', state.cameraFirst);
    $('cameraOrderBtn').textContent = state.cameraFirst ? '↓ Camera below video' : '↑ Camera on top';
  });
  document.addEventListener('fullscreenchange', () => {
    $('exitFullscreenBtn').hidden = document.fullscreenElement !== $('playerStage');
    if (state.status) updateStatus(state.status);
    if (!document.fullscreenElement && state.source !== 'screen' && state.status?.mode === 'record' && !state.busy) stop();
  });
}
bind(); $('playerTab').hidden = true; $('playerStage').classList.toggle('camera-first', state.cameraFirst); $('cameraOrderBtn').textContent = state.cameraFirst ? '↓ Camera below video' : '↑ Camera on top'; setupInstall(); refreshCameras().catch(e => note(e.message,true)); poll(); setInterval(poll, 1000); setInterval(() => refreshCameras().catch(()=>{}), 7000);
