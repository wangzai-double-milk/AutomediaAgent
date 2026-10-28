window.process = window.process || { platform: /Macintosh|Mac OS X/.test(navigator.userAgent) ? 'darwin' : 'win32' };
// AutoMedia Studio renderer.
// Handles UI state, sessions, streaming, slash commands.

'use strict';

// ─── State ────────────────────────────────────────────────────────────────
const pendingImages = []; // Uploaded media descriptors kept for legacy naming.
const state = {
  sessions: new Map(),      // localSessionId -> { id, bridgeSessionId, title, messages: [], cwd, config, diagnostics }
  activeId: null,
  bridgeReady: false,
  defaultConfig: { theme: 'auto', llmNo: 0, gaRoot: '' },
  modelProfiles: [],
  restartingBridge: false,
  bridgeNoticeMessage: null,
  mykeyReady: true,
  runtimeBySessionId: new Map(),
  platformStatus: null,
};

// Helper: get config/diagnostics for the active session (or defaults)
function getActiveConfig() {
  const sess = state.sessions.get(state.activeId);
  return sess ? sess.config : state.defaultConfig;
}
function getActiveDiagnostics() {
  const sess = state.sessions.get(state.activeId);
  return sess ? sess.diagnostics : [];
}

// ─── DOM refs ─────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const messagesEl = $('messages');
const inputEl = $('input');
const sendBtn = $('send-btn');
const sessionListEl = $('session-list');
const sessionTitleEl = $('session-title');
const statusBadge = $('status-badge');
const statusText = $('status-text');
const settingsModal = $('settings-modal');
const errorBanner = $('error-banner');
const diagnosticsPanel = $('diagnostics-panel');
const diagnosticsLogEl = $('diagnostics-log');
const liveStateEl = $('live-state');
const activityTitleEl = $('activity-title');
const activityDetailEl = $('activity-detail');
const activityTimelineEl = $('activity-timeline');
const browserLensEl = $('browser-lens');
const knowledgeListEl = $('knowledge-list');
const focusStageEl = $('focus-stage');
const focusTitleEl = $('focus-title');
const focusRoleEl = $('focus-role');
const focusCaptionEl = $('focus-caption');
let selectedWorkspaceItemId = null;
let currentWorkspace = null;
let browserWallLayout = 'auto';
let workspaceRenderKey = null;
let focusRenderKey = null;


// ─── Diagnostics ─────────────────────────────────────────────────────────
const MAX_DIAGNOSTICS = 200;

function diagnosticText(payload) {
  if (payload == null) return '';
  if (typeof payload === 'string') return payload;
  if (payload instanceof Error) return payload.stack || payload.message;
  try {
    return JSON.stringify(payload);
  } catch (_) {
    return String(payload);
  }
}

function addDiagnostic(level, message, payload) {
  const ts = new Date().toISOString();
  const detail = diagnosticText(payload);
  const diags = getActiveDiagnostics();
  diags.push({ ts, level, message, detail });
  if (diags.length > MAX_DIAGNOSTICS) diags.shift();
  renderDiagnostics();
}

function formatDiagnostics() {
  const diags = getActiveDiagnostics();
  if (diags.length === 0) return 'No diagnostics yet.';
  return diags.map((entry) => {
    const suffix = entry.detail ? `\n  ${entry.detail}` : '';
    return `[${entry.ts}] ${entry.level.toUpperCase()} ${entry.message}${suffix}`;
  }).join('\n');
}

function renderDiagnostics() {
  if (diagnosticsLogEl) diagnosticsLogEl.textContent = formatDiagnostics();
}

function openDiagnostics() {
  renderDiagnostics();
  diagnosticsPanel.classList.remove('hidden');
}

function closeDiagnostics() {
  diagnosticsPanel.classList.add('hidden');
}

async function copyDiagnostics() {
  const text = formatDiagnostics();
  try {
    await navigator.clipboard.writeText(text);
    addDiagnostic('info', 'Diagnostics copied to clipboard');
  } catch (err) {
    addDiagnostic('error', 'Failed to copy diagnostics', err);
    showError('Failed to copy diagnostics: ' + (err.message || err), null, null, { skipDiagnostic: true });
  }
}

function clearDiagnostics() {
  const sess = state.sessions.get(state.activeId);
  if (sess) sess.diagnostics = [];
  renderDiagnostics();
}

// ─── Agent Live telemetry ────────────────────────────────────────────────
let activityPollBusy = false;

function phaseLabel(phase, status) {
  if (status === 'error') return '异常';
  const labels = {
    idle: '待命中', boot: '准备中', thinking: '正在思考', search: '正在搜索',
    observe: '认真浏览', vision: '正在看素材', learn: '发现新知识', write: '整理结果',
    create: '正在制作', publish: '正在发布', guard: '继续探索',
    warning: '需要关注', done: '完成啦', action: '工作中',
  };
  return labels[phase] || (status === 'running' ? '工作中' : '待命中');
}

function setLiveState(data) {
  if (!liveStateEl) return;
  const status = data?.status || 'idle';
  liveStateEl.className = `live-state ${status === 'running' ? 'running' : status === 'error' ? 'error' : 'idle'}`;
  const label = liveStateEl.querySelector('span');
  if (label) label.textContent = phaseLabel(data?.phase, status);
  $('agent-live')?.classList.toggle('is-running', status === 'running');
}

function renderTimeline(items) {
  activityTimelineEl.innerHTML = '';
  const recent = (items || []).slice(-8).reverse();
  if (!recent.length) {
    activityTimelineEl.innerHTML = '<div class="timeline-empty">开始任务后，这里会像故事一样记录每一步</div>';
    return;
  }
  for (const item of recent) {
    const row = document.createElement('div');
    row.className = `timeline-item ${item.kind || 'action'}`;
    const timeEl = document.createElement('time');
    timeEl.textContent = item.ts || 'now';
    const content = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = item.label || item.step || '执行动作';
    content.appendChild(title);
    if (item.detail) {
      const detail = document.createElement('small');
      detail.textContent = item.detail;
      content.appendChild(detail);
    }
    row.append(timeEl, content);
    activityTimelineEl.appendChild(row);
  }
}

function renderBrowserLens(tabs) {
  browserLensEl.innerHTML = '';
  $('browser-count').textContent = `${(tabs || []).length} 个页面`;
  if (!tabs?.length) {
    browserLensEl.innerHTML = '<div class="lens-empty"><strong>暂时没有平台页面</strong></div>';
    return;
  }
  for (const tab of tabs) {
    const card = document.createElement('div');
    card.className = `browser-tab ${tab.platform || ''}${tab.active ? ' active' : ''}`;
    const title = document.createElement('strong');
    title.textContent = tab.title || tab.platform || 'Content page';
    const url = document.createElement('span');
    url.textContent = tab.url || '';
    card.append(title, url);
    browserLensEl.appendChild(card);
  }
}

function buildBrowserCard() {
  const card = document.createElement('article');
  const bar = document.createElement('header');
  const identity = document.createElement('div');
  const light = document.createElement('i');
  const platform = document.createElement('span');
  platform.className = 'win-platform';
  identity.append(light, platform);
  const title = document.createElement('strong');
  title.className = 'win-title';
  const position = document.createElement('b');
  position.className = 'win-index';
  bar.append(identity, title, position);
  const viewport = document.createElement('div');
  viewport.className = 'browser-viewport';
  const img = document.createElement('img');
  img.alt = '浏览器实时画面';
  img.loading = 'eager';
  const loading = document.createElement('div');
  loading.className = 'preview-loading';
  loading.innerHTML = '<i></i><span>正在同步画面</span>';
  viewport.append(img, loading);
  const footer = document.createElement('footer');
  footer.className = 'win-url';
  card.append(bar, viewport, footer);
  card._refs = { img, platform, title, position, footer, loading };
  return card;
}

function renderBrowserWall(tabs) {
  const wall = $('browser-wall');
  const items = tabs || [];
  $('wall-count').textContent = `${items.length} 个窗口`;
  wall.className = `browser-wall ${browserWallLayout} count-${Math.min(items.length, 6)}${items.length ? '' : ' empty'}`;

  if (!items.length) {
    if (wall._cards) wall._cards.clear();
    wall.innerHTML = '<div class="wall-empty"><i></i><strong>浏览器画面会出现在这里</strong><span>小红书、抖音和普通网页可以同时展示</span></div>';
    return;
  }
  // Remove empty placeholder if present (only leftover from empty state).
  if (!wall._cards || wall.querySelector('.wall-empty')) {
    wall.innerHTML = '';
    wall._cards = new Map();
  }
  const cards = wall._cards;
  const seen = new Set();

  items.forEach((tab, index) => {
    seen.add(tab.id);
    let card = cards.get(tab.id);
    if (!card) {
      card = buildBrowserCard();
      cards.set(tab.id, card);
    }
    const refs = card._refs;
    card.className = `browser-window ${tab.platform || 'web'}${tab.active ? ' active' : ''}`;
    refs.platform.textContent = tab.platform === 'xiaohongshu' ? '小红书' : tab.platform === 'douyin' ? '抖音' : '网页';
    const nextTitle = tab.title || tab.url || '浏览器窗口';
    if (refs.title.textContent !== nextTitle) refs.title.textContent = nextTitle;
    refs.position.textContent = String(index + 1).padStart(2, '0');
    const nextUrl = tab.url || '';
    if (refs.footer.textContent !== nextUrl) refs.footer.textContent = nextUrl;

    // Only swap the preview image when the version actually changes, so the
    // <img> element (and its loaded pixels) is reused between polls → no flicker.
    if (tab.previewReady) {
      const version = String(tab.previewVersion || '');
      if (card._previewVersion !== version) {
        card._previewVersion = version;
        refs.img.src = `/browser-preview/${encodeURIComponent(tab.id)}?v=${version || Date.now()}`;
      }
      refs.img.hidden = false;
      refs.loading.hidden = true;
    } else {
      refs.img.hidden = true;
      refs.loading.hidden = false;
    }
  });

  // Drop cards for tabs that no longer exist.
  for (const [id, card] of cards) {
    if (!seen.has(id)) {
      card.remove();
      cards.delete(id);
    }
  }
  // Re-order DOM to match the incoming order.
  items.forEach((tab) => {
    const card = cards.get(tab.id);
    if (card) wall.appendChild(card);
  });
}

function workspaceArtifactUrl(item) {
  const sess = state.sessions.get(state.activeId);
  if (!sess?.bridgeSessionId || !item?.relativePath) return '';
  return `/session/${encodeURIComponent(sess.bridgeSessionId)}/artifact?path=${encodeURIComponent(item.relativePath)}`;
}

function workspaceItems(workspace) {
  return [
    ...(workspace?.references || []),
    ...(workspace?.assets || []),
    ...(workspace?.documents || []).map((item) => ({
      ...item,
      id: `document:${item.relativePath}`,
      role: 'document',
      roleLabel: item.kind === 'report' ? '任务报告' : '中间文档',
      title: item.name,
    })),
    ...(workspace?.draft ? [{
      id: 'publish-draft', kind: 'document', role: 'draft', roleLabel: '发布文案',
      title: workspace.draft.title || '小红书发布文案', name: '发布文案',
      preview: [workspace.draft.desc, ...(workspace.draft.tags || [])].filter(Boolean).join('\n\n'),
    }] : []),
  ];
}

function setWorkspaceFocus(item) {
  if (!focusStageEl || !item) return;
  selectedWorkspaceItemId = item.id;
  const nextKey = JSON.stringify([state.activeId, item]);
  if (focusRenderKey === nextKey) {
    document.querySelectorAll('.asset-item, .document-item').forEach((element) => {
      element.classList.toggle('selected', element.dataset.itemId === item.id);
    });
    return;
  }
  focusRenderKey = nextKey;
  focusStageEl.innerHTML = '';
  focusStageEl.className = `focus-stage ${item.kind || 'document'} ${item.role || ''}`;
  focusTitleEl.textContent = item.title || item.name || '当前工作对象';
  focusRoleEl.textContent = item.roleLabel || 'CURRENT OBJECT';
  const openButton = $('focus-open');
  openButton.classList.toggle('hidden', !item.sourceUrl);
  openButton.dataset.url = item.sourceUrl || '';

  if (item.kind === 'video') {
    const video = document.createElement('video');
    video.src = workspaceArtifactUrl(item);
    video.controls = true;
    video.preload = 'metadata';
    focusStageEl.appendChild(video);
  } else if (item.kind === 'image') {
    const img = document.createElement('img');
    img.src = workspaceArtifactUrl(item);
    img.alt = item.title || item.name || '任务图片';
    img.loading = 'eager';
    focusStageEl.appendChild(img);
  } else {
    const documentPreview = document.createElement('article');
    documentPreview.className = 'focus-document';
    const heading = document.createElement('h4');
    heading.textContent = item.title || item.name || '文档';
    const content = document.createElement('pre');
    content.textContent = item.preview || '文件已经生成，可在任务目录中查看完整内容。';
    documentPreview.append(heading, content);
    focusStageEl.appendChild(documentPreview);
  }

  const captionLabel = document.createElement('span');
  captionLabel.textContent = item.roleLabel || '任务产物';
  const captionText = document.createElement('p');
  const meta = [];
  if (item.keyword) meta.push(`来自「${item.keyword}」搜索`);
  if (item.likes) meta.push(`${item.likes} 赞`);
  captionText.textContent = meta.join(' · ') || item.name || 'Agent 正在处理这个对象';
  focusCaptionEl.replaceChildren(captionLabel, captionText);

  document.querySelectorAll('.asset-item').forEach((element) => {
    element.classList.toggle('selected', element.dataset.itemId === item.id);
  });
  window.dispatchEvent(new CustomEvent('automedia:focus', { detail: item }));
}

