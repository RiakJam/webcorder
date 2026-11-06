const $ = (id) => document.getElementById(id);
const state = { source: 'screen', viewer: 'preview', camera: localStorage.getItem('webcorder-camera') || '', cameraFirst: localStorage.getItem('webcorder-camera-first') === 'true', status: null, busy: false, previewConnected: false, cameraConnected: false, localUrl: null, localSize: null, webUrl: null, webKind: null, webSize: null, embedPlayer: null, embedReady: false, loadId: 0, installPrompt: null };
const mediaVolume = Math.min(100, Math.max(0, Number(localStorage.getItem('webcorder-media-volume') ?? 35) || 0));
let playerSdkPromises = {};
function loadPlayerSdk(name, url, ready) {
  if (ready()) return Promise.resolve();
  if (!playerSdkPromises[name]) playerSdkPromises[name] = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = url; script.onload = () => {
      if (name !== 'YouTube') resolve();
      else if (ready()) resolve();
    }; script.onerror = () => reject(new Error(`${name} player controls could not load.`));
    if (name === 'YouTube') window.onYouTubeIframeAPIReady = resolve;
    document.head.append(script);
  });
  return playerSdkPromises[name];
}
function activeMedia() {
  if (state.source === 'local' && state.localUrl) return $('localPlayer');
  if (state.source === 'web' && state.webKind === 'video') return $('webVideoPlayer');
  return null;
}
function mediaClock(seconds) {
  if (!Number.isFinite(seconds)) return '0:00';
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}
function updateMediaControls() {
  const native = activeMedia();
  const embedded = state.source === 'web' && state.embedReady ? state.embedPlayer : null;
  $('mediaControls').hidden = !native && !embedded;
  if (!native && !embedded) return;
  let duration = 0, current = 0, playing = false;
  if (native) { duration = native.duration; current = native.currentTime; playing = !native.paused && !native.ended; }
  else if (state.webKind === 'youtube') {
    duration = embedded.getDuration(); current = embedded.getCurrentTime();
    playing = embedded.getPlayerState() === YT.PlayerState.PLAYING;
  } else {
    duration = state.vimeoDuration || 0; current = state.vimeoCurrent || 0; playing = state.vimeoPlaying || false;
  }
  $('mediaPlayBtn').textContent = playing ? '❚❚' : '▶';
  $('mediaPlayBtn').setAttribute('aria-label', playing ? 'Pause video' : 'Play video');
  $('mediaTime').textContent = `${mediaClock(current)} / ${mediaClock(duration)}`;
  if (!$('mediaSeek').matches(':active') && document.activeElement !== $('mediaSeek')) $('mediaSeek').value = duration > 0 ? Math.round(current / duration * 1000) : 0;
  $('mediaSeek').disabled = !duration;
}
function applyMediaVolume() {
  const volume = Number($('mediaVolume').value);
  localStorage.setItem('webcorder-media-volume', String(volume));
  $('mediaVolumeValue').textContent = `${volume}%`;
  $('localPlayer').volume = volume / 100;
  $('webVideoPlayer').volume = volume / 100;
  if (state.embedReady && state.webKind === 'youtube') state.embedPlayer.setVolume(volume);
  if (state.embedReady && state.webKind === 'vimeo') state.embedPlayer.setVolume(volume / 100).catch(() => {});
}
function clearEmbed() {
  state.loadId++;
  if (state.embedPlayer) {
    try { Promise.resolve(state.embedPlayer.destroy()).catch(() => {}); } catch (_) {}
  }
  state.embedPlayer = null; state.embedReady = false;
  state.vimeoDuration = 0; state.vimeoCurrent = 0; state.vimeoPlaying = false;
  const frame = $('webFrame');
  if (frame) frame.replaceWith(Object.assign(document.createElement('iframe'), {
    id:'webFrame', title:'Web video player', allow:'autoplay; encrypted-media; picture-in-picture', referrerPolicy:'strict-origin-when-cross-origin', hidden:true
  }));
  updateMediaControls();
}
function youtubeErrorMessage(code) {
  if (code === 100) return 'This YouTube video was removed or is private.';
  if (code === 101 || code === 150) return 'The video owner does not allow this video to play in embedded apps.';
  if (code === 153) return 'YouTube could not identify the embedding app. Reload Webcorder and try again.';
  if (code === 2) return 'This YouTube link does not contain a valid video ID.';
  return `YouTube could not play this video here (error ${code}).`;
}
async function setupEmbed(kind, id, url, loadId) {
  try {
    if (kind === 'youtube') {
      await loadPlayerSdk('YouTube', 'https://www.youtube.com/iframe_api', () => !!window.YT?.Player);
      if (loadId !== state.loadId) return;
      const player = new YT.Player('webFrame', {videoId:id, playerVars:{autoplay:0, controls:0, fs:0, playsinline:1, origin:location.origin}, events:{onReady:(event) => {
        if (loadId !== state.loadId) return;
        state.embedPlayer = event.target; state.embedReady = true; applyMediaVolume(); updateMediaControls();
        $('webLinkState').textContent = 'Video ready';
      }, onStateChange:updateMediaControls, onError:(event) => {
        if (loadId !== state.loadId) return;
        const message = youtubeErrorMessage(event.data);
        $('webLinkState').textContent = message;
        $('externalLinkBtn').hidden = false;
        note(`${message} Use Open in browser if embedding is blocked.`, true);
      }}});
      state.embedPlayer = player;
    } else {
      await loadPlayerSdk('Vimeo', 'https://player.vimeo.com/api/player.js', () => !!window.Vimeo?.Player);
      if (loadId !== state.loadId) return;
      const player = new Vimeo.Player($('webFrame'), {url, controls:false, title:false, byline:false, portrait:false});
      state.embedPlayer = player;
      player.on('timeupdate', data => { state.vimeoCurrent = data.seconds; state.vimeoDuration = data.duration; updateMediaControls(); });
      player.on('play', () => { state.vimeoPlaying = true; updateMediaControls(); });
      player.on('pause', () => { state.vimeoPlaying = false; updateMediaControls(); });
      await player.ready();
      if (loadId !== state.loadId) return;
      state.embedReady = true; applyMediaVolume(); updateMediaControls();
      $('webLinkState').textContent = 'Video ready';
    }
  } catch (error) {
    if (loadId !== state.loadId) return;
    note(`${error.message} Use the video provider's controls or open it in a browser.`, true);
    $('webFrame').src = url;
  }
}

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
    clearEmbed();
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
  $('webFrame').hidden = source !== 'web' || !['frame','youtube','vimeo'].includes(state.webKind);
  if (source !== 'local') $('localPlayer').pause();
  if (source !== 'web') $('webVideoPlayer').pause();
  $('playerEmpty').hidden = source === 'local' ? !!state.localUrl : source === 'web' ? !$('webFrame').hidden || !$('webVideoPlayer').hidden : false;
  $('playerEmpty').querySelector('p').textContent = source === 'web' ? 'Paste a link' : 'Choose a file';
  updateMediaControls();
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
    mic: $('micToggle').checked, desktopAudio: $('audioToggle').checked, fps: Number($('fpsSelect').value),
    cameraLayout: $('screenCameraLayout').value };
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
  if (data.width && data.height) $('previewStage').style.setProperty('--screen-stack-ratio', String(data.width / (data.height * 2)));
  $('previewStage').classList.toggle('stacked-screen', state.source === 'screen' && !!$('cameraSelect').value && ['top', 'bottom'].includes($('screenCameraLayout').value));
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
  applyMediaVolume(); updateMediaControls();
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
  clearEmbed(); $('webFrame').hidden = true;
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
  let embed = null, embedKind = null, embedId = null;
  if (hostname === 'youtu.be') {
    const id = url.pathname.split('/').filter(Boolean)[0];
    if (/^[\w-]{11}$/.test(id || '')) { embed = `https://www.youtube.com/embed/${id}`; embedKind = 'youtube'; embedId = id; }
  } else if (['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtube-nocookie.com','www.youtube-nocookie.com'].includes(hostname)) {
    if (url.pathname.startsWith('/shorts/')) state.webSize = {width:720, height:1280};
    const id = url.searchParams.get('v') || url.pathname.match(/^\/(?:shorts|live|embed)\/([\w-]{11})/)?.[1];
    if (/^[\w-]{11}$/.test(id || '')) { embed = `https://www.youtube.com/embed/${id}`; embedKind = 'youtube'; embedId = id; }
  } else if (hostname === 'vimeo.com' || hostname === 'www.vimeo.com' || hostname === 'player.vimeo.com') {
    const id = url.pathname.match(/\/(\d+)(?:\/|$)/)?.[1];
    if (id) { embed = `https://player.vimeo.com/video/${id}`; embedKind = 'vimeo'; embedId = id; }
  }
  if (/\.(?:mp4|webm|ogg)(?:$|\?)/i.test(url.pathname + url.search)) {
    state.webKind = 'video';
    $('webVideoPlayer').src = url.toString();
    $('webVideoPlayer').hidden = false;
    applyMediaVolume();
    $('webVideoPlayer').play().catch(() => {});
    $('webLinkState').textContent = 'Video ready';
  } else {
    state.webKind = embedKind || 'frame';
    if (embedKind) {
      $('webFrame').src = embedKind === 'youtube' ? `${embed}?enablejsapi=1&controls=0&fs=0&playsinline=1&origin=${encodeURIComponent(location.origin)}` : `${embed}?controls=0&title=0&byline=0&portrait=0`;
      setupEmbed(embedKind, embedId, url.toString(), state.loadId);
    }
    else $('webFrame').src = url.toString();
    $('webFrame').hidden = false;
    $('webLinkState').textContent = embed ? 'Loading video…' : 'Page ready';
    $('externalLinkBtn').hidden = false;
  }
  $('playerEmpty').hidden = true;
  setViewer('player');
  resizePlayer();
  updateMediaControls();
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
  const savedLayout = localStorage.getItem('webcorder-screen-layout');
  if (['floating', 'top', 'bottom'].includes(savedLayout)) $('screenCameraLayout').value = savedLayout;
  $('screenCameraLayout').addEventListener('change', async () => {
    if (state.status?.mode === 'record') {
      $('screenCameraLayout').value = localStorage.getItem('webcorder-screen-layout') || 'floating';
      note('Stop the recording before changing the camera layout.', true);
      return;
    }
    localStorage.setItem('webcorder-screen-layout', $('screenCameraLayout').value);
    $('previewStage').classList.toggle('stacked-screen', !!$('cameraSelect').value && $('screenCameraLayout').value !== 'floating');
    if (state.source === 'screen' && state.status?.mode === 'preview') await begin('preview');
  });
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
  for (const id of ['localPlayer', 'webVideoPlayer']) {
    for (const event of ['play', 'pause', 'timeupdate', 'durationchange', 'ended']) $(id).addEventListener(event, updateMediaControls);
  }
  $('mediaVolume').value = mediaVolume;
  applyMediaVolume();
  $('mediaVolume').addEventListener('input', applyMediaVolume);
  $('mediaPlayBtn').addEventListener('click', async () => {
    const native = activeMedia();
    if (native) { if (native.paused) await native.play().catch(error => note(error.message, true)); else native.pause(); }
    else if (state.embedReady && state.webKind === 'youtube') {
      if (state.embedPlayer.getPlayerState() === YT.PlayerState.PLAYING) state.embedPlayer.pauseVideo();
      else state.embedPlayer.playVideo();
    } else if (state.embedReady && state.webKind === 'vimeo') {
      if (state.vimeoPlaying) await state.embedPlayer.pause().catch(error => note(error.message, true));
      else await state.embedPlayer.play().catch(error => note(error.message, true));
    }
    updateMediaControls();
  });
  $('mediaSeek').addEventListener('input', () => {
    const native = activeMedia();
    const duration = native?.duration || (state.webKind === 'youtube' && state.embedReady ? state.embedPlayer.getDuration() : state.vimeoDuration);
    $('mediaTime').textContent = `${mediaClock(duration * Number($('mediaSeek').value) / 1000)} / ${mediaClock(duration)}`;
  });
  $('mediaSeek').addEventListener('change', () => {
    const native = activeMedia();
    const duration = native?.duration || (state.webKind === 'youtube' && state.embedReady ? state.embedPlayer.getDuration() : state.vimeoDuration);
    const position = duration * Number($('mediaSeek').value) / 1000;
    if (native) native.currentTime = position;
    else if (state.embedReady && state.webKind === 'youtube') state.embedPlayer.seekTo(position, true);
    else if (state.embedReady && state.webKind === 'vimeo') state.embedPlayer.setCurrentTime(position).catch(error => note(error.message, true));
    $('mediaSeek').blur();
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
    if (state.status?.mode === 'record') note('Stop the recording before changing the camera.', true);
    else if (state.status?.mode === 'preview') begin('preview');
    else note('Camera selected. Start preview to see the layout.');
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
bind(); $('playerTab').hidden = true; $('playerStage').classList.toggle('camera-first', state.cameraFirst); $('cameraOrderBtn').textContent = state.cameraFirst ? '↓ Camera below video' : '↑ Camera on top'; setupInstall(); refreshCameras().catch(e => note(e.message,true)); poll(); setInterval(poll, 1000); setInterval(updateMediaControls, 500); setInterval(() => refreshCameras().catch(()=>{}), 7000);
