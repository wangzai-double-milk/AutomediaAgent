// Small, event-driven studio. Creative controls only prepare text locally.
(() => {
  'use strict';
  const el = (id) => document.getElementById(id);
  const app = el('app');
  const titles = {
    scene: ['AUTOMEDIA / ON SET', '你的创作小片场', '来，开个好头。'],
    operations: ['AUTOMEDIA / PERSONAL JOURNAL', '她的生活手记', '每一次观察，都留下一点变化。'],
    assets: ['AUTOMEDIA / MATERIAL DESK', '作品，摊开来看。', '每个版本都算数。'],
    ideas: ['AUTOMEDIA / IDEA PLAYGROUND', '好点子，从玩开始。', '给下一条找个开场。'],
    rhythm: ['AUTOMEDIA / RELEASE RHYTHM', '创作大胆，发布从容。', '一步一步，留住成果。'],
  };
  let activity = null;
  let selectedStation = null;
  let studioState = null;
  let platformState = null;
  let toastTimer;
  let busy = false;
  let requestId = 0;
  const storage = {
    read(key, fallback = '') { try { return localStorage.getItem(`automedia:${key}`) ?? fallback; } catch (_) { return fallback; } },
    write(key, value) { try { localStorage.setItem(`automedia:${key}`, value); return true; } catch (_) { return false; } },
  };
  function toast(text) {
    clearTimeout(toastTimer);
    el('studio-toast').textContent = text;
    el('studio-toast').hidden = false;
    toastTimer = setTimeout(() => { el('studio-toast').hidden = true; }, 3400);
  }
  function showView(view) {
    if (!titles[view]) return;
    app.dataset.studioView = view;
    document.querySelectorAll('[data-studio-nav]').forEach((button) => {
      if (button.dataset.studioNav === view) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-studio-panel]').forEach((panel) => {
      panel.hidden = !panel.dataset.studioPanel.split(' ').includes(view);
    });
    el('studio-eyebrow').textContent = titles[view][0];
    const sub = document.createElement('span');
    sub.textContent = titles[view][2];
    el('studio-view-title').replaceChildren(document.createTextNode(titles[view][1]), sub);
    el('agent-live').scrollTop = 0;
    storage.write('view', view);
    if (view === 'operations') el('setup-gate')?.classList.add('hidden');
    if (view === 'rhythm') refreshStudio();
  }
  document.querySelectorAll('[data-studio-nav]').forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.studioNav));
  });

  const stations = {
    research: ['观察台', '换关键词搜索，读参考帖、看画面，留下可以追溯的证据。', '看看真实参考帖 ↗', 'assets'],
    create: ['制作桌', '把原始素材、改图版本、视频和文稿放在一起，选一个对象仔细看。', '摊开素材桌 ↗', 'assets'],
    publish: ['出片口', '核对账号、作品和可见范围，按共享额度安排提交，结果回读后再确认完成。', '查看发布节奏 ↗', 'rhythm'],
  };
  function stageFor(data) {
    if (['search', 'observe', 'vision', 'learn', 'guard'].includes(data?.phase)) return 'research';
    if (['create', 'write'].includes(data?.phase)) return 'create';
    if (['publish', 'done'].includes(data?.phase)) return 'publish';
    return 'idle';
  }
  function selectStation(id) {
    selectedStation = id;
    const info = stations[id];
    el('station-title').textContent = info ? info[0] : 'Agent 的当前动作';
    el('station-copy').textContent = info ? info[1] : activity?.current?.detail || '尚未收到任务。可以到灵感区组合一个起点。';
    el('station-action').textContent = info ? info[2] : '去灵感区找个开场 ↗';
    document.querySelectorAll('[data-station]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.station === id));
    });
  }
  document.querySelectorAll('[data-station]').forEach((button) => {
    button.addEventListener('click', () => selectStation(button.dataset.station));
  });
  el('studio-agent').addEventListener('click', () => {
    selectStation(null);
    toast(activity?.current?.label || '我在等你安排下一场。');
  });
  el('station-action').addEventListener('click', () => showView(stations[selectedStation]?.[3] || 'ideas'));
  function updateActivity(data) {
    activity = data;
    const stage = stageFor(data);
    const status = data?.status || 'idle';
    const floor = el('scene-floor');
    floor.dataset.phase = stage;
    floor.dataset.status = status;
    document.querySelectorAll('[data-station]').forEach((button) => {
      button.classList.toggle('working', button.dataset.station === stage && status === 'running');
    });
    const labels = { idle: '等你喊开拍', research: '多看一眼', create: '想法成形中', publish: '认真核对' };
    el('agent-bubble').textContent = status === 'error' ? '有事需要处理' : data?.phase === 'done' ? '这场，完成！' : labels[stage];
    const current = data?.current?.label || '等待任务';
    el('studio-current').textContent = `${phaseLabel(data?.phase, status)} · ${current}`;
    el('studio-agent').setAttribute('aria-label', `查看 Agent 当前动作：${current}`);
    if (selectedStation === null && data?.status === 'running') el('station-copy').textContent = data.current?.detail || '正在推进当前任务。';
  }
  window.addEventListener('automedia:activity', (event) => updateActivity(event.detail));

  let motionPaused = storage.read('motion-paused') === '1';
  function setMotion() {
    app.classList.toggle('motion-paused', motionPaused);
    el('motion-toggle').setAttribute('aria-pressed', String(motionPaused));
    el('motion-toggle').textContent = motionPaused ? '恢复动效' : '暂停动效';
  }
  el('motion-toggle').addEventListener('click', () => {
    motionPaused = !motionPaused;
    storage.write('motion-paused', motionPaused ? '1' : '0');
    setMotion();
  });
  setMotion();

  // Chat stays underneath the workspace; sizing is available without a mouse.
  let normalHeight = Number(storage.read('chat-height', '240')) || 240;
  let expanded = false;
  function chatBounds() {
    return { min: 180, max: Math.max(180, Math.floor(window.innerHeight * 0.72)) };
  }
  function resizeChat(height, remember = true) {
    const { min, max } = chatBounds();
    const next = Math.max(min, Math.min(max, height));
    app.style.setProperty('--chat-height', `${next}px`);
    const resizer = el('chat-resizer');
    resizer.setAttribute('aria-valuemin', String(min));
    resizer.setAttribute('aria-valuemax', String(max));
    resizer.setAttribute('aria-valuenow', String(Math.round(next)));
    if (remember) { normalHeight = next; storage.write('chat-height', String(next)); }
  }
  function setExpanded(value) {
    expanded = value;
    el('chat-toggle').setAttribute('aria-expanded', String(value));
    el('chat-toggle').textContent = value ? '收起对话 ↙' : '展开对话 ↗';
    app.classList.toggle('chat-expanded', value);
    resizeChat(value ? window.innerHeight * 0.58 : normalHeight, false);
  }
  el('chat-toggle').addEventListener('click', () => setExpanded(!expanded));
  let drag = null;
  const resizer = el('chat-resizer');
  resizer.addEventListener('pointerdown', (event) => {
    drag = { y: event.clientY, height: el('main').getBoundingClientRect().height };
    resizer.setPointerCapture(event.pointerId);
    app.classList.add('chat-dragging');
  });
  resizer.addEventListener('pointermove', (event) => {
    if (!drag) return;
    setExpanded(false);
    resizeChat(drag.height + drag.y - event.clientY);
  });
  function endDrag() { drag = null; app.classList.remove('chat-dragging'); }
  resizer.addEventListener('pointerup', endDrag);
  resizer.addEventListener('pointercancel', endDrag);
  resizer.addEventListener('lostpointercapture', endDrag);
  resizer.addEventListener('keydown', (event) => {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const height = el('main').getBoundingClientRect().height;
    setExpanded(false);
    resizeChat(event.key === 'Home' ? chatBounds().min : event.key === 'End' ? chatBounds().max
      : height + (event.key === 'ArrowUp' ? 30 : -30));
  });
  window.addEventListener('resize', () => resizeChat(expanded ? window.innerHeight * 0.58 : normalHeight, false));
  resizeChat(normalHeight, false);

  function applyFilter() {
    const needle = el('asset-filter').value.trim().toLocaleLowerCase();
    let visible = 0;
    const cards = document.querySelectorAll('.asset-gallery .asset-item, .asset-gallery .document-item');
    const lookup = new Map(workspaceItems(currentWorkspace).map((item) => [item.id, item]));
    cards.forEach((card) => {
      const item = lookup.get(card.dataset.itemId) || {};
      const match = !needle || `${card.textContent} ${item.keyword || ''} ${item.kind || ''} ${item.roleLabel || ''}`.toLocaleLowerCase().includes(needle);
      card.hidden = !match;
      if (match) visible++;
    });
    el('asset-filter-result').textContent = needle ? `${visible} 个匹配对象` : `${cards.length} 个工作对象`;
  }
  el('asset-filter').addEventListener('input', applyFilter);
  function updateWorkspace(workspace) {
    const total = workspaceItems(workspace).length;
    el('studio-asset-count').textContent = String(total);
    el('scene-materials').textContent = `${total} 个工作对象`;
    applyFilter();
    // Pipeline nodes expose the real stage description through the same controls.
    document.querySelectorAll('.pipeline-stage').forEach((node) => {
      node.setAttribute('role', 'button');
      node.tabIndex = 0;
      node.onclick = () => toast(`${node.textContent.trim()} · ${node.classList.contains('done') ? '已完成' : node.classList.contains('active') ? '正在进行' : '等待执行'}`);
      node.onkeydown = (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); node.click(); } };
    });
  }
  window.addEventListener('automedia:workspace', (event) => updateWorkspace(event.detail));
  window.addEventListener('automedia:focus', (event) => toast(`桌上聚焦：${event.detail.title || event.detail.name || '当前对象'}`));

  function ideaPrompt() {
    const topic = el('idea-topic').value;
    const format = el('idea-format').value;
    const angle = el('idea-angle').value;
    const pace = format === '高节奏短视频' ? '压缩移动和等待时间，提高动作与反馈频率；先用简笔画和箭头说明动作轨迹。' : '开头具体、过程可复用，保留自己的观察和方法。';
    return `围绕「${topic}」策划一篇${format}，从「${angle}」切入。${pace}先给我选题、结构和素材方案，再讨论制作与发布安排。`;
  }
  function updateIdea() {
    el('idea-title').textContent = `${el('idea-topic').value} × ${el('idea-angle').value}`;
    el('idea-preview').textContent = ideaPrompt();
    const selection = ['idea-topic', 'idea-format', 'idea-angle'].map((id) => el(id).selectedIndex);
    storage.write('idea', JSON.stringify(selection));
  }
  try {
    const saved = JSON.parse(storage.read('idea', '[]'));
    if (Array.isArray(saved)) ['idea-topic', 'idea-format', 'idea-angle'].forEach((id, i) => {
      if (Number.isInteger(saved[i]) && saved[i] >= 0 && saved[i] < el(id).options.length) el(id).selectedIndex = saved[i];
    });
  } catch (_) {}
  ['idea-topic', 'idea-format', 'idea-angle'].forEach((id) => el(id).addEventListener('change', updateIdea));
  el('idea-shuffle').addEventListener('click', () => {
    const previous = ideaPrompt();
    ['idea-topic', 'idea-format', 'idea-angle'].forEach((id) => { el(id).selectedIndex = Math.floor(Math.random() * el(id).options.length); });
    if (ideaPrompt() === previous) el('idea-angle').selectedIndex = (el('idea-angle').selectedIndex + 1) % el('idea-angle').options.length;
    updateIdea();
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches && !motionPaused) {
      el('idea-ticket').animate([{ transform: 'rotate(-1deg) translateY(8px)', opacity: 0.6 }, { transform: 'none', opacity: 1 }], { duration: 260 });
    }
    toast('抽到了一个新开场。');
  });
  el('idea-use').addEventListener('click', () => {
    const input = el('input');
    // Preserve any unfinished message by appending instead of replacing.
    const previous = input.value.trimEnd();
    input.value = `${previous}${previous ? '\n\n' : ''}${ideaPrompt()}`;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    toast('灵感已带到对话舱，准备好后再发送。');
  });
  updateIdea();
  el('studio-note').value = storage.read('note');
  el('studio-note').addEventListener('input', () => { el('note-status').textContent = '有未保存的修改'; });
  el('note-save').addEventListener('click', () => {
    el('note-status').textContent = storage.write('note', el('studio-note').value) ? '已保存到当前浏览器' : '浏览器存储不可用，请复制便笺留存';
  });

  function readinessLabel(available) {
    if (!available) return '尚未核对';
    if (available.ok) return '发帖间隔已满足 · 提交前会核对浏览器账号';
    return available.reason || '当前写操作已暂停';
  }
  function renderRhythm(data) {
    studioState = data;
    const test = data?.testControl || {};
    const quota = data?.quota || {};
    const binding = test.binding;
    const healthy = data?.ok === true && quota.available === true;
    const limits = test.limits || {};
    el('rhythm-status').textContent = data?.ok === false ? data.error || '状态读取失败，稍后重试。' : readinessLabel(test.publish_ready);
    el('quota-count').textContent = healthy ? limits.rolling_24h == null ?
      `近24小时已记录 ${quota.usedWrites} 次操作` : `${quota.remainingWrites} / ${limits.rolling_24h} 次剩余` : '等待状态核对';
    const dots = [];
    if (healthy) for (let i = 0; i < limits.rolling_24h; i++) {
      const dot = document.createElement('span');
      dot.className = i < quota.usedWrites ? 'used' : '';
      dot.textContent = i < quota.usedWrites ? '已领取' : '可用';
      dots.push(dot);
    }
    el('quota-dots').replaceChildren(...dots);
    el('publish-quota').textContent = healthy ? limits.publish_24h == null ?
      '不限次数' : `${quota.remainingPublishes} / ${limits.publish_24h} 次` : '—';
    el('rhythm-interval').textContent = limits.min_interval_seconds ? `${limits.min_interval_seconds / 60} 分钟` : '—';
    const ready = test.publish_ready;
    el('rhythm-next').textContent = ready?.retry_after ? new Date(ready.retry_after).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ready?.ok ? '现在可发' : '核对账号后';
    el('rhythm-account').textContent = binding?.nickname || '新测试账号未绑定';
    const platform = platformState?.platforms?.xiaohongshu;
    el('rhythm-account-detail').textContent = binding
      ? `账号 ID ${binding.owner_id}${platform?.state === 'restricted' ? '；当前平台页面有访问限制，请先核对登录身份。' : '；每次提交仍需核对主站与创作中心。'}`
      : '主站与创作中心需登录同一个健康账号，读取稳定 ID 后才能绑定。当前可继续策划和制作。';
    el('rhythm-policy').textContent = test.message || '两次发帖间隔约10分钟，不设每日次数上限。';
    const queue = data?.queue || {};
    el('studio-queue-count').textContent = queue.ok === false ? '—' : String(queue.total || 0);
    el('queue-status').textContent = queue.ok === false ? '队列记录不可读，保留原文件并等待核对。' : test.background_paused ? '后台发布暂停 · 作品原样保留' : '后台按发帖间隔处理作品';
    const rows = [];
    const states = { queued: '等待', running: '执行中 · 需核对', uncertain: '结果待核对', done: '完成', cancelled: '已取消', failed: '失败' };
    for (const item of queue.items || []) {
      const row = document.createElement('article');
      const heading = document.createElement('strong');
      heading.textContent = item.title || '未命名作品';
      const stateText = document.createElement('span');
      stateText.textContent = states[item.state] || item.state || '未知状态';
      const copy = document.createElement('p');
      copy.textContent = `${item.account || '未标记昵称'} · ${item.sameAccount ? '当前绑定账号' : '其他或尚未绑定账号，保留'}${item.runAfter ? ` · ${new Date(item.runAfter).toLocaleString()}` : ''}`;
      row.append(heading, stateText, copy);
      rows.push(row);
    }
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'queue-empty';
      empty.textContent = queue.ok === false ? '无法读取候场作品' : '候场区很安静。作品排队后会出现在这里。';
      rows.push(empty);
    }
    if (queue.total > (queue.items || []).length) {
      const more = document.createElement('p');
      more.textContent = `显示最近 ${(queue.items || []).length} 项，共 ${queue.total} 项`;
      rows.push(more);
    }
    el('studio-queue').replaceChildren(...rows);
  }
  async function refreshStudio() {
    if (busy) return;
    busy = true;
    const token = ++requestId;
    el('rhythm-refresh').disabled = true;
    try {
      if (!window.ga?.getStudioState) throw new Error('当前连接尚未支持节奏面板');
      const data = await window.ga.getStudioState();
      if (token === requestId) renderRhythm(data);
    } catch (_) {
      if (token === requestId) renderRhythm({ ok: false, error: '暂时连不上节奏面板，请刷新重试。', queue: { ok: false } });
    } finally {
      busy = false;
      el('rhythm-refresh').disabled = false;
    }
  }
  el('rhythm-refresh').addEventListener('click', refreshStudio);
  window.addEventListener('automedia:platform', (event) => {
    platformState = event.detail;
    if (studioState) renderRhythm(studioState);
  });
  window.ga?.onBridgeReady(() => refreshStudio());
  setInterval(() => { if (!document.hidden && app.dataset.studioView === 'rhythm') refreshStudio(); }, 15000);
  updateWorkspace(currentWorkspace);
  updateActivity(null);
  const savedView = new URLSearchParams(location.search).get('view') || 'operations';
  showView(titles[savedView] ? savedView : 'operations');
})();
