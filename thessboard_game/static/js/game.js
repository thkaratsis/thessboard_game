const socket = io();

const els = {
  roomPill: document.getElementById('room-pill'),
  modePanel: document.getElementById('mode-panel'),
  modeHotseat: document.getElementById('mode-hotseat'),
  modeMulti: document.getElementById('mode-multi'),
  modeBots: document.getElementById('mode-bots'),
  playersList: document.getElementById('players-list'),
  interactionBox: document.getElementById('interaction-box'),
  logBox: document.getElementById('log-box'),
  rollBtn: document.getElementById('roll-btn'),
  endTurnBtn: document.getElementById('end-turn-btn'),
  tokensLayer: document.getElementById('tokens-layer'),
  nodeOverlay: document.getElementById('node-overlay'),
  diceDisplay: document.getElementById('dice-display'),
  turnCard: document.getElementById('turn-card'),
};

const appState = { playerId: null, roomCode: null, gameState: null, nodes: [], sounds: {}, animating: false, selectedMode: 'hotseat' };
fetch('/static/data/game_data.json').then(r => r.json()).then(data => { appState.nodes = data.nodes; drawNodeOverlay(); renderModePanel(); });

function loadSound(name, path) { const audio = new Audio(path); audio.preload = 'auto'; appState.sounds[name] = audio; }
function playSound(name, volume = 0.72) { const src = appState.sounds[name]; if (!src) return; const a = src.cloneNode(); a.volume = volume; a.play().catch(() => {}); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function currentPlayer(state) { return state?.players?.[state.turnIndex] || null; }
function me(state) { return state?.players?.find(p => p.id === appState.playerId) || null; }
function canControlCurrentTurn(state) {
  if (!state?.started) return false;

  if (state.mode === 'hotseat') {
    return !appState.animating;
  }

  const mine = me(state);
  return !!(mine && currentPlayer(state)?.id === mine.id && !appState.animating);
}
function nodeById(nodeId) { return appState.nodes.find(n => n.id === nodeId); }

loadSound('dice', '/static/audio/dice_roll.wav');
loadSound('move', '/static/audio/token_move.wav');
loadSound('correct', '/static/audio/correct.wav');
loadSound('wrong', '/static/audio/wrong.wav');
loadSound('win', '/static/audio/win.wav');
loadSound('click', '/static/audio/ui_click.wav');

function drawNodeOverlay() {
  if (!appState.nodes.length) return;
  els.nodeOverlay.innerHTML = '';
  appState.nodes.forEach(node => {
    if (!['metro', 'landmark', 'event', 'start'].includes(node.kind)) return;
    const dot = document.createElement('div');
    dot.className = `board-node node-${node.kind}`;
    dot.style.left = `${node.x * 100}%`;
    dot.style.top = `${node.y * 100}%`;
    dot.title = node.label || node.kind;
    els.nodeOverlay.appendChild(dot);
  });
}

function activateMode(mode) {
  appState.selectedMode = mode;
  [els.modeHotseat, els.modeMulti, els.modeBots].forEach(btn => btn.classList.remove('active'));
  ({ hotseat: els.modeHotseat, multiplayer: els.modeMulti, bots: els.modeBots })[mode].classList.add('active');
  renderModePanel();
}

function renderModePanel() {
  if (appState.gameState && appState.gameState.started) {
    els.modePanel.innerHTML = '<div class="modal-card">Το παιχνίδι έχει ξεκινήσει.</div>';
    return;
  }
  if (appState.selectedMode === 'hotseat') {
    els.modePanel.innerHTML = `
      <div class="stack-fields">
        <input id="hs-name-1" placeholder="Παίκτης 1" value="Player 1">
        <input id="hs-name-2" placeholder="Παίκτης 2" value="Player 2">
        <input id="hs-name-3" placeholder="Παίκτης 3 (προαιρετικά)">
        <button class="primary-btn" id="create-hotseat-btn">Έναρξη στο ίδιο κινητό</button>
      </div>`;
    document.getElementById('create-hotseat-btn').onclick = () => {
      playSound('click');
      const names = ['hs-name-1', 'hs-name-2', 'hs-name-3'].map(id => document.getElementById(id).value.trim()).filter(Boolean);
      socket.emit('create_hotseat', { names });
    };
    return;
  }
  if (appState.selectedMode === 'multiplayer') {
    els.modePanel.innerHTML = `
      <div class="stack-fields">
        <input id="mp-name" placeholder="Το όνομά σου">
        <div class="auth-actions">
          <button class="primary-btn" id="create-room-btn">Δημιούργησε δωμάτιο</button>
          <button class="secondary-btn" id="join-room-btn">Σύνδεση</button>
        </div>
        <input id="mp-room" maxlength="5" placeholder="Κωδικός δωματίου">
        <button class="secondary-btn" id="add-bot-btn">Πρόσθεσε Bot</button>
        <button class="primary-btn hidden" id="start-btn">Έναρξη παιχνιδιού</button>
      </div>`;
    document.getElementById('create-room-btn').onclick = () => { playSound('click'); socket.emit('create_room', { name: document.getElementById('mp-name').value.trim() || 'Host' }); };
    document.getElementById('join-room-btn').onclick = () => { playSound('click'); socket.emit('join_room_request', { name: document.getElementById('mp-name').value.trim() || 'Παίκτης', roomCode: document.getElementById('mp-room').value.trim() }); };
    document.getElementById('add-bot-btn').onclick = () => { playSound('click'); socket.emit('add_bot'); };
    document.getElementById('start-btn').onclick = () => { playSound('click'); socket.emit('start_game'); };
    return;
  }
  els.modePanel.innerHTML = `
    <div class="stack-fields">
      <input id="bot-player-name" placeholder="Το όνομά σου" value="Player 1">
      <select id="bot-count">
        <option value="1">1 bot</option>
        <option value="2" selected>2 bots</option>
      </select>
      <button class="primary-btn" id="create-bot-match-btn">Start vs Bots</button>
    </div>`;
  document.getElementById('create-bot-match-btn').onclick = () => {
    playSound('click');
    socket.emit('create_bot_match', { name: document.getElementById('bot-player-name').value.trim() || 'Παίκτης', botCount: document.getElementById('bot-count').value });
  };
}

function setButtons(state) {
  const isMyTurn = canControlCurrentTurn(state);
  els.rollBtn.disabled = !(state?.started && state.phase === 'await_roll' && isMyTurn);
  els.endTurnBtn.disabled = true;
  const startBtn = document.getElementById('start-btn');
  if (startBtn) {
    startBtn.classList.toggle('hidden', !state || state.started || state.mode !== 'multiplayer');
    const host = state?.players?.[0];
    startBtn.disabled = !(host && host.id === appState.playerId && state.players.length >= 2);
  }
}

function renderPlayers(state) {
  els.playersList.innerHTML = '';
  if (!state?.players?.length) { els.playersList.innerHTML = '<div class="log-item">Δεν υπάρχουν ακόμα παίκτες.</div>'; return; }
  state.players.forEach((player, index) => {
    const tile = document.createElement('div');
    tile.className = 'player-tile' + (state.turnIndex === index ? ' current-turn' : '');
    const node = nodeById(player.nodeId);
    tile.innerHTML = `
      <div class="player-header">
        <div class="player-chip" style="background:${player.color}"></div>
        <div class="player-name-block">
          <div class="player-name">${player.name}${player.isBot ? ' 🤖' : ''}</div>
          <div class="player-sub">${player.connected ? 'online' : 'offline'} · ${node?.label || 'Διαδρομή'}</div>
        </div>
        <div class="player-score">${player.monumentCards}/${state.cardsToWin}</div>
      </div>`;
    els.playersList.appendChild(tile);
  });
}

function renderLog(state) {
  els.logBox.innerHTML = '';
  (state?.log || []).forEach(item => {
    const div = document.createElement('div');
    div.className = 'log-item';
    div.textContent = item;
    els.logBox.appendChild(div);
  });
}

function tokenOffset(index) { return [{ x: 0, y: 0 }, { x: 16, y: -14 }, { x: -16, y: 14 }][index] || { x: 0, y: 0 }; }
function placeToken(el, node, offset, instant = false) {
  if (!node) return;
  if (instant) el.classList.add('instant'); else el.classList.remove('instant');
  el.style.left = `calc(${node.x * 100}% + ${offset.x}px)`;
  el.style.top = `calc(${node.y * 100}% + ${offset.y}px)`;
}

function renderTokens(state, instant = false) {
  els.tokensLayer.innerHTML = '';
  (state?.players || []).forEach((player, index) => {
    const node = nodeById(player.nodeId); const offset = tokenOffset(index);
    const token = document.createElement('div'); token.className = 'token' + (state.turnIndex === index ? ' current' : ''); token.dataset.playerId = player.id; token.style.background = player.color; placeToken(token, node, offset, instant);
    const label = document.createElement('div'); label.className = 'token-label'; label.textContent = player.name; label.style.left = `calc(${node.x * 100}% + ${offset.x}px)`; label.style.top = `calc(${node.y * 100}% + ${offset.y - 18}px)`;
    els.tokensLayer.appendChild(token); els.tokensLayer.appendChild(label);
  });
}

function renderTurnCard(state) {
  if (!state?.started) { els.turnCard.textContent = 'Διάλεξε mode από το lobby.'; return; }
  const cp = currentPlayer(state);
  const modeLabel = state.mode === 'hotseat' ? 'Ίδια συσκευή' : state.mode === 'bots' ? 'Bots' : 'Multiplayer';
  els.turnCard.textContent = state.winnerId ? `Νικητής: ${state.players.find(p => p.id === state.winnerId)?.name || ''}` : `${modeLabel} · Σειρά: ${cp?.name || ''}`;
}

function optionButtons(options, cls = 'option-btn', attr = 'data-answer') {
  return (options || []).map(option => `<button class="${cls}" ${attr}="${encodeURIComponent(option)}">${option}</button>`).join('');
}

function renderInteraction(state) {
  const mine = me(state);
  const isMyTurn = canControlCurrentTurn(state);
  const pending = state?.pendingCard;  if (!state) { els.interactionBox.innerHTML = '<div class="modal-card">Διάλεξε mode για να ξεκινήσεις.</div>'; return; }
  if (state.winnerId) { const winner = state.players.find(p => p.id === state.winnerId); els.interactionBox.innerHTML = `<div class="modal-card"><div class="correct-banner">🏆 Νικητής: ${winner?.name || ''}</div></div>`; return; }
  if (!state.started) { els.interactionBox.innerHTML = '<div class="modal-card">Περίμενε να ξεκινήσει το παιχνίδι.</div>'; return; }
  if (!isMyTurn) {
    if (state.phase === 'await_card_resolution' && pending) {
      els.interactionBox.innerHTML = `<div class="modal-card spectator-card"><div class="card-tag">Σειρά άλλου παίκτη</div><p><strong>${currentPlayer(state)?.name || ''}</strong> ενεργοποίησε συμβάν:</p><p>${pending.card.text}</p></div>`;
      return;
    }
    if (state.phase === 'await_answer' && pending) {
      const title = pending.type === 'metro' ? 'Κάρτα Μετρό' : pending.node.label;
      els.interactionBox.innerHTML = `<div class="modal-card spectator-card"><div class="card-tag">${title}</div><p><strong>${currentPlayer(state)?.name || ''}</strong> απαντά τώρα:</p><p>${pending.card.question || pending.card.text}</p><div class="answers-grid spectator">${(pending.card.options || []).map(o => `<div class="spectator-option">${o}</div>`).join('')}</div></div>`;
      return;
    }
    if (state.phase === 'await_landmark_choice') {
      els.interactionBox.innerHTML = `<div class="modal-card spectator-card">Ο/Η <strong>${currentPlayer(state)?.name || ''}</strong> διαλέγει μνημείο μέσω μετρό…</div>`;
      return;
    }
    els.interactionBox.innerHTML = `<div class="modal-card">Περίμενε τη σειρά σου. Παίζει ο/η <strong>${currentPlayer(state)?.name || ''}</strong>.</div>`;
    return;
  }
  if (state.phase === 'await_roll') { els.interactionBox.innerHTML = '<div class="modal-card">Ρίξε το ζάρι.</div>'; return; }
  if (state.phase === 'await_card_resolution' && pending) {
    els.interactionBox.innerHTML = `<div class="modal-card"><div class="card-tag">Συμβάν</div><p>${pending.card.text}</p><button class="primary-btn" id="resolve-event-btn">Εφαρμογή κάρτας</button></div>`;
    document.getElementById('resolve-event-btn').onclick = () => { playSound('click'); socket.emit('resolve_event_card'); };
    return;
  }
  if (state.phase === 'await_answer' && pending) {
    const title = pending.type === 'metro' ? 'Κάρτα Μετρό' : pending.node.label;
    els.interactionBox.innerHTML = `<div class="modal-card"><div class="card-tag">${title}</div><p>${pending.card.question || pending.card.text}</p><div class="answers-grid">${optionButtons(pending.card.options)}</div></div>`;
    els.interactionBox.querySelectorAll('[data-answer]').forEach(btn => { btn.onclick = () => { playSound('click', 0.45); socket.emit('submit_answer', { answer: decodeURIComponent(btn.dataset.answer) }); }; });
    return;
  }
  if (state.phase === 'await_landmark_choice') {
    const choices = state.pendingChoices || [];
    els.interactionBox.innerHTML = `<div class="modal-card"><div class="card-tag">Μετακίνηση με Μετρό</div><p>Διάλεξε μνημείο για άμεση μεταφορά.</p><div class="choice-grid">${choices.map(c => `<button class="choice-btn" data-node-id="${c.id}"><span>${c.label}</span><small>${c.deck || ''}</small></button>`).join('')}</div></div>`;
    els.interactionBox.querySelectorAll('[data-node-id]').forEach(btn => { btn.onclick = () => socket.emit('choose_landmark', { nodeId: btn.dataset.nodeId }); });
    return;
  }
  if (state.phase === 'await_end_turn') { els.interactionBox.innerHTML = '<div class="modal-card">Η σειρά τελειώνει αυτόματα…</div>'; return; }
  els.interactionBox.innerHTML = '<div class="modal-card">Περίμενε…</div>';
}

async function animateMove(lastMove, state) {
  if (!lastMove?.playerId || !lastMove.path?.length) return;
  const token = els.tokensLayer.querySelector(`.token[data-player-id="${lastMove.playerId}"]`);
  const labelNodes = Array.from(els.tokensLayer.querySelectorAll('.token-label'));
  const playerIndex = state.players.findIndex(p => p.id === lastMove.playerId);
  const label = labelNodes[playerIndex];
  const offset = tokenOffset(playerIndex);
  if (!token || !label) return;
  appState.animating = true; setButtons(state);
  const stepDelay = lastMove.reason === 'metro_jump' ? 70 : 240;
  for (const step of lastMove.path) {
    const node = nodeById(step); placeToken(token, node, offset); label.style.left = `calc(${node.x * 100}% + ${offset.x}px)`; label.style.top = `calc(${node.y * 100}% + ${offset.y - 18}px)`; playSound('move', 0.35); await sleep(stepDelay);
  }
  appState.animating = false; setButtons(state);
}

function renderState(state, instant = false) {
  if (!state) return;
  els.roomPill.textContent = state.roomCode ? `Room ${state.roomCode}` : 'Main Menu';
  els.diceDisplay.textContent = state.lastDice || '🎲';

  renderPlayers(state);
  renderLog(state);
  renderTokens(state, instant);
  renderTurnCard(state);
  renderInteraction(state);
  renderModePanel();
  setButtons(state);
}

let previousState = null; let lastAnimatedSignature = '';
socket.on('state_update', async state => {
  const moveSignature = JSON.stringify(state.lastMove || {});
  const hadCorrect = previousState?.phase === 'await_answer' && state.phase === 'await_landmark_choice';
  const hadWrong = previousState?.phase === 'await_answer' && state.phase === 'await_end_turn' && !state.pendingCard;
  const newWinner = !previousState?.winnerId && state.winnerId;
  const newRoll = previousState?.lastDice !== state.lastDice && state.lastDice;
  appState.gameState = state; renderState(state, !previousState);
  if (newRoll) playSound('dice');
  if (moveSignature !== lastAnimatedSignature && state.lastMove?.path?.length) { lastAnimatedSignature = moveSignature; await animateMove(state.lastMove, state); }
  if (hadCorrect) playSound('correct');
  if (hadWrong) playSound('wrong');
  if (newWinner) playSound('win');
  previousState = JSON.parse(JSON.stringify(state)); renderState(state);
});

socket.on('room_created', payload => { appState.roomCode = payload.roomCode; appState.playerId = payload.playerId; });
socket.on('room_joined', payload => { appState.roomCode = payload.roomCode; appState.playerId = payload.playerId; });
socket.on('server_error', payload => { alert(payload.message || 'Σφάλμα διακομιστή'); });

els.modeHotseat.onclick = () => activateMode('hotseat');
els.modeMulti.onclick = () => activateMode('multiplayer');
els.modeBots.onclick = () => activateMode('bots');
els.rollBtn.onclick = () => socket.emit('roll_dice');
renderModePanel();
