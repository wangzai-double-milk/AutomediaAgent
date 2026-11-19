(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const states = {completed: '已完成', blocked: '需要处理', failed: '未完成', stale: '资料已变化',
    interrupted: '已中断', cancelled: '已停止', queued: '等待开始', thinking: '正在思考',
    behavior: '正在观察', talk: '正在整理', running: '进行中', archived: '历史记录'};
  let character, items = [], filter = 'all', limit = 6, loading = false, journalSig = '', albumSig = '';
  let generating = false;
  const ideas = [
    ['温柔，也可以有边界', '把一句不好意思说出口的话，换一种得体的表达。'],
    ['翻书时，慢一点', '从一段有来源的文字出发，聊聊它为什么打动你；没有原文就先找材料。'],
    ['一件小事，两种看法', '选一个日常沟通情境，分别理解双方，再给出你自己的判断。'],
    ['给日常留一点空白', '围绕阅读、光线和生活审美，构思一篇AI虚拟角色的图文手记。']
  ];
  let idea = 0;
  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const value = await r.json();
    if (!r.ok) throw Error(value.error || '暂时无法读取，请稍后重试');
    return value;
  }
  function reveal(id, focus) {
    const target = $(id);
    target.open = true;
    target.scrollIntoView({block: 'start', behavior: 'auto'});
    if (focus) $(focus).focus({preventScroll: true});
  }
  function characterView(value) {
    character = value;
    $('journal-edition').textContent = `VOL. ${String(value.appearance_revision).padStart(2, '0')}`;
    const persona = value.profile.persona;
    $('journal-persona').textContent = persona ? persona.split('\n').filter(Boolean).slice(0, 2).join(' ').slice(0, 130) :
      '她的性格与偏好还未写下。翻开人物档案，一起确定最初的方向。';
    $('journal-tags').replaceChildren(...(persona ? ['共同角色 v' + value.revision,
      value.profile.appearance.references.length ? '外貌已留档' : '外貌待生成',
      value.profile.voice.references.length ? '声音已留档' : '声音待设定'] : ['等待定义角色']).map(t => node('span', t)));
  }
  window.addEventListener('automedia:character', e => characterView(e.detail));
  $('journal-character-open').onclick = () => reveal('journal-character', 'rt-persona');
  $('journal-goal').onclick = () => reveal('mind-compose', 'mind-objective');
  $('journal-letter').onclick = () => $('input').focus();
  $('journal-appearance').onclick = () => reveal('journal-album');
  $('journal-close').onclick = () => $('journal-dialog').close();
  $('journal-shuffle').onclick = () => {
    idea = (idea + 1) % ideas.length;
    $('journal-idea-title').textContent = ideas[idea][0];
    $('journal-idea-text').textContent = ideas[idea][1];
  };
  $('journal-use').onclick = () => {
    const input = $('input');
    input.value = [input.value.trim(), `灵感：${ideas[idea][0]}。${ideas[idea][1]} 请先形成本地构思。`].filter(Boolean).join('\n\n');
    input.dispatchEvent(new Event('input', {bubbles: true})); input.focus();
  };
  async function openPage(item) {
    if (item.kind === 'appearance') { reveal('journal-album'); return; }
    $('journal-dialog-title').textContent = item.title;
    const body = $('journal-dialog-body');
    body.replaceChildren(node('p', `${item.source} · ${new Date(item.at).toLocaleString()}`, 'journal-meta'),
      node('p', states[item.state] || item.state, 'journal-state-tag'), node('p', item.excerpt, 'runtime-output'));
    $('journal-dialog').showModal();
    if (item.kind === 'archive') {
      body.append(node('p', '历史工具记录，仅表示当时观察或操作；不能据此确认整个任务或发布已完成。', 'journal-meta'));
      for (const note of item.notes || []) {
        const row = node('div', undefined, 'journal-note');
        row.append(node('strong', `${note.time} · ${note.label}`), node('p', note.text),
          node('small', note.status)); body.append(row);
      }
      if (item.related_sources?.length) {
        const sources = node('details', undefined, 'ops-details');
        sources.append(node('summary', '查看来源记录'), node('p', item.related_sources.join('\n'), 'journal-meta'));
        body.append(sources);
      }
      return;
    }
    const section = node('div'); body.append(section);
    try {
      const {events} = await api('/runtime/events?job_id=' + encodeURIComponent(item.job_id));
      for (const ev of events) {
        const p = ev.payload || {}, row = node('details', undefined, 'ops-details');
        const names = {request: '这次想做的事', decision: '形成的判断', action_receipt: '实际操作回执',
          speech_draft: '写下的表达', controller_response: '给你的手记', review: '独立核对', job_terminal: '最后的状态'};
        row.append(node('summary', `${names[ev.kind] || '过程记录'} · ${new Date(ev.created_at).toLocaleTimeString()}`));
        const text = p.summary || p.text || p.draft?.text || p.decision?.summary || p.objective || p.error || p.state;
        if (text) row.append(node('p', String(text), 'runtime-output'));
        const raw = node('details', undefined, 'ops-details');
        raw.append(node('summary', '查看原始证据'), node('pre', JSON.stringify(p, null, 2), 'runtime-evidence'));
        row.append(raw); section.append(row);
      }
    } catch (e) { section.textContent = e.message; }
  }
  function renderPages() {
    const chosen = items.filter(x => filter === 'all' || x.kind === filter);
    $('journal-state').textContent = chosen.length ? `${chosen.length} 条真实记录 · 打开一页看详情` :
      '这一页还空着。完成一次观察或创作，记录就会留在这里。';
    $('journal-pages').replaceChildren(...chosen.slice(0, limit).map((item, i) => {
      const button = node('button', undefined, 'journal-page');
      button.type = 'button'; button.dataset.state = item.state;
      const head = node('div', undefined, 'journal-page-head');
      head.append(node('span', `${String(i + 1).padStart(2, '0')} / ${item.source}`),
        node('time', new Date(item.at).toLocaleDateString('zh-CN', {month: '2-digit', day: '2-digit'})));
      button.append(head, node('strong', item.title), node('p', item.excerpt),
        node('span', (states[item.state] || item.state) + '　↗', 'journal-state-tag'));
      button.onclick = () => openPage(item); return button;
    }));
    $('journal-more').hidden = chosen.length <= limit;
  }
  document.querySelectorAll('[data-journal-filter]').forEach(button => {
    button.onclick = () => {
      filter = button.dataset.journalFilter; limit = 6;
      document.querySelectorAll('[data-journal-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
      renderPages();
    };
  });
  $('journal-more').onclick = () => { limit += 6; renderPages(); };
  function renderAlbum(versions) {
    generating = versions.some(v => ['queued', 'generating', 'reviewing'].includes(v.state));
    $('journal-generate').disabled = generating;
    $('journal-album-state').textContent = generating ? '正在生成' :
      versions.some(v => v.state === 'completed') ? '已有形象' : '等待生成';
    const next = JSON.stringify(versions);
    if (next === albumSig) return;
    albumSig = next;
    $('journal-portraits').replaceChildren(...versions.map(v => {
      const card = node('article', undefined, 'journal-portrait');
      card.append(node('strong', v.state === 'completed' ? '外貌版本 · 角色 v' + v.character_revision : '生成记录'),
        node('p', v.message), node('small', new Date(v.created_at).toLocaleString()));
      if (v.web_available && v.state === 'completed') {
        const img = node('img'); img.src = v.web_source; img.alt = '生成的基础角色参考';
        img.loading = 'lazy'; img.onerror = () => { img.hidden = true; };
        card.append(img);
      }
      for (const asset of v.assets || []) {
        const a = node('a', asset.role === 'reference' ? '打开基础形象 ↗' : '打开场景版本 ↗', 'btn');
        a.href = '/appearance/' + encodeURIComponent(v.id) + '/' + encodeURIComponent(asset.role);
        a.target = '_blank'; a.rel = 'noopener'; card.append(a);
      }
      if (v.web_message) card.append(node('p', v.web_message, 'journal-meta'));
      return card;
    }));
    if (!versions.length) $('journal-portraits').append(node('p', '无需上传照片。保存外貌方向后，就能生成第一版。'));
  }
  $('journal-generate').onclick = async () => {
    $('journal-generate').disabled = true;
    try {
      if (window.automediaCharacterDirty?.()) throw Error('人物档案有未保存修改，请先保存。');
      if (!character) throw Error('角色还未读取，请稍后重试。');
      const result = await api('/appearance', {expected_revision: character.revision});
      $('journal-generation-message').textContent = result.message;
      await refresh();
    } catch (e) {
      $('journal-generation-message').textContent = e.message;
    } finally { $('journal-generate').disabled = generating; }
  };
  async function refresh() {
    if (loading) return; loading = true;
    try {
      const results = await Promise.allSettled([api('/journal'), api('/appearance'), api('/runtime/health'), api('/runtime/character')]);
      const [journal, album, health, persona] = results;
      if (journal.status === 'fulfilled') {
        const sig = JSON.stringify(journal.value);
        if (sig !== journalSig) { items = journal.value.items; journalSig = sig; renderPages(); }
      } else { $('journal-state').textContent = '手记暂时无法读取，已有内容保留；稍后会重新连接。'; journalSig = ''; }
      if (album.status === 'fulfilled') renderAlbum(album.value.versions);
      else $('journal-album-state').textContent = '形象册暂时不可用';
      if (persona.status === 'fulfilled') characterView(persona.value);
      const h = health.status === 'fulfilled' ? health.value : null;
      const working = Math.max(h?.active_jobs || 0,
        items.filter(i => ['queued', 'thinking', 'behavior', 'talk', 'running'].includes(i.state)).length);
      $('journal-now').textContent = !h ? '暂时失去连接，状态待核对' : h.scheduler !== 'running' ?
        '调度需要检查' : working || h.active_reflections ? `正在推进 ${working} 件事，整理 ${h.active_reflections} 个判断` :
          '此刻没有进行中的任务 · 可以交给她一件新鲜事';
    } finally { loading = false; }
  }
  refresh();
  setInterval(() => {
    if (!document.hidden && $('app').dataset.studioView === 'operations') refresh();
  }, 8000);
})();