function createMediaCard(item) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `asset-item ${item.role || ''}`;
  button.dataset.itemId = item.id;
  const thumb = document.createElement('div');
  thumb.className = 'asset-thumb';
  if (item.kind === 'video') {
    const video = document.createElement('video');
    video.src = workspaceArtifactUrl(item);
    video.muted = true;
    video.preload = 'metadata';
    thumb.appendChild(video);
    const badge = document.createElement('i');
    badge.textContent = '▶';
    thumb.appendChild(badge);
  } else {
    const img = document.createElement('img');
    img.src = workspaceArtifactUrl(item);
    img.alt = '';
    img.loading = 'lazy';
    thumb.appendChild(img);
  }
  const copy = document.createElement('div');
  const title = document.createElement('strong');
  title.textContent = item.title || item.name || '任务素材';
  const meta = document.createElement('span');
  meta.textContent = item.likes ? `${item.roleLabel} · ${item.likes} 赞` : item.roleLabel || '任务素材';
  copy.append(title, meta);
  button.append(thumb, copy);
  button.addEventListener('click', () => setWorkspaceFocus(item));
  return button;
}

function renderMediaGallery(element, items, emptyText) {
  element.innerHTML = '';
  if (!items?.length) {
    const empty = document.createElement('div');
    empty.className = 'gallery-empty';
    empty.textContent = emptyText;
    element.appendChild(empty);
    return;
  }
  items.forEach((item) => element.appendChild(createMediaCard(item)));
}

function renderDocumentGallery(workspace) {
  const element = $('document-gallery');
  element.innerHTML = '';
  const draft = workspace?.draft;
  if (draft) {
    const draftButton = document.createElement('button');
    draftButton.type = 'button';
    draftButton.className = 'document-item draft-document';
    draftButton.dataset.itemId = 'publish-draft';
    draftButton.innerHTML = '<i>文</i>';
    const copy = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = draft.title || '小红书发布文案';
    const meta = document.createElement('span');
    meta.textContent = `${draft.descLength || 0} 字 · ${(draft.tags || []).length} 个话题`;
    copy.append(title, meta);
    draftButton.append(copy);
    draftButton.addEventListener('click', () => {
      setWorkspaceFocus({
        id: 'publish-draft',
        kind: 'document',
        role: 'draft',
        roleLabel: '发布文案',
        title: draft.title || '小红书发布文案',
        name: '发布文案',
        preview: [draft.desc, ...(draft.tags || [])].filter(Boolean).join('\n\n'),
      });
    });
    element.appendChild(draftButton);
  }
  for (const documentItem of workspace?.documents || []) {
    const item = {
      ...documentItem,
      id: `document:${documentItem.relativePath}`,
      role: 'document',
      roleLabel: documentItem.kind === 'report' ? '任务报告' : '中间文档',
      title: documentItem.name,
    };
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'document-item';
    button.dataset.itemId = item.id;
    const icon = document.createElement('i');
    icon.textContent = item.kind === 'report' ? '报' : '稿';
    const copy = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = item.name;
    const meta = document.createElement('span');
    meta.textContent = item.kind === 'report' ? '研究报告 · 点击预览' : '任务文档 · 点击预览';
    copy.append(title, meta);
    button.append(icon, copy);
    button.addEventListener('click', () => setWorkspaceFocus(item));
    element.appendChild(button);
  }
  if (!element.children.length) {
    element.innerHTML = '<div class="gallery-empty">文案草稿、标签和研究报告会留在这里</div>';
  }
}

function renderPipeline(workspace) {
  const pipeline = $('task-pipeline');
  pipeline.innerHTML = '';
  if (!workspace?.stages?.length) {
    pipeline.innerHTML = '<span class="pipeline-empty">任务开始后，这里会显示从准备到交付的每一步</span>';
    return;
  }
  const mode = document.createElement('strong');
  mode.className = `pipeline-mode ${workspace.mode || 'idle'}`;
  mode.textContent = workspace.modeLabel || '当前任务';
  pipeline.appendChild(mode);
  workspace.stages.forEach((stage, index) => {
    const item = document.createElement('div');
    item.className = `pipeline-stage ${stage.status || 'pending'}`;
    const dot = document.createElement('i');
    dot.textContent = stage.status === 'done' ? '✓' : String(index + 1);
    const label = document.createElement('span');
    label.textContent = stage.label;
    item.append(dot, label);
    pipeline.appendChild(item);
  });
}

function renderDraft(draft) {
  const panel = $('draft-preview');
  panel.classList.toggle('hidden', !draft);
  if (!draft) return;
  panel.className = `draft-preview ${draft.status || 'preparing'}`;
  const statusLabels = {
    published: draft.scheduleAt ? '已提交定时发布' : '已经发布',
    prepared: '预填完成，尚未发布',
    draft: '已保存浏览器草稿',
    uncertain: '提交结果等待核对',
    error: '发布需要处理',
    preparing: '发布准备中',
  };
  panel.querySelector('.draft-status span').textContent = statusLabels[draft.status] || '发布准备中';
  $('draft-title').textContent = draft.title || '标题还在生成';
  $('draft-desc').textContent = draft.desc || `正文已准备 ${draft.descLength || 0} 字`;
  $('draft-visibility').textContent = draft.visibility || '等待确认可见范围';
  $('draft-image-count').textContent = draft.kind === 'podcast' ? '音频与封面'
    : draft.kind === 'longform' ? '长文排版图片'
    : draft.kind === 'video' ? '1 个视频' : `${(draft.images || []).length} 张图`;
  $('draft-tags').replaceChildren(...(draft.tags || []).map((value) => {
    const tag = document.createElement('span');
    tag.textContent = value;
    return tag;
  }));
}

function renderWorkspace(workspace) {
  currentWorkspace = workspace || null;
  const nextKey = JSON.stringify([state.activeId, workspace]);
  if (workspaceRenderKey === nextKey) return;
  workspaceRenderKey = nextKey;
  renderPipeline(workspace);
  const references = workspace?.references || [];
  const assets = workspace?.assets || [];
  $('reference-count').textContent = String(references.length);
  $('asset-count').textContent = String(assets.length);
  $('document-count').textContent = String((workspace?.documents || []).length + (workspace?.draft ? 1 : 0));
  renderMediaGallery($('reference-gallery'), references, 'Agent 浏览和分析过的帖子会留在这里');
  renderMediaGallery($('asset-gallery'), assets, '原图、改图版本和待发布素材会留在这里');
  renderDocumentGallery(workspace);
  renderDraft(workspace?.draft);

  const allItems = workspaceItems(workspace);
  const retained = allItems.find((item) => item.id === selectedWorkspaceItemId);
  const focus = retained || workspace?.focus || allItems[0];
  if (focus) {
    setWorkspaceFocus(focus);
  } else {
    selectedWorkspaceItemId = null;
    focusRenderKey = null;
    focusStageEl.className = 'focus-stage empty';
    focusStageEl.innerHTML = '<div class="focus-placeholder"><div class="focus-shapes"><i></i><i></i><i></i></div><strong>中间过程会出现在这里</strong><span>参考帖子、原始素材、改图版本和待发布内容都会自动汇集</span></div>';
    focusTitleEl.textContent = '当前工作对象';
    focusRoleEl.textContent = 'CURRENT OBJECT';
    $('focus-open').classList.add('hidden');
  }
  window.dispatchEvent(new CustomEvent('automedia:workspace', { detail: workspace }));
}

function renderKnowledge(items) {
  knowledgeListEl.innerHTML = '';
  $('knowledge-count').textContent = `${(items || []).length} 条新发现`;
  if (!items?.length) {
    knowledgeListEl.innerHTML = '<div class="knowledge-empty"><span>✦</span><strong>新知识正在路上</strong><small>只有真正不同的新发现才会记录在这里</small></div>';
    return;
  }
  for (const item of items.slice().reverse()) {
    const row = document.createElement('div');
    row.className = 'knowledge-item';
    const meta = document.createElement('div');
    meta.className = 'knowledge-meta';
    const platform = document.createElement('span');
    platform.textContent = item.platform || 'research';
    const itemId = document.createElement('span');
    itemId.textContent = item.itemId ? `#${item.itemId.slice(0, 18)}` : item.ts || '';
    meta.append(platform, itemId);
    const text = document.createElement('div');
    text.textContent = item.text || '';
    row.append(meta, text);
    knowledgeListEl.appendChild(row);
  }
}

function metricProgress(value, target) {
  if (!target) return 0;
  return Math.max(0, Math.min(100, Math.round((value / target) * 100)));
}

function renderMetrics(metrics = {}) {
  const searchTarget = metrics.searchTarget || 2;
  const readTarget = metrics.readTarget || 5;
  const noNewTarget = metrics.noNewTarget || 5;
  $('metric-search').textContent = `${metrics.searchRounds || 0}/${searchTarget}`;
  $('metric-read').textContent = `${metrics.itemsRead || 0}/${readTarget}`;
  $('metric-nonew').textContent = `${metrics.consecutiveNoNew || 0}/${noNewTarget}`;
  $('metric-media').textContent = String(metrics.failedMedia || 0);
  $('bar-search').style.width = `${metricProgress(metrics.searchRounds || 0, searchTarget)}%`;
  $('bar-read').style.width = `${metricProgress(metrics.itemsRead || 0, readTarget)}%`;
  $('bar-nonew').style.width = `${metricProgress(metrics.consecutiveNoNew || 0, noNewTarget)}%`;
  $('bar-media').style.width = metrics.failedMedia ? '100%' : '0%';
  const converged = (metrics.searchRounds || 0) >= searchTarget
    && (metrics.itemsRead || 0) >= readTarget
    && (metrics.consecutiveNoNew || 0) >= noNewTarget
    && !(metrics.failedMedia || 0);
  const searchProgress = metricProgress(metrics.searchRounds || 0, searchTarget);
  const readProgress = metricProgress(metrics.itemsRead || 0, readTarget);
  const noNewProgress = metricProgress(metrics.consecutiveNoNew || 0, noNewTarget);
  const journey = Math.round((searchProgress + readProgress + noNewProgress) / 3);
  $('journey-percent').textContent = `${journey}%`;
  $('journey-bar').style.width = `${journey}%`;
  $('convergence-status').textContent = converged ? '信息已经看充分' : journey ? '还在多看几条' : '等待出发';
  $('convergence-copy').textContent = converged
    ? '它已经从多个角度交叉验证，可以更有把握地给出结论。'
    : '它会多看、多比较，不会看两条内容就草率下结论。';
}

function renderActivity(data) {
  setLiveState(data);
  activityTitleEl.textContent = data?.current?.label || '等你安排任务';
  activityDetailEl.textContent = data?.current?.detail || (data?.taskDir ? '正在整理这次任务的观察和证据' : '下达一个目标后，你会在这里看到它搜索、浏览和学习的全过程。');
  const thoughts = {
    idle: '准备好替你逛遍内容平台',
    boot: '正在搭好这次任务的工作台',
    thinking: '正在理解你的目标',
    search: '换几个关键词，避免只看到一种答案',
    observe: '正在判断这条内容值不值得学',
    vision: '正在理解画面、字幕和视频节奏',
    create: '正在把想法变成可以发布的素材',
    publish: '正在检查图片、文案和发布设置',
    learn: '发现了和之前不一样的信息',
    write: '把零散发现整理成可用结论',
    guard: '证据还不够，再多看一些',
    warning: '遇到一点阻碍，正在换种办法',
    done: '任务完成，正在等你的下一句话',
  };
  $('agent-thought').textContent = thoughts[data?.phase] || (data?.status === 'running' ? '正在推进任务' : thoughts.idle);
  renderTimeline(data?.timeline || []);
  renderBrowserLens(data?.browser || []);
  renderBrowserWall(data?.browser || []);
  renderKnowledge(data?.knowledge || []);
  renderWorkspace(data?.workspace || null);
  renderMetrics(data?.metrics || {});
  window.dispatchEvent(new CustomEvent('automedia:activity', { detail: data }));
}

async function pollActivity() {
  if (activityPollBusy) return;
  const sess = state.sessions.get(state.activeId);
  if (!state.bridgeReady || !sess?.bridgeSessionId) {
    if (!sess) renderActivity(null);
    return;
  }
  activityPollBusy = true;
  try {
    const data = await window.ga.getActivity(sess.bridgeSessionId);
    renderActivity(data);
  } catch (err) {
    if (err?.status !== 404) addDiagnostic('warn', 'Activity telemetry unavailable', err);
  } finally {
    activityPollBusy = false;
  }
}

// ─── Platform readiness ─────────────────────────────────────────────────
let platformPollBusy = false;

function renderPlatformStatus(data) {
  state.platformStatus = data || null;
  window.dispatchEvent(new CustomEvent('automedia:platform', { detail: data }));
  const platforms = data?.platforms || {};
  for (const id of ['xiaohongshu', 'douyin']) {
    let item = platforms[id] || {
      state: 'open_required',
      label: '还没打开',
      detail: '请先打开平台并完成登录',
    };
    if (id === 'xiaohongshu' && item.state !== 'restricted'
        && !data?.testControl?.enabled
        && !data?.browserConnected && data?.desktop?.available) {
      const desktop = data.desktop;
      item = {
        ...item,
        state: desktop.loggedIn ? 'ready' : 'check_required',
        label: desktop.loggedIn ? '已就绪' : '桌面操作可用',
        detail: desktop.loggedIn
          ? `${desktop.account || '当前账号'}已登录，可以执行小红书任务`
          : '已发现现有 Chrome 小红书页面；任务开始时会核对登录账号',
      };
    }
    if (id === 'xiaohongshu' && data?.testControl?.enabled) {
      const test = data.testControl;
      const binding = test.binding;
      const available = test.write_ready || {};
      if (item.state !== 'restricted') {
        const after = available.retry_after ? new Date(available.retry_after).toLocaleString() : '';
        item = { ...item, state: available.ok ? 'ready' : 'check_required',
          label: !binding ? '等待测试账号' : available.ok ? '测试账号已绑定' : '测试冷却中',
          detail: `${binding?.nickname || '请登录并核对新测试账号'}${after ? `；下次可提交 ${after}` : ''}；后台发布已暂停`,
        };
      }
    }
    const card = document.querySelector(`[data-platform-card="${id}"]`);
    if (card) {
      card.className = `platform-check ${id === 'xiaohongshu' ? 'xhs' : 'douyin'} ${item.state}`;
      const detail = card.querySelector('[data-platform-detail]');
      const status = card.querySelector('[data-platform-state]');
      const open = card.querySelector('[data-platform-open]');
      if (detail) detail.textContent = item.detail || '';
      if (status) {
        status.className = `platform-state ${item.state}`;
        status.textContent = item.label || '请确认';
      }
      if (open) {
        open.textContent = item.state === 'ready' ? '已登录 · 重新打开' : '打开并登录';
      }
    }
    const mini = document.querySelector(`[data-platform-mini="${id}"]`);
    if (mini) mini.className = item.state || '';
  }
  const setupEntry = $('setup-entry');
  setupEntry?.classList.toggle('ready', !!data?.overallReady);
  const hint = $('setup-hint');
  if (hint) {
    hint.textContent = data?.overallReady
      ? '平台已就绪，可以开始任务。'
      : '小红书任务只需准备小红书；任务开始时会核对账号。';
  }
  const continueBtn = $('setup-continue');
  if (continueBtn) continueBtn.textContent = data?.overallReady ? '都准备好了，开始使用' : '暂时跳过，先看看';
}

async function pollPlatformStatus(force = false) {
  if (platformPollBusy || !state.bridgeReady) return;
  platformPollBusy = true;
  try {
    const data = await window.ga.getPlatformStatus(force);
    renderPlatformStatus(data);
  } catch (err) {
    addDiagnostic('warn', 'Platform readiness unavailable', err);
  } finally {
    platformPollBusy = false;
  }
}

function openSetupGate() {
  $('setup-gate')?.classList.remove('hidden');
  pollPlatformStatus(true);
}

function closeSetupGate() {
  $('setup-gate')?.classList.add('hidden');
  try { sessionStorage.setItem('automedia-setup-dismissed', '1'); } catch (_) {}
  // Restore focus outside the now-hidden dialog for keyboard/screen readers.
  $('input')?.focus();
}

async function openPlatform(platform) {
  const button = document.querySelector(`[data-platform-open="${platform}"]`);
  const previous = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = '正在打开…';
  }
  try {
    await window.ga.openPlatform(platform);
    window.setTimeout(() => pollPlatformStatus(true), 2200);
  } catch (err) {
    showError(`无法打开平台：${err.message || err}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previous || '打开并登录';
    }
  }
}

// ─── Markdown ─────────────────────────────────────────────────────────────
if (typeof marked !== 'undefined') {
  marked.setOptions({
    gfm: true,
    breaks: true,
    mangle: false,
    headerIds: false
  });
}

const ALLOWED_URI_RE = /^(https?:|mailto:|tel:|#|\/)/i;

function renderMarkdown(text) {
  if (typeof marked === 'undefined') {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  try {
    return sanitizeMarkdown(marked.parse(text));
  } catch (e) {
    return escapeHtml(text);
  }
}

function sanitizeMarkdown(html) {
  const template = document.createElement('template');
  template.innerHTML = String(html);
  const blockedTags = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'BASE', 'FORM', 'INPUT', 'BUTTON']);
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT);
  const removals = [];
  while (walker.nextNode()) {
    const el = walker.currentNode;
    if (blockedTags.has(el.tagName)) {
      removals.push(el);
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();
      if (name.startsWith('on') || name === 'srcdoc') {
        el.removeAttribute(attr.name);
        continue;
      }
      if ((name === 'href' || name === 'src' || name === 'xlink:href') && value && !ALLOWED_URI_RE.test(value)) {
        el.removeAttribute(attr.name);
      }
    }
    if (el.tagName === 'A') {
      el.setAttribute('rel', 'noopener noreferrer');
      el.setAttribute('target', '_blank');
    }
  }
  for (const el of removals) el.remove();
  return template.innerHTML;
}


function detectStructuredKind(line) {
  const trimmed = String(line || '').trim();
  const m = trimmed.match(/^(TOOL_RECALL|TOOL_REQUEST|TOOL_RESPONSE|COWORK|TUNR|TURN|ACTION|OBSERVATION|THOUGHT|TOOL)[\s:_-]*(.*)$/i);
  if (m) return { kind: m[1].toUpperCase(), rest: (m[2] || '').trim() };

  // The bridge currently streams tool calls/results as plain
  // assistant text, not as ACP `tool_call` notifications. Recognize the real
  // XML-ish markers so streamed code_run/file_read/etc. blocks are folded.
  if (/^<function_calls\b[^>]*>/i.test(trimmed) || /^<invoke\b[^>]*\bname=["'][^"']+["'][^>]*>/i.test(trimmed)) {
    return { kind: 'TOOL_CALL', rest: trimmed };
  }
  if (/^<function_results\b[^>]*>/i.test(trimmed) || /^<result\b[^>]*>/i.test(trimmed)) {
    return { kind: 'TOOL_RESULT', rest: trimmed };
  }
  return null;
}

function isStructuredClosingLine(line, kind, textSoFar) {
  const trimmed = String(line || '').trim();
  const block = String(textSoFar || '');
  if (kind === 'TOOL_CALL') {
    if (/^<\/function_calls>$/i.test(trimmed)) return true;
    // Single-invoke streams may omit the <function_calls> wrapper.
    return /^<\/invoke>$/i.test(trimmed) && !/^\s*<function_calls\b/im.test(block);
  }
  if (kind === 'TOOL_RESULT') {
    if (/^<\/function_results>$/i.test(trimmed)) return true;
    // Single-result streams may omit the <function_results> wrapper.
    return /^<\/result>$/i.test(trimmed) && !/^\s*<function_results\b/im.test(block);
  }
  return false;
}

function summarizeStructuredBlock(kind, text) {
  const raw = String(text || '');
  // For all kinds: prefer <summary> tag content only
  const summaryMatch = raw.match(/<summary>\s*([\s\S]*?)\s*<\/summary>/i);
  if (summaryMatch) {
    const line = summaryMatch[1].trim().split('\n')[0] || kind;
    return line.length > 96 ? line.slice(0, 96) + '…' : line;
  }
  // No summary tag: show kind only (no body text leakage)
  if (kind === 'LLM_RUNNING') return 'LLM Running';
  return kind;
}

const LLM_RUNNING_MARKER_RE = /(\**LLM Running \(Turn \d+\) \.\.\.\**)/g;

function splitLLMRunningSegments(raw) {
  const placeholders = [];
  const protect = value => {
    placeholders.push(value);
    return `\u0000PH${placeholders.length - 1}\u0000`;
  };
  let safe = String(raw || '').replace(/`{4,}[\s\S]*?`{4,}/g, protect);
  safe = safe.replace(/`{4,}[^`][\s\S]*$/g, protect);
  const restore = value => String(value || '').replace(/\u0000PH(\d+)\u0000/g, (_, i) => placeholders[Number(i)] || '');
  const parts = safe.split(LLM_RUNNING_MARKER_RE).map(restore);
  if (parts.length < 4) return null;
  const segments = [];
  if (parts[0] && parts[0].trim()) segments.push({ kind: 'agent_message_chunk', text: parts[0].trimEnd() });
  const turns = [];
  for (let i = 1; i < parts.length; i += 2) {
    turns.push({ marker: parts[i] || '', content: parts[i + 1] || '' });
  }
  turns.forEach((turn, idx) => {
    const text = `${turn.marker}${turn.content}`.trimEnd();
    if (!text) return;
    // Match Streamlit: historical/intermediate LLM Running turns are folded;
    // the latest turn remains plain so final answers are not hidden by default.
    segments.push({ kind: idx < turns.length - 1 ? 'LLM_RUNNING' : 'agent_message_chunk', text });
  });
  return segments.length ? segments : null;
}

function splitStructuredSegments(text) {
  const raw = String(text || '');
  const llmSegments = splitLLMRunningSegments(raw);
  if (llmSegments) return llmSegments;
  const lines = raw.split(/\r?\n/);
  const segments = [];
  let buf = [];
  let kind = 'agent_message_chunk';
  let inFence = false;
  const flush = () => {
    if (!buf.length) return;
    segments.push({ kind, text: buf.join('\n').trimEnd() });
    buf = [];
  };
  for (const line of lines) {
    const fence = /^\s*```/.test(line);
    const hit = !inFence ? detectStructuredKind(line) : null;
    if (hit && hit.kind !== kind) {
      flush();
      kind = hit.kind;
      buf.push(line);
    } else {
      buf.push(line);
    }
    if (!inFence && kind !== 'agent_message_chunk' && isStructuredClosingLine(line, kind, buf.join('\n'))) {
      flush();
      kind = 'agent_message_chunk';
    }
    if (fence) inFence = !inFence;
  }
  flush();
  return segments.length ? segments : [{ kind: 'agent_message_chunk', text: raw }];
}

function hasUnfencedStructuredMarker(text) {
  let inFence = false;
  for (const line of String(text || '').split(/\r?\n/)) {
    const fence = /^\s*```/.test(line);
    if (!inFence && detectStructuredKind(line)) return true;
    if (fence) inFence = !inFence;
  }
  return false;
}

function shouldFoldSegment(kind, text) {
  return kind !== 'agent_message_chunk' || hasUnfencedStructuredMarker(text);
}

function getNowMs() {
  if (typeof performance !== 'undefined' && typeof performance.now === 'function') return Math.round(performance.now());
  return Date.now();
}

function formatDuration(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value < 0) return '';
  if (value < 1000) return `${Math.max(1, Math.round(value))}ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}s`;
  const minutes = Math.floor(value / 60000);
  const seconds = Math.round((value % 60000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function formatTaskElapsed(ms, ended) {
  const totalSeconds = Math.max(0, Math.floor(Number(ms) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const parts = [];
  if (hours) parts.push(`${hours}h`);
  if (hours || minutes) parts.push(`${minutes}min`);
  parts.push(`${seconds}s`);
  const elapsed = parts.join(' ');
  if (ended) return `Done ✓ ${elapsed}`;
  const spinner = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';
  const frame = spinner[Math.floor(Date.now() / 1000) % spinner.length];
  return `${frame} ${elapsed}`;
}

function getSessionRuntime(sess) {
  if (!sess) return null;
  const sessionId = sess.id;
  let runtime = state.runtimeBySessionId.get(sessionId);
  if (!runtime) {
    runtime = {
      busy: false,
      currentTurnEl: null,
      lastMessageType: null,
      taskStartedAt: 0,
      taskTimerId: null,
      assistantDraft: null,

    };
    state.runtimeBySessionId.set(sessionId, runtime);
  }
  return runtime;
}

function getActiveSessionRuntime() {
  const sess = state.sessions.get(state.activeId);
  return sess ? getSessionRuntime(sess) : null;
}

function findSessionByBridgeId(bridgeSessionId) {
  if (!bridgeSessionId) return state.sessions.get(state.activeId) || null;
  for (const sess of state.sessions.values()) {
    if (sess.bridgeSessionId === bridgeSessionId || sess.id === bridgeSessionId) return sess;
  }
  return null;
}

function isActiveSession(sess) {
  return !!sess && sess.id === state.activeId;
}

function withSessionDom(sess, fn) {
  if (isActiveSession(sess)) return fn();
  return null;
}

function updateTaskRuntimeBadges(now = getNowMs()) {
  const badges = document.querySelectorAll('.task-elapsed[data-started-at]');
  badges.forEach((badge) => {
    const startedAt = Number(badge.dataset.startedAt || 0);
    if (startedAt) badge.textContent = formatTaskElapsed(now - startedAt);
  });
}

function clearTaskTimer(sess) {
  const runtime = sess ? getSessionRuntime(sess) : getActiveSessionRuntime();
  if (runtime?.taskTimerId) {
    clearInterval(runtime.taskTimerId);
    runtime.taskTimerId = null;
  }
}

function startTaskTimer(sess, startedAt = getNowMs()) {
  const runtime = getSessionRuntime(sess);
  clearTaskTimer(sess);
  runtime.taskStartedAt = Number(startedAt) || getNowMs();
  if (isActiveSession(sess)) updateTaskRuntimeBadges(runtime.taskStartedAt);
  runtime.taskTimerId = setInterval(() => {
    if (isActiveSession(sess)) updateTaskRuntimeBadges();
  }, 1000);
}

function stopTaskTimer(sess) {
  if (isActiveSession(sess)) updateTaskRuntimeBadges();
  const runtime = getSessionRuntime(sess);
  clearTaskTimer(sess);
  runtime.taskStartedAt = 0;
}

function taskElapsedBadge(startedAt, endedAt) {
  const start = Number(startedAt || 0);
  if (!start) return '';
  const end = Number(endedAt || 0);
  const now = end || getNowMs();
  const ended = !!end;
  const liveAttr = ended ? 'data-ended="1"' : `data-started-at="${escapeHtml(String(start))}"`;
  return `<span class="task-elapsed" ${liveAttr}>${escapeHtml(formatTaskElapsed(now - start, ended))}</span>`;
}

function ensureAssistantTaskElapsed(wrap, startedAt, endedAt) {
  if (!wrap) return null;
  const html = taskElapsedBadge(startedAt, endedAt);
  let badge = wrap.querySelector(':scope > .task-elapsed');
  if (!html) {
    badge?.remove();
    return null;
  }
  if (!badge) {
    wrap.insertAdjacentHTML('afterbegin', html);
    badge = wrap.querySelector(':scope > .task-elapsed');
  } else {
    const holder = document.createElement('div');
    holder.innerHTML = html;
    badge.replaceWith(holder.firstElementChild);
    badge = wrap.querySelector(':scope > .task-elapsed');
  }
  return badge;
}

function turnLabelForSegment(seg, index) {
  const summary = summarizeStructuredBlock(seg.kind, seg.text);
  if (seg.kind === 'LLM_RUNNING') return summary || `Turn ${index + 1}`;
  if (seg.kind === 'TOOL_CALL') return 'Tool';
  if (seg.kind === 'TOOL_RESULT') return 'Result';
  return summary || seg.kind || `Turn ${index + 1}`;
}

function nextTurnIndexForWrap(wrap) {
  const current = Number(wrap?.dataset?.turnIndex || 0) || 0;
  const next = current + 1;
  if (wrap) wrap.dataset.turnIndex = String(next);
  return next;
}

function turnHeaderLabel(index, label) {
  return `Turn ${index} : ${label || 'response'}`;
}

function groupIntoTurns(segments) {
  let foldIndex = 0;
  return (segments || []).map((seg) => {
    if (!shouldFoldSegment(seg.kind, seg.text)) return { type: 'plain', segment: seg };
    const index = ++foldIndex;
    return {
      type: 'turn',
      index,
      label: turnLabelForSegment(seg, index - 1),
      segment: seg
    };
  });
}

function extractTagBody(text, tag) {
  const escapedTag = String(tag || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = '<' + escapedTag + '\\b[^>]*>([\\s\\S]*?)<\\/' + escapedTag + '>';
  const m = String(text || '').match(new RegExp(pattern, 'i'));
  return m ? m[1].trim() : '';
}

function parseToolDetails(kind, text) {
  const raw = String(text || '');
  if (kind === 'TOOL_CALL') {
    const invoke = raw.match(/<invoke\b[^>]*\bname=["']([^"']+)["'][^>]*>/i);
    const tool = invoke ? invoke[1] : '';
    const params = extractTagBody(raw, 'parameter') || extractTagBody(raw, 'arguments') || extractTagBody(raw, 'args');
    const jsonish = params || (raw.match(/<invoke\b[^>]*>[\s\S]*?<\/invoke>/i)?.[0] || '').replace(/<\/?invoke[^>]*>/gi, '').trim();
    if (tool || jsonish) return { title: tool ? `Tool: ${tool}` : 'Tool call', tool, args: jsonish };
  }
  if (kind === 'TOOL_RESULT') {
    const result = extractTagBody(raw, 'result') || raw.replace(/<\/?function_results[^>]*>/gi, '').trim();
    if (result) return { title: 'Tool result', tool: 'result', args: result };
  }
  return null;
}

function renderToolDetailInto(container, seg) {
  const detail = parseToolDetails(seg.kind, seg.text);
  if (!detail) return;
  const detailTurn = document.createElement('div');
  detailTurn.className = 'turn tool-detail-turn';
  const header = document.createElement('button');
  header.type = 'button';
  header.className = 'turn-header tool-detail-header';
  header.innerHTML = `<span class="turn-caret">▼</span><span class="turn-tag">${escapeHtml(detail.title)}</span><span class="turn-summary">args</span>`;
  header.addEventListener('click', () => detailTurn.classList.toggle('collapsed'));
  const body = document.createElement('div');
  body.className = 'turn-body md tool-detail-body';
  const codeText = `Tool: ${detail.tool || detail.title.replace(/^Tool:\s*/, '') || 'tool'}\nargs:\n${detail.args || ''}`;
  body.innerHTML = `<pre class="tool-args-code"><code>${escapeHtml(codeText)}</code></pre>`;
  detailTurn.appendChild(header);
  detailTurn.appendChild(body);
  container.appendChild(detailTurn);
}

function renderTurnTreeInto(container, turn) {
  const seg = turn.segment;
  const node = document.createElement('div');
  node.className = 'turn collapsed structured-turn turn-group';
  node.dataset.kind = seg.kind;
  node.dataset.buf = seg.text;
  const header = document.createElement('button');
  header.type = 'button';
  header.className = 'turn-header';
  header.innerHTML = `<span class="turn-caret">▼</span><span class="turn-tag">${escapeHtml(turnHeaderLabel(turn.index, turn.label))}</span>`;
  header.addEventListener('click', () => node.classList.toggle('collapsed'));
  const body = document.createElement('div');
  body.className = 'turn-body md';
  const hasToolDetail = Boolean(parseToolDetails(seg.kind, seg.text));
  renderToolDetailInto(body, seg);
  if (!hasToolDetail) {
    const rendered = document.createElement('div');
    rendered.className = 'turn-rendered-md';
    rendered.innerHTML = renderMarkdown(seg.text);
    body.appendChild(rendered);
  }
  node.appendChild(header);
  node.appendChild(body);
  container.appendChild(node);
}

/**
 * Extract <summary>...</summary> from text, render it as a faded italic hint,
 * and return the remaining text. If no summary tag found, returns text unchanged.
 */
/**
 * Strip leading <summary> and <think> tags from text.
 * Returns { summary, think, remaining } where summary/think are the extracted
 * content strings (or null), and remaining is the text to render as markdown.
 */
function stripLeadingMetaTags(text) {
  let remaining = text;
  let summary = null;
  let think = null;
  // Strip <summary>...</summary> at start
  const sumRe = /^<summary>([\s\S]*?)<\/summary>\s*/i;
  const sumM = remaining.match(sumRe);
  if (sumM) {
    summary = sumM[1].trim();
    remaining = remaining.slice(sumM[0].length);
  }
  // Strip <think>...</think> at start (or after summary)
  const thinkRe = /^<think>([\s\S]*?)<\/think>\s*/i;
  const thinkM = remaining.match(thinkRe);
  if (thinkM) {
    think = thinkM[1].trim();
    remaining = remaining.slice(thinkM[0].length);
  }
  return { summary, think, remaining };
}

function extractAndRenderSummary(container, text) {
  const { summary, think, remaining } = stripLeadingMetaTags(text);
  if (summary) {
    const hint = document.createElement('div');
    hint.className = 'summary-hint';
    hint.textContent = summary;
    container.appendChild(hint);
  }
  if (think) {
    const thinkEl = document.createElement('div');
    thinkEl.className = 'think-hint';
    thinkEl.textContent = think;
    container.appendChild(thinkEl);
  }
  return remaining;
}

function renderStructuredMarkdownInto(container, text, options = {}) {
  if (window.AutoMediaExecution?.render(container, text, {
    key: `${state.activeId || 'none'}:${options.messageId || options.taskStartedAt || 'live'}`,
    live: !!options.live,
    status: options.status,
    markdown: renderMarkdown,
  })) return;
  const segments = splitStructuredSegments(text);
  container.innerHTML = '';
  if (segments.length === 1 && !shouldFoldSegment(segments[0].kind, segments[0].text)) {
    const remaining = extractAndRenderSummary(container, text);
    if (remaining) container.insertAdjacentHTML('beforeend', renderMarkdown(remaining));
    return;
  }
  for (const item of groupIntoTurns(segments)) {
    if (item.type === 'plain') {
      const plain = document.createElement('div');
      plain.className = 'md';
      const remaining = extractAndRenderSummary(plain, item.segment.text);
      if (remaining) plain.insertAdjacentHTML('beforeend', renderMarkdown(remaining));
      container.appendChild(plain);
      continue;
    }
    renderTurnTreeInto(container, item);
  }
}

// ─── Copy button injection for code blocks and pre blocks ─────────────────
function injectCopyButtons(container) {
  if (!container) return;
  const blocks = container.querySelectorAll('pre');
  blocks.forEach(pre => {
    if (pre.querySelector('.copy-btn')) return; // already injected
    pre.style.position = 'relative';
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.setAttribute('aria-label', 'Copy code');
    btn.addEventListener('click', () => {
      const code = pre.querySelector('code') || pre;
      navigator.clipboard.writeText(code.textContent).then(() => {
        btn.textContent = '✓ Copied';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
      }).catch(() => {
        btn.textContent = '✗ Failed';
        setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
      });
    });
    pre.appendChild(btn);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// ─── Session management ──────────────────────────────────────────────────
function isUntitledSessionTitle(title) {
  return !title || /^new\s+chat$/i.test(String(title).trim());
}

function createLocalSession(id, title, bridgeSessionId = id) {
  const sess = {
    id, bridgeSessionId, title: title || 'New chat', messages: [], cwd: null,
    untitled: isUntitledSessionTitle(title),
    config: { ...state.defaultConfig },
    diagnostics: [],
  };
  getSessionRuntime(sess);
  // Keep freshly-created chats visually quiet: the empty state is enough guidance.
  state.sessions.set(id, sess);
  renderSessionList();
  return sess;
}

function setActiveSession(id) {
  // Save scroll position of current session before switching
  if (state.activeId) {
    const prevRuntime = state.runtimeBySessionId.get(state.activeId);
    if (prevRuntime) prevRuntime.scrollPos = messagesEl.scrollTop;
  }
  state.activeId = id;
  selectedWorkspaceItemId = null;
  currentWorkspace = null;
  const sess = state.sessions.get(id);
  if (!sess) return;
  sessionTitleEl.textContent = sess.title;
  renderMessages();
  renderSessionList();
  renderDiagnostics();
  pollActivity();
  const runtime = getSessionRuntime(sess);
  setBusy(runtime.busy, runtime.busy ? 'Agent is responding…' : null, sess);
  // When switching to a session that is still running, ensure the live draft
  // is rendered immediately and polling is active (it may have been started
  // earlier but its render calls were no-ops because the session wasn't active).
  if (runtime.busy) {
    const draft = runtime.assistantDraft;
    if (draft && !draft.finalized) {
      renderAssistantDraftInPlace(sess, draft);
    }
    // Restart polling if it stopped (e.g. page reload or race condition)
    if (!runtime.polling) {
      runtime.forcePollOnce = true;
      pollSessionMessages(sess);
    } else {
      // Polling is running but was rendering as no-op while we were away.
      // Do an immediate one-shot poll to refresh the view right now.
      (async () => {
        try {
          const res = await window.ga.pollSession(sess.bridgeSessionId || sess.id, runtime.lastPolledMessageId || 0);
          if (res?.error) return;
          const result = res.result || res;
          for (const msg of (result.messages || [])) upsertPolledMessage(sess, msg, { partial: false });
          if (result.partial) upsertPolledMessage(sess, result.partial, { partial: true });
        } catch(e) { /* ignore, regular polling will handle it */ }
      })();
    }
  }
}

function renderSessionList() {
  // Preserve the + button (must remain in DOM as anchor for insertBefore)
  const newBtn = document.getElementById('new-session-btn');
  // Remove only existing tab elements, never the + button
  sessionListEl.querySelectorAll('.session-tab').forEach((el) => el.remove());
  if (state.sessions.size === 0) return;
  for (const sess of state.sessions.values()) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'session-tab' + (sess.id === state.activeId ? ' active' : '');
    item.setAttribute('role', 'tab');
    item.setAttribute('aria-selected', sess.id === state.activeId ? 'true' : 'false');
    item.setAttribute('data-session-id', sess.id);
    item.title = sess.title;
    // ─── Drag-and-drop reorder ───
    item.draggable = true;
    item.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain', sess.id);
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      sessionListEl.querySelectorAll('.session-tab.drag-over').forEach(el => el.classList.remove('drag-over'));
    });
    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      item.classList.add('drag-over');
    });
    item.addEventListener('dragleave', () => {
      item.classList.remove('drag-over');
    });
    item.addEventListener('drop', (e) => {
      e.preventDefault();
      item.classList.remove('drag-over');
      const draggedId = e.dataTransfer.getData('text/plain');
      if (draggedId && draggedId !== sess.id) {
        reorderSession(draggedId, sess.id);
      }
    });
    // Per-tab status dot
    const dot = document.createElement('span');
    dot.className = 'tab-dot';
    const runtime = getSessionRuntime(sess);
    if (runtime && runtime.busy) dot.classList.add('busy');
    item.appendChild(dot);
    // Tab label
    const label = document.createElement('span');
    label.className = 'tab-label';
    label.textContent = sess.title;
    item.appendChild(label);
    // Close button (Chrome-style ×)
    const closeBtn = document.createElement('span');
    closeBtn.className = 'tab-close';
    closeBtn.setAttribute('role', 'button');
    closeBtn.setAttribute('aria-label', 'Close tab');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      closeSession(sess.id);
    });
    item.appendChild(closeBtn);
    item.addEventListener('click', () => setActiveSession(sess.id));
    sessionListEl.insertBefore(item, newBtn);
  }
}

// ─── Tab drag reorder helper ─────────────────────────────────────────────────
function reorderSession(draggedId, targetId) {
  const entries = [...state.sessions.entries()];
  const fromIdx = entries.findIndex(([id]) => id === draggedId);
  const toIdx = entries.findIndex(([id]) => id === targetId);
  if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return;
  const [moved] = entries.splice(fromIdx, 1);
  entries.splice(toIdx, 0, moved);
  state.sessions = new Map(entries);
  renderSessionList();
}

function closeSession(id) {
  if (state.sessions.size <= 1) return; // Don't close the last tab
  // Notify bridge to delete this session
  const sess = state.sessions.get(id);
  if (sess && sess.bridgeSessionId) {
    const bridgeUrl = window.ga.bridgeUrl || 'http://127.0.0.1:14168';
    fetch(`${bridgeUrl}/session/${sess.bridgeSessionId}`, { method: 'DELETE' }).catch(() => {});
  }
  const keys = [...state.sessions.keys()];
  const idx = keys.indexOf(id);
  state.sessions.delete(id);
  state.runtimeBySessionId.delete(id);
  if (state.activeId === id) {
    // Switch to adjacent tab (prefer right, fallback left)
    const newIdx = Math.min(idx, keys.length - 2);
    const remaining = [...state.sessions.keys()];
    setActiveSession(remaining[Math.max(0, Math.min(newIdx, remaining.length - 1))]);
  } else {
    renderSessionList();
  }
}

async function newSession() {
  if (!state.bridgeReady) {
    showError('Bridge is not ready yet. Please wait a moment.');
    return;
  }
  const previousSess = state.sessions.get(state.activeId) || null;
  // Don't mark previousSess as busy - it's not doing anything
  // Just show status text without changing any tab dot
  const statusEl = $('status');
  if (statusEl) statusEl.textContent = 'Creating session…';
  let createdSess = null;
  try {
    const cwd = await getCwd();
    const res = await window.ga.rpc('session/new', { cwd, mcp_servers: [] });
    if (res.error) throw new Error(typeof res.error === 'string' ? res.error : (res.error.message || JSON.stringify(res.error)));
    const bridgeSessionId = res.sessionId;
    const localSessionId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    createdSess = createLocalSession(localSessionId, 'New chat', bridgeSessionId);
    createdSess.cwd = cwd;
    setActiveSession(localSessionId);
  } catch (e) {
    showError('Failed to create session: ' + e.message);
  } finally {
    setBusy(false, null, createdSess || previousSess);
  }
}

async function getCwd() {
  // Use GA root as default cwd
  const status = await window.ga.checkStatus();
  return status.gaRoot;
}

// ─── Messages rendering ──────────────────────────────────────────────────
// DOM cache: sessionId -> { fragment, scrollTop }
const _domCache = new Map();

function renderMessages() {
  const sess = state.sessions.get(state.activeId);
  const runtime = sess ? getSessionRuntime(sess) : null;

  // Save current DOM + scroll to cache for previous session
  if (state._prevRenderedId && state._prevRenderedId !== state.activeId) {
    const frag = document.createDocumentFragment();
    while (messagesEl.firstChild) frag.appendChild(messagesEl.firstChild);
    _domCache.set(state._prevRenderedId, {
      fragment: frag,
      scrollTop: runtime ? (state.runtimeBySessionId.get(state._prevRenderedId)?.scrollPos ?? 0) : 0,
    });
  }

  if (runtime) {
    runtime.currentTurnEl = null;
    runtime.lastMessageType = null;
  }

  const hasSavedMessages = !!sess && sess.messages.length > 0;
  const hasDraft = !!runtime?.assistantDraft && !runtime.assistantDraft.finalized;
  if (!sess || (!hasSavedMessages && !hasDraft)) {
    messagesEl.innerHTML = '';
    messagesEl.classList.add('empty');
    messagesEl.innerHTML = `
      <div class="empty-state">
        <div class="empty-kicker">导演，请说</div>
        <div class="empty-title">今天，开拍什么？</div>
        <div class="empty-sub">给个目标，看它搜索、拆解，再把想法变成作品。</div>
        <div class="starter-grid">
          <button class="starter-card" data-prompt="调研小红书和抖音最近的内容趋势，持续看到没有新知识为止"><span class="starter-icon">↗</span><strong>找近期趋势</strong><small>看看大家最近都在聊什么</small></button>
          <button class="starter-card" data-prompt="分析我所在赛道的爆款内容，整理可复用的拍摄和文案方法"><span class="starter-icon">✦</span><strong>拆解爆款</strong><small>学会高互动内容怎么做</small></button>
          <button class="starter-card" data-prompt="检查当前任务目录的研究成果，告诉我学到了什么、还缺什么"><span class="starter-icon">◎</span><strong>总结新发现</strong><small>用简单的话告诉我学到了什么</small></button>
        </div>
      </div>`;
    activateStarterCards(messagesEl);
    state._prevRenderedId = state.activeId;
    return;
  }

  messagesEl.classList.remove('empty');

  // Try to restore from cache
  const cached = _domCache.get(state.activeId);
  if (cached) {
    messagesEl.innerHTML = '';
    messagesEl.appendChild(cached.fragment);
    _domCache.delete(state.activeId);
    // If there's a live draft, the cached DOM is stale — re-render the draft portion
    if (hasDraft) {
      // Remove the stale assistant wrap (last unfinalized msg-assistant element)
      const last = messagesEl.lastElementChild;
      if (last?.classList?.contains('msg-assistant') && last.dataset.finalized !== '1') {
        last.remove();
      }
      renderAssistantDraft(sess, runtime.assistantDraft);
    }
    messagesEl.scrollTop = cached.scrollTop;
  } else {
    messagesEl.innerHTML = '';
    for (const m of sess.messages) renderMessage(m, false);
    if (hasDraft) renderAssistantDraft(sess, runtime.assistantDraft);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  state._prevRenderedId = state.activeId;
}

function prepareMessagesForContent() {
  if (messagesEl.classList.contains('empty')) messagesEl.innerHTML = '';
  messagesEl.classList.remove('empty');
}

function renderMessage(msg, append = true) {
  prepareMessagesForContent();

  if (msg.role === 'user') {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-user';
    let imagesHtml = '';
    const attachments = msg.attachments || [];
    const ids = msg.image_ids || [];
    if (attachments.length > 0) {
      imagesHtml = '<div class="user-images">' + attachments.map(item => {
        const url = item.previewUrl || sessionStorage.getItem('img:' + item.id);
        if (!url) return '<span class="user-msg-thumb-placeholder">素材</span>';
        if (item.kind === 'video') {
          return `<video src="${escapeHtml(url)}" class="user-msg-thumb user-msg-video" controls preload="metadata"></video>`;
        }
        return `<img src="${escapeHtml(url)}" class="user-msg-thumb" alt="${escapeHtml(item.name || '图片')}" />`;
      }).join('') + '</div>';
    } else if (ids.length > 0) {
      imagesHtml = '<div class="user-images">' + ids.map(id => {
        const dataUrl = sessionStorage.getItem('img:' + id);
        if (dataUrl) {
          return `<img src="${dataUrl}" class="user-msg-thumb" />`;
        }
        return `<span class="user-msg-thumb-placeholder" title="Image expired">🖼</span>`;
      }).join('') + '</div>';
    }
    wrap.innerHTML = `<div class="bubble">${imagesHtml}${escapeHtml(msg.content)}</div>`;
    messagesEl.appendChild(wrap);
    const sess = state.sessions.get(state.activeId);
    const runtime = sess ? getSessionRuntime(sess) : null;
    if (runtime) {
      runtime.currentTurnEl = null; // reset turn grouping on user message
      runtime.lastMessageType = 'user';
    }
  } else if (msg.role === 'system') {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-system';
    wrap.textContent = msg.content;
    messagesEl.appendChild(wrap);
  } else if (msg.role === 'error') {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-error';
    wrap.textContent = msg.content;
    messagesEl.appendChild(wrap);
    const sess = state.sessions.get(state.activeId);
    const runtime = sess ? getSessionRuntime(sess) : null;
    if (runtime) runtime.currentTurnEl = null;
  } else if (msg.role === 'assistant') {
    // Final full message (when reloading from state)
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-assistant';
    if (msg.segments) {
      ensureAssistantTaskElapsed(wrap, msg.taskStartedAt, msg.taskEndedAt);
      for (const seg of msg.segments) {
        wrap.appendChild(buildTurn(seg.kind, seg.text, seg.collapsed, nextTurnIndexForWrap(wrap)));
      }
    } else {
      const body = document.createElement('div');
      body.className = 'assistant-response md';
      ensureAssistantTaskElapsed(wrap, msg.taskStartedAt, msg.taskEndedAt);
      const cleanContent = (msg.content || '').replace(/\n*`{5}\n*\[Info\] Final response to user\.\n*`{5}\s*$/, '');
      renderStructuredMarkdownInto(body, cleanContent, { messageId: msg.id, taskStartedAt: msg.taskStartedAt });
      injectCopyButtons(body);
      wrap.appendChild(body);
      wrap.classList.toggle('execution-message', !!body._executionFeed);
    }
    injectCopyButtons(wrap);
    messagesEl.appendChild(wrap);
  }
  if (append) scrollToBottom();
}

function buildTurn(kind, text, collapsed, index) {
  const turn = document.createElement('div');
  turn.className = 'turn' + (collapsed ? ' collapsed' : '');
  turn.dataset.kind = kind;
  const turnIndex = Number(index || 0);
  const label = turnIndex ? turnHeaderLabel(turnIndex, kind) : kind;
  const summary = summarizeStructuredBlock(kind, text);
  const header = document.createElement('div');
  header.className = 'turn-header';
  header.innerHTML = `<span class="turn-caret">▼</span><span class="turn-tag">${escapeHtml(label)}</span><span class="turn-summary">${escapeHtml(summary)}</span>`;
  header.addEventListener('click', () => turn.classList.toggle('collapsed'));
  const body = document.createElement('div');
  body.className = 'turn-body md';
  const hasToolDetail = Boolean(parseToolDetails(kind, text));
  renderToolDetailInto(body, { kind, text });
  if (!hasToolDetail) {
    const rendered = document.createElement('div');
    rendered.className = 'turn-rendered-md';
    rendered.innerHTML = renderMarkdown(text);
    body.appendChild(rendered);
  }
  turn.appendChild(header);
  turn.appendChild(body);
  injectCopyButtons(body);
  return turn;
}

function isNearBottom(threshold = 150) {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < threshold;
}

function scrollToBottom(smooth = true) {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
}

// ─── Streaming chunks (from ACP bridge notifications) ────────────────────
// ACP sends method='session/update' with params.update.sessionUpdate=
//   agent_message_chunk | agent_thought_chunk | tool_call | tool_call_update | plan | available_commands_update
function handleNotification(msg) {
  // Handle WS session-state notifications from the bridge backend.
  // These have {type: "session-state", sessionId, state, status, seq, ...}
  // and are used to kick-start polling for sessions that became active
  // (e.g. after page reload, or when a background session starts running).
  if (msg.type === 'session-state') {
    const sess = findSessionByBridgeId(msg.sessionId);
    if (!sess) return;
    const runtime = getSessionRuntime(sess);
    if ((msg.state === 'running' || msg.status === 'running') && !runtime.polling) {
      runtime.busy = true;
      runtime.forcePollOnce = true;
      setBusy(true, 'Thinking…', sess);
      pollSessionMessages(sess);
    } else if (msg.state === 'idle' || msg.state === 'error' || msg.status === 'idle') {
      // Session finished in background — do a final poll to pick up remaining messages
      if (!runtime.polling && runtime.busy) {
        runtime.forcePollOnce = true;
        pollSessionMessages(sess);
      }
    }
    // Update tab dot regardless
    renderSessionList();
    return;
  }
  if (msg.method !== 'session/update') return;
  const update = msg.params?.update;
  if (!update) return;
  const kind = update.sessionUpdate;
  const bridgeSessionId = msg.params?.sessionId || update.sessionId || update.session?.id;
  const sess = findSessionByBridgeId(bridgeSessionId);
  if (!sess) return;


  if (kind === 'agent_message_chunk') {
    const text = extractText(update.content);
    appendAssistantChunk(sess, text);
  } else if (kind === 'task_started') {
    hideError();
    startTaskTimer(sess);
    setBusy(true, 'Thinking…', sess);
  } else if (kind === 'task_completed' || kind === 'cancelled') {
    finalizeAssistantReply(sess);
    setBusy(false, null, sess);
    hideError();
  } else if (kind === 'error') {
    finalizeAssistantReply(sess);
    setBusy(false, null, sess);
    const errText = update.message || update.error || 'Bridge error';
    sess.messages.push({ role: 'error', content: errText });
    if (isActiveSession(sess)) renderMessage({ role: 'error', content: errText });
    showError(errText);
  } else if (kind === 'agent_thought_chunk') {
    const text = extractText(update.content);
    appendStreamChunk(sess, kind, text);
  } else if (kind === 'tool_call') {
    const toolName = update.title || update.name || update.kind || update.toolCallId || 'tool';
    const args = update.arguments || update.args || update.input || update.content || '';
    const argText = typeof args === 'string' ? args : JSON.stringify(args, null, 2);
    const text = `<function_calls>
<invoke name="${escapeHtml(toolName)}">
<parameter name="args">${escapeHtml(argText)}</parameter>
</invoke>
</function_calls>`;
    appendTurn(sess, 'TOOL_CALL', text, true);
  } else if (kind === 'tool_call_update') {
    // Status updates, keep simple
    if (update.status && update.status !== 'in_progress') {
      appendTurn(sess, 'tool', `[${update.status}] ${update.toolCallId || ''}`, true);
    }
  } else if (kind === 'plan') {
    const lines = (update.entries || []).map(e =>
      `- [${e.status || 'pending'}] ${e.content || ''}`
    ).join('\n');
    appendTurn(sess, 'plan', lines, false);
  }
}

function extractText(content) {
  if (!content) return '';
  if (typeof content === 'string') return content;
  if (content.type === 'text') return content.text || '';
  if (Array.isArray(content)) return content.map(extractText).join('');
  return '';
}

function getLiveAssistantWrap(sess) {
  if (!isActiveSession(sess)) return null;
  const last = messagesEl.lastElementChild;
  if (last?.classList?.contains('msg-assistant') && last.dataset.finalized !== '1') return last;
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant';
  const runtime = getSessionRuntime(sess);
  if (runtime.taskStartedAt) {
    wrap.dataset.taskStartedAt = String(runtime.taskStartedAt);
    ensureAssistantTaskElapsed(wrap, runtime.taskStartedAt);
  }
  messagesEl.appendChild(wrap);
  return wrap;
}

function getAssistantDraft(sess) {
  const runtime = getSessionRuntime(sess);
  if (!runtime.assistantDraft || runtime.assistantDraft.finalized) {
    runtime.assistantDraft = {
      text: '',
      segments: [],
      currentSegmentIndex: -1,
      taskStartedAt: runtime.taskStartedAt || 0,
      taskEndedAt: 0,
      finalized: false,
      bridgeMessageId: 0
    };
  }
  if (!runtime.assistantDraft.taskStartedAt && runtime.taskStartedAt) runtime.assistantDraft.taskStartedAt = runtime.taskStartedAt;
  return runtime.assistantDraft;
}

function renderAssistantDraft(sess, draft) {
  if (!isActiveSession(sess) || !draft || draft.finalized) return null;
  prepareMessagesForContent();
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant';
  if (draft.taskStartedAt) {
    wrap.dataset.taskStartedAt = String(draft.taskStartedAt);
    ensureAssistantTaskElapsed(wrap, draft.taskStartedAt, draft.taskEndedAt);
  }
  if (draft.text) {
    wrap.dataset.buf = draft.text;
    const body = document.createElement('div');
    body.className = 'assistant-response md';
    renderStructuredMarkdownInto(body, draft.text, { live: true, messageId: draft.bridgeMessageId, taskStartedAt: draft.taskStartedAt });
    injectCopyButtons(body);
    if (!draft.finalized && !body._executionFeed) body.insertAdjacentHTML('beforeend', '<span class="cursor"></span>');
    wrap.appendChild(body);
    wrap.classList.toggle('execution-message', !!body._executionFeed);
  }
  for (const seg of draft.segments || []) {
    wrap.appendChild(buildTurn(seg.kind, seg.text, seg.collapsed, nextTurnIndexForWrap(wrap)));
  }
  injectCopyButtons(wrap);
  messagesEl.appendChild(wrap);
  return wrap;
}

function renderAssistantDraftInPlace(sess, draft) {
  if (!isActiveSession(sess) || !draft || draft.finalized) return null;
  prepareMessagesForContent();
  const runtime = getSessionRuntime(sess);
  const wrap = getLiveAssistantWrap(sess);
  if (draft.taskStartedAt || runtime.taskStartedAt) {
    const startedAt = draft.taskStartedAt || runtime.taskStartedAt;
    wrap.dataset.taskStartedAt = String(startedAt);
    ensureAssistantTaskElapsed(wrap, startedAt, draft.taskEndedAt);
  }
  wrap.dataset.buf = draft.text || '';
  let body = wrap.querySelector('.assistant-response');
  if (!body) {
    body = document.createElement('div');
    body.className = 'assistant-response md';
    // Keep plain assistant text before folded/tool turns.
    const firstTurn = wrap.querySelector('.turn');
    if (firstTurn) wrap.insertBefore(body, firstTurn);
    else wrap.appendChild(body);
  }
  renderStructuredMarkdownInto(body, draft.text || '', { live: true, messageId: draft.bridgeMessageId, taskStartedAt: draft.taskStartedAt });
  injectCopyButtons(body);
  if (!body._executionFeed) body.insertAdjacentHTML('beforeend', '<span class="cursor"></span>');
  if (isNearBottom()) scrollToBottom(false);
  return wrap;
}

function appendAssistantChunk(sess, text) {
  if (!text) return;
  const runtime = getSessionRuntime(sess);
  const draft = getAssistantDraft(sess);
  draft.text += text;
  draft.currentSegmentIndex = -1;
  runtime.currentTurnEl = null;
  if (!isActiveSession(sess)) return;
  prepareMessagesForContent();
  let wrap = getLiveAssistantWrap(sess);
  let body = wrap.querySelector('.assistant-response');
  if (!body) {
    body = document.createElement('div');
    body.className = 'assistant-response md';
    wrap.appendChild(body);
  }
  wrap.dataset.buf = draft.text;
  ensureAssistantTaskElapsed(wrap, draft.taskStartedAt || runtime.taskStartedAt);
  renderStructuredMarkdownInto(body, draft.text, { live: true, messageId: draft.bridgeMessageId, taskStartedAt: draft.taskStartedAt });
  if (!body._executionFeed) body.insertAdjacentHTML('beforeend', '<span class="cursor"></span>');
  if (isNearBottom()) scrollToBottom(false);
}

function appendStreamChunk(sess, kind, text) {
  if (!text) return;
  // Group consecutive chunks of same kind into one turn (fold_turns style)
  const runtime = getSessionRuntime(sess);
  const draft = getAssistantDraft(sess);
  let seg = draft.segments[draft.currentSegmentIndex];
  if (!seg || seg.kind !== kind) {
    seg = { kind, text: '', collapsed: false };
    draft.segments.push(seg);
    draft.currentSegmentIndex = draft.segments.length - 1;
    runtime.currentTurnEl = null;
  }
  seg.text += text;
  if (!isActiveSession(sess)) return;
  let turn = runtime.currentTurnEl;
  const currentKind = turn?.dataset.kind;
  if (!turn || currentKind !== kind) {
    turn = createStreamingTurn(sess, kind);
    runtime.currentTurnEl = turn;
  }
  turn.dataset.buf = seg.text;
  const body = turn.querySelector('.turn-body');
  const { summary, remaining: cleanText } = stripLeadingMetaTags(seg.text);
  // Update the header turn-summary span (visible when collapsed)
  const summarySpan = turn.querySelector('.turn-summary');
  if (summarySpan && summary) {
    summarySpan.textContent = summary;
  }
  // Only render the clean body text (no summary-hint in body)
  body.innerHTML = renderMarkdown(cleanText) + '<span class="cursor"></span>';
  if (isNearBottom()) scrollToBottom(false);
}

function appendTurn(sess, kind, text, collapsed) {
  const draft = getAssistantDraft(sess);
  draft.segments.push({ kind, text, collapsed: !!collapsed });
  draft.currentSegmentIndex = -1;
  if (!isActiveSession(sess)) return;
  prepareMessagesForContent();
  const wrap = getLiveAssistantWrap(sess);
  wrap.appendChild(buildTurn(kind, text, collapsed, nextTurnIndexForWrap(wrap)));
  if (isNearBottom()) scrollToBottom(false);
}

function createStreamingTurn(sess, kind) {
  prepareMessagesForContent();
  const wrap = getLiveAssistantWrap(sess);
  const turn = document.createElement('div');
  turn.className = 'turn';
  turn.dataset.kind = kind;
  const displayKind = kind === 'agent_thought_chunk' ? 'thinking' : 'response';
  const turnIndex = nextTurnIndexForWrap(wrap);
  turn.innerHTML = `
    <div class="turn-header"><span class="turn-caret">▼</span><span class="turn-tag">${escapeHtml(turnHeaderLabel(turnIndex, displayKind))}</span><span class="turn-summary"></span></div>
    <div class="turn-body md"></div>`;
  turn.querySelector('.turn-header').addEventListener('click', () => turn.classList.toggle('collapsed'));
  // thinking turns collapsed by default once complete
  if (kind === 'agent_thought_chunk') turn.dataset.autoCollapse = '1';
  wrap.appendChild(turn);
  return turn;
}

function getCurrentAssistantWrap(sess) {
  if (!isActiveSession(sess)) return null;
  const last = messagesEl.lastElementChild;
  if (last?.classList?.contains('msg-assistant') && last.dataset.finalized !== '1') return last;
  return null;
}

function finalizeStreamingTurn(sess) {
  const runtime = getSessionRuntime(sess);
  const wrap = getCurrentAssistantWrap(sess);
  const liveAssistant = wrap?.querySelector('.assistant-response');
  if (liveAssistant) {
    renderStructuredMarkdownInto(liveAssistant, wrap.dataset.buf || '');
    injectCopyButtons(liveAssistant);
  }
  if (runtime.assistantDraft?.segments?.length) {
    for (const seg of runtime.assistantDraft.segments) {
      if (seg.kind === 'agent_thought_chunk') seg.collapsed = true;
    }
  }
  if (!runtime.currentTurnEl) return;
  const t = runtime.currentTurnEl;
  const body = t.querySelector('.turn-body');
  // Remove cursor, strip summary/think tags; set turn-summary in header
  if (body) {
    const { summary: extractedSummary, remaining: cleanBuf } = stripLeadingMetaTags(t.dataset.buf || '');
    body.innerHTML = renderMarkdown(cleanBuf);
    // Set the turn-summary span in the header for collapsed display
    const summaryEl = t.querySelector('.turn-summary');
    if (summaryEl) {
      const kind = t.dataset.kind || 'response';
      summaryEl.textContent = extractedSummary || summarizeStructuredBlock(kind, cleanBuf);
    }
  }
  if (t.dataset.autoCollapse === '1') t.classList.add('collapsed');
  const idx = runtime.assistantDraft?.currentSegmentIndex;
  if (Number.isInteger(idx) && runtime.assistantDraft?.segments?.[idx]) {
    runtime.assistantDraft.segments[idx].collapsed = t.classList.contains('collapsed');
  }
  runtime.currentTurnEl = null;
}

function finalizeAssistantReply(sess) {
  const endedAt = getNowMs();
  finalizeStreamingTurn(sess);
  // Remove any residual blinking cursors (e.g. after RPC timeout)
  messagesEl.querySelectorAll('.cursor').forEach(el => el.remove());
  const runtime = getSessionRuntime(sess);
  const draft = runtime.assistantDraft;
  const wrap = getCurrentAssistantWrap(sess);
  if (draft && sess && !draft.finalized) {
    draft.finalized = true;
    draft.taskEndedAt = endedAt;
    // Strip trailing [Info] Final response to user. marker (wrapped in 5 backticks)
    if (draft.text) {
      draft.text = draft.text.replace(/\n*`{5}\n*\[Info\] Final response to user\.\n*`{5}\s*$/, '');
    }
    if (draft.segments?.length) {
      const last = draft.segments[draft.segments.length - 1];
      if (last && last.text) {
        last.text = last.text.replace(/\n*`{5}\n*\[Info\] Final response to user\.\n*`{5}\s*$/, '');
      }
    }
    const msg = { role: 'assistant', finalized: true, taskEndedAt: endedAt };
    if (draft.bridgeMessageId) msg.id = Number(draft.bridgeMessageId);
    if (draft.taskStartedAt) msg.taskStartedAt = Number(draft.taskStartedAt);
    if (draft.text) msg.content = draft.text;
    if (draft.segments?.length) msg.segments = draft.segments.map((seg) => ({
      kind: seg.kind,
      text: seg.text || '',
      collapsed: !!seg.collapsed
    }));
    if (msg.content || msg.segments?.length) sess.messages.push(msg);
    runtime.assistantDraft = null;
  }
  if (wrap) {
    wrap.dataset.finalized = '1';
    wrap.dataset.taskEndedAt = String(endedAt);
    ensureAssistantTaskElapsed(wrap, wrap.dataset.taskStartedAt || runtime.taskStartedAt || draft?.taskStartedAt, endedAt);
    renderMessages();
  } else if (!isActiveSession(sess)) {
    // Session finished in background — its DOM cache is stale, discard it
    // so that switching to it will do a full re-render from sess.messages
    _domCache.delete(sess.id);
  }
  stopTaskTimer(sess);
}

// ─── Sending prompts ─────────────────────────────────────────────────────
function normalizeBridgeMessage(msg) {
  return {
    id: Number(msg.id || 0),
    role: msg.role || 'system',
    content: msg.content || '',
    image_ids: msg.image_ids || []
  };
}

function upsertPolledMessage(sess, raw, { partial = false } = {}) {
  if (!sess || !raw) return;
  const msg = normalizeBridgeMessage(raw);
  if (!msg.id) return;
  const runtime = getSessionRuntime(sess);
  if (!runtime.seenBridgeMessageIds) runtime.seenBridgeMessageIds = new Set();

  if (partial && msg.role === 'assistant') {
    const draft = getAssistantDraft(sess);
    const changed = draft.bridgeMessageId !== msg.id || draft.text !== (msg.content || '');
    draft.bridgeMessageId = msg.id;
    draft.text = msg.content || '';
    draft.currentSegmentIndex = -1;
    draft.finalized = false;
    // Polling partial updates used to call renderMessages(), which rebuilt the
    // whole message list every 500ms. That destroys user fold/collapse DOM
    // state and can make the live answer appear to jump/duplicate. Update only
    // the live assistant draft in-place; final messages are still reconciled by
    // id in the non-partial branch below.
    if (changed && isActiveSession(sess)) renderAssistantDraftInPlace(sess, draft);
    return;
  }

  if (runtime.seenBridgeMessageIds.has(msg.id)) return;
  runtime.seenBridgeMessageIds.add(msg.id);
  runtime.lastPolledMessageId = Math.max(Number(runtime.lastPolledMessageId || 0), msg.id);

  const draft = runtime.assistantDraft;
  if (msg.role === 'assistant' && draft && !draft.finalized && Number(draft.bridgeMessageId || 0) === msg.id) {
    draft.text = msg.content || draft.text || '';
    finalizeAssistantReply(sess);
    return;
  }
  sess.messages.push(msg);
  if (isActiveSession(sess)) renderMessage(msg);
}

async function pollSessionMessages(sess) {
  if (!sess) return;
  const runtime = getSessionRuntime(sess);
  if (runtime.polling) return;
  runtime.polling = true;
  try {
    while (runtime.busy || runtime.forcePollOnce) {
      runtime.forcePollOnce = false;
      const res = await window.ga.pollSession(sess.bridgeSessionId || sess.id, runtime.lastPolledMessageId || 0);
      if (res?.error) throw new Error(res.error.message || res.error);
      const result = res.result || res;
      for (const msg of (result.messages || [])) upsertPolledMessage(sess, msg, { partial: false });
      if (result.partial) upsertPolledMessage(sess, result.partial, { partial: true });
      const busy = result.status === 'running' || !!result.partial;
      setBusy(busy, busy ? 'Thinking…' : null, sess);
      if (!busy) {
        finalizeAssistantReply(sess);
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } catch (e) {
    addDiagnostic('error', 'Polling failed', e);
    showError('Polling failed: ' + (e.message || e));
    setBusy(false, null, sess);
  } finally {
    runtime.polling = false;
  }
}

async function sendPrompt(text, attachments = []) {
  if (!state.bridgeReady) {
    showError('Bridge is not ready.');
    return;
  }
  if (!state.activeId) {
    await newSession();
    if (!state.activeId) return;
  }
  const sess = state.sessions.get(state.activeId);
  const runtime = getSessionRuntime(sess);
  if (runtime.busy) return;

  const imageIds = attachments.filter(item => item.kind === 'image').map(item => {
    try { sessionStorage.setItem('img:' + item.id, item.previewUrl); } catch(e) { /* quota */ }
    return item.id;
  });

  const localUserMsg = {
    role: 'user',
    content: text,
    image_ids: imageIds,
    attachments: attachments.map(item => ({
      id: item.id,
      name: item.name,
      kind: item.kind,
      previewUrl: item.previewUrl,
    })),
  };
  sess.messages.push(localUserMsg);
  renderMessage(localUserMsg);
  startTaskTimer(sess);
  if (sess.untitled || isUntitledSessionTitle(sess.title)) {
    sess.title = text.trim().slice(0, 40) + (text.trim().length > 40 ? '…' : '');
    sess.untitled = false;
    sessionTitleEl.textContent = sess.title;
    renderSessionList();
  }

  setBusy(true, 'Thinking…', sess);
  try {
    const res = await window.ga.rpc('session/prompt', {
      sessionId: await ensureBridgeSession(sess),
      prompt: text,
      attachments: attachments.map(item => ({
        id: item.id,
        name: item.name,
        path: item.path,
        kind: item.kind,
      })),
      llmNo: sess.config.llmNo
    });
    if (res?.error) throw new Error(res.error.message || res.error);
    const acceptedUserId = Number(res.userMessageId || res.result?.userMessageId || 0);
    if (acceptedUserId) {
      if (!runtime.seenBridgeMessageIds) runtime.seenBridgeMessageIds = new Set();
      runtime.seenBridgeMessageIds.add(acceptedUserId);
      runtime.lastPolledMessageId = Math.max(Number(runtime.lastPolledMessageId || 0), acceptedUserId);
    }
    runtime.forcePollOnce = true;
    pollSessionMessages(sess);
  } catch (e) {
    sess.messages.push({ role: 'error', content: e.message || String(e) });
    if (isActiveSession(sess)) renderMessage({ role: 'error', content: e.message || String(e) });
    setBusy(false, null, sess);
  }
}

async function cancelPrompt() {
  const sess = state.sessions.get(state.activeId);
  const runtime = sess ? getSessionRuntime(sess) : null;
  if (!runtime?.busy) return false;
  try {
    const res = await window.ga.rpc('session/cancel', { sessionId: sess?.bridgeSessionId || state.activeId });
    if (res.error) throw new Error(res.error.message || res.error);
    return true;
  } catch (e) {
    showSystem('Stop failed: ' + (e.message || e));
    return false;
  }
}

// ─── Slash commands ──────────────────────────────────────────────────────
async function handleSlash(cmd) {
  const [name, ...rest] = cmd.trim().slice(1).split(/\s+/);
  const arg = rest.join(' ');
  const sess = state.sessions.get(state.activeId);

  switch (name) {
    case 'help':
      showSystem([
        'Available commands:',
        '  /new        New session',
        '  /clear      Clear current session display',
        '  /stop       Cancel the current request',
        '  /theme      Switch theme (light|dark|auto)',
      ].join('\n'));
      break;
    case 'new':
      await newSession();
      break;
    case 'clear':
      if (sess) { sess.messages = []; renderMessages(); }
      break;
    case 'stop':
      if (await cancelPrompt()) showSystem('Stop requested.');
      break;
    case 'restart':
      await restartBridge();
      break;
    case 'settings':
      openSettings();
      break;
    case 'theme':
      if (['light', 'dark', 'auto'].includes(arg)) {
        const cfg = getActiveConfig();
        cfg.theme = arg;
        applyTheme();
        await window.ga.saveConfig(cfg);
        showSystem(`Theme → ${arg}`);
      } else {
        showSystem('Usage: /theme light|dark|auto');
      }
      break;
    case 'cwd':
      if (!arg) {
        const status = await window.ga.checkStatus();
        showSystem(`cwd: ${sess?.cwd || status.gaRoot}`);
      } else {
        showSystem(`Creating new session in ${arg}…`);
        // Need a new session for different cwd
        const res = await window.ga.rpc('session/new', { cwd: arg, mcp_servers: [] });
        if (res.error) showSystem('Failed: ' + (res.error.message || res.error));
        else {
          const bridgeSessionId = res.sessionId;
          const localSessionId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
          const ns = createLocalSession(localSessionId, arg.split('/').pop() || arg, bridgeSessionId);
          ns.cwd = arg;
          setActiveSession(localSessionId);
        }
      }
      break;
    default:
      showSystem(`Unknown command: /${name}. Try /help.`);
  }
}

function showSystem(text) {
  const msg = { role: 'system', content: text };
  const sess = state.sessions.get(state.activeId);
  if (sess) sess.messages.push(msg);
  renderMessage(msg);
  return msg;
}

function updateBridgeNotice(text) {
  const notice = state.bridgeNoticeMessage;
  state.bridgeNoticeMessage = null;
  if (!notice) return;
  notice.content = text;
  const sess = state.sessions.get(state.activeId);
  if (sess && sess.messages.includes(notice)) renderMessages();
}

// ─── Status / UI helpers ─────────────────────────────────────────────────
function setStatus(kind, text) {
  statusBadge.className = 'badge ' + kind;
  statusText.textContent = text;
  // Update per-tab dot for active session
  updateTabDot(state.activeId, kind);
}

function updateTabDot(sessionId, kind) {
  if (!sessionId) return;
  const tab = sessionListEl.querySelector(`[data-session-id="${sessionId}"]`);
  if (!tab) return;
  const dot = tab.querySelector('.tab-dot');
  if (!dot) return;
  dot.className = 'tab-dot';
  if (kind === 'busy') dot.classList.add('busy');
  else if (kind === 'warn') dot.classList.add('warn');
  else if (kind === 'err') dot.classList.add('err');
  // 'ok' = default green (no extra class needed)
}

const SEND_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`;
const STOP_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>`;

function setBusy(busy, label, sess = state.sessions.get(state.activeId)) {
  const runtime = sess ? getSessionRuntime(sess) : null;
  if (runtime) runtime.busy = busy;
  // Always update per-tab dot for this session
  if (sess) {
    const dotKind = busy ? 'busy' : (state.bridgeReady ? 'ok' : 'warn');
    updateTabDot(sess.id, dotKind);
  }
  if (!isActiveSession(sess)) return;
  if (busy) setStatus('busy', label || 'Working…');
  else setStatus(state.bridgeReady ? 'ok' : 'warn', state.bridgeReady ? 'Ready' : 'Starting…');
  renderSendButtonState();
}

function renderSendButtonState() {
  const hasText = inputEl.value.trim().length > 0;
  const busy = !!getActiveSessionRuntime()?.busy;
  sendBtn.classList.toggle('stop', busy);
  sendBtn.title = busy ? 'Stop (Esc)' : 'Send (Enter)';
  sendBtn.setAttribute('aria-label', busy ? '停止任务' : '发送任务');
  sendBtn.innerHTML = busy ? STOP_ICON : SEND_ICON;
  sendBtn.disabled = !hasText && !busy && pendingImages.length === 0;
}

function updateSendButton() {
  renderSendButtonState();
}

function showError(text, actionLabel, actionFn, options = {}) {
  if (!options.skipDiagnostic) addDiagnostic('error', text);
  $('error-text').textContent = text;
  const actionBtn = $('error-action');
  if (actionLabel && actionFn) {
    actionBtn.textContent = actionLabel;
    actionBtn.classList.remove('hidden');
    actionBtn.onclick = async () => {
      try {
        await actionFn();
      } catch (err) {
        showError('Action failed: ' + (err.message || err));
      }
    };
  } else {
    actionBtn.classList.add('hidden');
  }
  errorBanner.classList.remove('hidden');
  clearTimeout(showError._t);
  if (!actionLabel) {
    showError._t = setTimeout(() => errorBanner.classList.add('hidden'), 6000);
  }
}
function hideError() { errorBanner.classList.add('hidden'); }

// ─── Theme ───────────────────────────────────────────────────────────────
function applyTheme() {
  const cfg = getActiveConfig();
  document.documentElement.setAttribute('data-theme', cfg.theme || 'auto');
}

// ─── Settings modal ──────────────────────────────────────────────────────
function renderModelOptions() {
  const select = $('cfg-llm');
  const selected = String(getActiveConfig().llmNo || 0);
  const profiles = Array.isArray(state.modelProfiles) ? state.modelProfiles : [];
  const options = profiles.length ? profiles : [{ llmNo: 0, name: 'Default / Auto' }];
  select.textContent = '';
  for (const profile of options) {
    const opt = document.createElement('option');
    opt.value = String(profile.llmNo);
    // Display as "name/model" when both fields available
    const displayName = profile.name && profile.model
      ? `${profile.name}/${profile.model}`
      : profile.name || profile.model || `Model ${profile.llmNo}`;
    opt.textContent = displayName;
    select.appendChild(opt);
  }
  if (![...select.options].some((opt) => opt.value === selected)) {
    const opt = document.createElement('option');
    opt.value = selected;
    opt.textContent = selected === '0' ? 'Default / Auto' : `Model ${selected}`;
    select.appendChild(opt);
  }
  select.value = selected;
}

async function loadModelProfiles() {
  try {
    const result = await window.ga.getModelProfiles();
    state.modelProfiles = Array.isArray(result && result.profiles) ? result.profiles : [];
    renderModelOptions();
  } catch (err) {
    addDiagnostic('warn', 'Failed to load model names', err);
    renderModelOptions();
  }
}

function openSettings() {
  renderModelOptions();
  const cfg = getActiveConfig();
  $('cfg-llm').value = String(cfg.llmNo || 0);
  settingsModal.classList.remove('hidden');
  loadModelProfiles();
}
function closeSettings() { settingsModal.classList.add('hidden'); }

async function openConfigFile(openFn, label) {
  try {
    const result = await openFn();
    if (result && result.ok === false) {
      showError(`Failed to open ${label}: ${result.error || result.path || 'unknown error'}`);
    }
  } catch (err) {
    showError(`Failed to open ${label}: ${err.message || err}`);
  }
}

async function saveSettings() {
  const saveBtn = $('save-settings');
  saveBtn.disabled = true;
  try {
    const sess = state.sessions.get(state.activeId);
    if (!sess) throw new Error('No active session');
    const cfg = sess.config;
    cfg.llmNo = Math.max(0, parseInt($('cfg-llm').value, 10) || 0);
    await window.ga.saveConfig(cfg);
    closeSettings();
  } catch (err) {
    showError('Failed to save settings: ' + (err.message || err));
  } finally {
    saveBtn.disabled = false;
  }
}

async function ensureBridgeSession(sess) {
  if (!sess) throw new Error('No active session.');
  if (sess.bridgeSessionId) return sess.bridgeSessionId;
  const cwd = sess.cwd || await getCwd();
  const res = await window.ga.rpc('session/new', { cwd, mcp_servers: [] });
  if (res.error) throw new Error(typeof res.error === 'string' ? res.error : (res.error.message || JSON.stringify(res.error)));
  sess.bridgeSessionId = res.sessionId;
  sess.cwd = cwd;
  return sess.bridgeSessionId;
}

async function restartBridge(options = {}) {
  const { remapSessions = false } = options;
  setStatus('warn', 'Restarting…');
  state.bridgeReady = false;
  state.restartingBridge = true;
  if (remapSessions) {
    for (const sess of state.sessions.values()) sess.bridgeSessionId = null;
  }
  state.bridgeNoticeMessage = showSystem('Bridge restarting…');
  await window.ga.startBridge(getActiveConfig().llmNo || 0);
  window.setTimeout(() => {
    if (state.restartingBridge && !state.bridgeReady && !getActiveSessionRuntime()?.busy) {
      markBridgeReady('Bridge ready.');
      addDiagnostic('warn', 'Bridge ready event timeout; restored Ready status locally');
    }
  }, 2500);
}

// ─── Bridge events ───────────────────────────────────────────────────────
let _bootstrappingSession = false;
async function markBridgeReady(noticeText = 'Bridge ready.') {
  if (state.bridgeReady) return; // already marked ready, prevent double-fire
  state.bridgeReady = true;
  state.restartingBridge = false;
  if (getActiveSessionRuntime()?.busy) setStatus('busy', 'Agent is responding…');
  else setStatus('ok', 'Ready');
  updateBridgeNotice(noticeText);
  hideError();
  // Restore sessions from bridge (survives page refresh) or create first session
  if (state.sessions.size === 0 && !_bootstrappingSession) {
    _bootstrappingSession = true;
    try {
      // Try to restore existing sessions from bridge
      const bridgeUrl = window.ga.bridgeUrl || 'http://127.0.0.1:14168';
      const listRes = await fetch(`${bridgeUrl}/sessions`).then(r => r.json()).catch(() => null);
      const existingSessions = listRes?.sessions || [];
      if (existingSessions.length > 0) {
        // Restore each session from bridge
        for (const bSess of existingSessions) {
          const localId = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
          const sess = createLocalSession(localId, bSess.title || 'Restored', bSess.id || bSess.sessionId);
          // Fetch full messages for this session
          const sid = bSess.id || bSess.sessionId;
          const msgRes = await fetch(`${bridgeUrl}/session/${sid}/messages?after=0&limit=9999`).then(r => r.json()).catch(() => null);
          if (msgRes?.messages) {
            sess.messages = msgRes.messages;
            // Initialize polling state so we don't re-fetch these messages
            const runtime = getSessionRuntime(sess);
            runtime.seenBridgeMessageIds = new Set();
            let maxId = 0;
            for (const m of msgRes.messages) {
              if (m.id) { runtime.seenBridgeMessageIds.add(Number(m.id)); maxId = Math.max(maxId, Number(m.id)); }
            }
            runtime.lastPolledMessageId = maxId;
          }
        }
        // Activate the first session
        const firstLocalId = [...state.sessions.keys()][0];
        if (firstLocalId) setActiveSession(firstLocalId);
      } else {
        await newSession();
      }
    } finally { _bootstrappingSession = false; }
  }
  updateSendButton();
  // Refresh model profiles from bridge (authoritative source)
  loadModelProfiles();
  pollPlatformStatus(true);
}

window.ga.onBridgeReady(() => {
  markBridgeReady();
});

window.ga.onBridgeMessage(() => {
  // RPC responses are resolved in main; renderer readiness comes from bridge-ready.
});

window.ga.onBridgeNotification((msg) => {
  handleNotification(msg);
});

window.ga.onBridgeError((err) => {
  console.error('Bridge error:', err);
  addDiagnostic('error', 'Bridge error', err);
  setStatus('err', 'Error');
  state.bridgeReady = false;
  state.restartingBridge = false;

  if (err.type === 'no-mykey') {
    showError(err.message, 'Setup', async () => {
      await window.ga.openMykeyTemplate();
    }, { skipDiagnostic: true });
  } else if (err.type === 'no-python') {
    showError(err.message, 'Settings', openSettings, { skipDiagnostic: true });
  } else {
    showError(err.message || 'Bridge error', null, null, { skipDiagnostic: true });
  }
});

window.ga.onBridgeClosed((info) => {
  addDiagnostic('warn', 'Bridge closed', info);
  if (state.restartingBridge) {
    setStatus('warn', 'Restarting…');
    return;
  }
  state.bridgeReady = false;
  setStatus('err', `Bridge stopped (${info.code})`);
});

window.ga.onBridgeLog((text) => {
  console.log('[bridge]', text);
  addDiagnostic('info', 'Bridge log', text);
});

// ─── Input handling ──────────────────────────────────────────────────────
inputEl.addEventListener('input', () => {
  // auto-resize
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
  updateSendButton();
});

// IME composition fix - triple guard for CJK input methods (macOS especially)
let _imeComposing = false;
inputEl.addEventListener('compositionstart', () => { _imeComposing = true; });
inputEl.addEventListener('compositionend', () => { _imeComposing = false; });

inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    if (e.isComposing || _imeComposing || e.keyCode === 229) return; // IME active, ignore
    e.preventDefault();
    submitInput();
  } else if (e.key === 'Escape' && getActiveSessionRuntime()?.busy) {
    e.preventDefault();
    cancelPrompt();
  }
});

// ─── Local image/video upload ─────────────────────────────────────────────
const imagePreviews = document.getElementById('image-previews');
const mediaInput = $('media-input');

async function uploadMediaFile(file) {
  if (!file || (!file.type.startsWith('image/') && !file.type.startsWith('video/'))) {
    throw new Error(`${file?.name || '该文件'}不是支持的图片或视频`);
  }
  const form = new FormData();
  form.append('file', file, file.name);
  const response = await fetch('/upload', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.raw || `上传失败 (${response.status})`);
  return data;
}

async function addLocalFiles(files) {
  const accepted = Array.from(files || []);
  for (const file of accepted) {
    const placeholder = {
      id: `uploading-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`,
      name: file.name,
      kind: file.type.startsWith('video/') ? 'video' : 'image',
      uploading: true,
      progress: '上传中',
    };
    pendingImages.push(placeholder);
    renderImagePreviews();
    try {
      const uploaded = await uploadMediaFile(file);
      Object.assign(placeholder, uploaded, { uploading: false });
    } catch (err) {
      const index = pendingImages.indexOf(placeholder);
      if (index >= 0) pendingImages.splice(index, 1);
      showError(err.message || String(err));
    }
    renderImagePreviews();
  }
  updateSendButton();
}

inputEl.addEventListener('paste', (e) => {
  const files = Array.from(e.clipboardData?.files || []).filter(file => file.type.startsWith('image/') || file.type.startsWith('video/'));
  if (!files.length) return;
  e.preventDefault();
  addLocalFiles(files);
});

function renderImagePreviews() {
  imagePreviews.innerHTML = '';
  for (const img of pendingImages) {
    const wrapper = document.createElement('div');
    wrapper.className = 'image-preview-item';
    wrapper.dataset.imgId = img.id;
    wrapper.classList.toggle('uploading', !!img.uploading);

    let mediaEl;
    if (img.kind === 'video' && img.previewUrl) {
      mediaEl = document.createElement('video');
      mediaEl.src = img.previewUrl;
      mediaEl.muted = true;
      mediaEl.preload = 'metadata';
    } else if (img.previewUrl) {
      mediaEl = document.createElement('img');
      mediaEl.src = img.previewUrl;
      mediaEl.alt = img.name || '上传图片';
    } else {
      mediaEl = document.createElement('div');
      mediaEl.className = 'upload-placeholder';
      mediaEl.textContent = img.kind === 'video' ? 'VID' : 'IMG';
    }
    const label = document.createElement('span');
    label.className = 'media-preview-label';
    label.textContent = img.uploading ? '上传中…' : img.name || (img.kind === 'video' ? '视频' : '图片');

    const closeBtn = document.createElement('button');
    closeBtn.className = 'remove-img';
    closeBtn.textContent = '×';
    closeBtn.setAttribute('aria-label', 'Remove image');
    closeBtn.addEventListener('click', () => {
      const idx = pendingImages.findIndex(i => i.id === img.id);
      if (idx !== -1) pendingImages.splice(idx, 1);
      renderImagePreviews();
      updateSendButton();
    });

    wrapper.appendChild(mediaEl);
    wrapper.appendChild(label);
    wrapper.appendChild(closeBtn);
    imagePreviews.appendChild(wrapper);
  }
  imagePreviews.style.display = pendingImages.length ? 'flex' : 'none';
}

function clearPendingImages() {
  pendingImages.length = 0;
  renderImagePreviews();
}

function submitInput() {
  const text = inputEl.value.trim();
  if (!text && pendingImages.length === 0) return;
  if (!state.bridgeReady) {
    showError('工作台尚未连接，输入和素材已保留。请确认服务运行后再发送。');
    return;
  }
  if (pendingImages.some(item => item.uploading)) {
    showError('素材仍在上传，请稍等完成后再发送。');
    return;
  }
  if (getActiveSessionRuntime()?.busy) {
    showSystem('Agent is still responding. Press Esc or Stop before sending another message.');
    return;
  }
  const attachments = [...pendingImages];
  inputEl.value = '';
  inputEl.style.height = 'auto';
  clearPendingImages();
  updateSendButton();

  if (text.startsWith('/')) {
    handleSlash(text).catch((err) => {
      showSystem('Command failed: ' + (err.message || err));
    });
  } else {
    sendPrompt(text, attachments);
  }
}

sendBtn.addEventListener('click', () => {
  if (getActiveSessionRuntime()?.busy) {
    cancelPrompt().then((ok) => {
      if (ok) showSystem('Stop requested.');
    });
  } else submitInput();
});

function activateStarterCards(root = document) {
  root.querySelectorAll('.starter-card').forEach((card) => {
    if (card.dataset.bound === '1') return;
    card.dataset.bound = '1';
    card.addEventListener('click', () => {
      const prompt = card.getAttribute('data-prompt') || '';
      if (!prompt || getActiveSessionRuntime()?.busy) return;
      inputEl.value = prompt;
      inputEl.dispatchEvent(new Event('input', { bubbles: true }));
      submitInput();
    });
  });
}
activateStarterCards();

// ─── Buttons ─────────────────────────────────────────────────────────────
$('new-session-btn').addEventListener('click', newSession);
$('attach-btn')?.addEventListener('click', () => mediaInput?.click());
mediaInput?.addEventListener('change', () => {
  addLocalFiles(mediaInput.files);
  mediaInput.value = '';
});
$('settings-btn').addEventListener('click', openSettings);
$('close-settings').addEventListener('click', closeSettings);
$('cancel-settings').addEventListener('click', closeSettings);
$('save-settings').addEventListener('click', saveSettings);
$('open-mykey').addEventListener('click', () => openConfigFile(window.ga.openMykey, 'mykey.py'));
$('error-dismiss').addEventListener('click', hideError);
$('setup-entry')?.addEventListener('click', openSetupGate);
$('setup-close')?.addEventListener('click', closeSetupGate);
$('setup-continue')?.addEventListener('click', closeSetupGate);
$('setup-refresh')?.addEventListener('click', () => pollPlatformStatus(true));
document.querySelectorAll('[data-platform-open]').forEach((button) => {
  button.addEventListener('click', () => openPlatform(button.dataset.platformOpen));
});
$('focus-open')?.addEventListener('click', () => {
  const url = $('focus-open').dataset.url;
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
});
document.querySelectorAll('[data-workspace-tab]').forEach((button) => {
  button.addEventListener('click', () => {
    const tab = button.dataset.workspaceTab;
    document.querySelectorAll('[data-workspace-tab]').forEach((item) => {
      item.classList.toggle('active', item === button);
    });
    document.querySelectorAll('[data-workspace-panel]').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.workspacePanel === tab);
    });
  });
});
document.querySelectorAll('[data-wall-layout]').forEach((button) => {
  button.addEventListener('click', () => {
    browserWallLayout = button.dataset.wallLayout || 'auto';
    document.querySelectorAll('[data-wall-layout]').forEach((item) => item.classList.toggle('active', item === button));
    renderBrowserWall([]);
    pollActivity();
  });
});

let dragDepth = 0;
document.addEventListener('dragenter', (event) => {
  if (!event.dataTransfer?.types?.includes('Files')) return;
  event.preventDefault();
  dragDepth += 1;
  $('drop-overlay')?.classList.remove('hidden');
});
document.addEventListener('dragover', (event) => {
  if (event.dataTransfer?.types?.includes('Files')) event.preventDefault();
});
document.addEventListener('dragleave', (event) => {
  if (!event.dataTransfer?.types?.includes('Files')) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $('drop-overlay')?.classList.add('hidden');
});
document.addEventListener('drop', (event) => {
  if (!event.dataTransfer?.files?.length) return;
  event.preventDefault();
  dragDepth = 0;
  $('drop-overlay')?.classList.add('hidden');
  addLocalFiles(event.dataTransfer.files);
});

settingsModal.querySelector('.modal-backdrop').addEventListener('click', closeSettings);

// ─── Message Search (Cmd/Ctrl+F) ─────────────────────────────────────────
(function initSearch() {
  const searchBar = document.getElementById('search-bar');
  const searchInput = document.getElementById('search-input');
  const searchClose = document.getElementById('search-close');
  const searchPrev = document.getElementById('search-prev');
  const searchNext = document.getElementById('search-next');
  const searchCount = document.getElementById('search-count');

  let highlights = [];
  let currentIdx = -1;

  function openSearch() {
    searchBar.classList.remove('hidden');
    searchBar.classList.add('visible');
    searchInput.focus();
    searchInput.select();
  }

  function closeSearch() {
    searchBar.classList.remove('visible');
    searchBar.classList.add('hidden');
    clearHighlights();
    searchInput.value = '';
    searchCount.textContent = '';
  }

  function clearHighlights() {
    highlights.forEach(el => {
      const parent = el.parentNode;
      if (parent) {
        parent.replaceChild(document.createTextNode(el.textContent), el);
        parent.normalize();
      }
    });
    highlights = [];
    currentIdx = -1;
  }

  function doSearch(query) {
    clearHighlights();
    if (!query) { searchCount.textContent = ''; return; }

    const chatArea = document.getElementById('messages');
    window.AutoMediaExecution?.prepareSearch(chatArea, query);
    const walker = document.createTreeWalker(chatArea, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    const lowerQ = query.toLowerCase();
    textNodes.forEach(node => {
      const text = node.textContent;
      const lower = text.toLowerCase();
      let idx = lower.indexOf(lowerQ);
      if (idx === -1) return;

      const frag = document.createDocumentFragment();
      let lastIdx = 0;
      while (idx !== -1) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx, idx)));
        const mark = document.createElement('mark');
        mark.className = 'search-highlight';
        mark.textContent = text.slice(idx, idx + query.length);
        frag.appendChild(mark);
        highlights.push(mark);
        lastIdx = idx + query.length;
        idx = lower.indexOf(lowerQ, lastIdx);
      }
      frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      node.parentNode.replaceChild(frag, node);
    });

    searchCount.textContent = highlights.length ? `1/${highlights.length}` : '0';
    if (highlights.length) { currentIdx = 0; scrollToHighlight(); }
  }

  function scrollToHighlight() {
    highlights.forEach((el, i) => el.classList.toggle('active', i === currentIdx));
    if (highlights[currentIdx]) {
      // Expand any collapsed ancestor turns so the match is visible
      let ancestor = highlights[currentIdx].parentElement;
      while (ancestor && ancestor !== document.body) {
        if (ancestor.tagName === 'DETAILS') ancestor.open = true;
        if (ancestor.classList.contains('turn') && ancestor.classList.contains('collapsed')) {
          ancestor.classList.remove('collapsed');
        }
        ancestor = ancestor.parentElement;
      }
      highlights[currentIdx].scrollIntoView({ block: 'center', behavior: 'smooth' });
      searchCount.textContent = `${currentIdx + 1}/${highlights.length}`;
    }
  }

  function nextMatch() { if (!highlights.length) return; currentIdx = (currentIdx + 1) % highlights.length; scrollToHighlight(); }
  function prevMatch() { if (!highlights.length) return; currentIdx = (currentIdx - 1 + highlights.length) % highlights.length; scrollToHighlight(); }

  // Event listeners
  $('chat-search')?.addEventListener('click', openSearch);
  searchClose.addEventListener('click', closeSearch);
  searchPrev.addEventListener('click', prevMatch);
  searchNext.addEventListener('click', nextMatch);

  let searchTimeout;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => doSearch(searchInput.value), 200);
  });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.shiftKey ? prevMatch() : nextMatch(); e.preventDefault(); }
    if (e.key === 'Escape') { closeSearch(); e.preventDefault(); }
  });

  // Global shortcut: Cmd+F (Mac) / Ctrl+F (Win/Linux)
  // Note: On macOS Electron intercepts Cmd+F via menu accelerator,
  // so we also listen for IPC 'open-search' from main process.
  document.addEventListener('keydown', (e) => {
    const isMac = /Macintosh|Mac OS X/.test(navigator.userAgent);
    const mod = isMac ? e.metaKey : e.ctrlKey;
    if (mod && e.key === 'f') {
      e.preventDefault();
      openSearch();
    }
    if (e.key === 'Escape' && searchBar.classList.contains('visible')) {
      e.preventDefault();
      closeSearch();
    }
  });

  // Listen for IPC from main process (menu accelerator on macOS)
  if (window.ga && window.ga.onOpenSearch) {
    window.ga.onOpenSearch(() => openSearch());
  }
})();

// ─── Init ────────────────────────────────────────────────────────────────
(async function init() {
  // Add platform class to body for platform-specific CSS
  const platform = (window.ga && window.ga.platform) || process.platform || 'unknown';
  document.body.classList.add('platform-' + platform);

  try {
    const saved = await window.ga.getConfig();
    Object.assign(state.defaultConfig, saved);
  } catch (err) {
    addDiagnostic('error', 'Failed to load settings', err);
    showError('Failed to load settings; using defaults: ' + (err.message || err));
  }
  applyTheme();
  await loadModelProfiles();
  updateSendButton();
  renderActivity(null);
  window.setInterval(pollActivity, 1200);
  window.setInterval(() => pollPlatformStatus(false), 8000);
  inputEl.focus();
})();
