(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let character, dirty = false, pending = null, fingerprint = '', busy = false;
  const active = ['queued', 'thinking', 'behavior', 'talk', 'running'];
  const labels = {queued: '等待调度', thinking: '形成意图', behavior: '观察执行',
    talk: '表达与核对', running: '执行中', completed: '已完成', blocked: '受阻 / 待修订',
    cancelled: '已停止', stale: '资料变化，需重新准备', failed: '未完成', interrupted: '中断，证据已保留'};
  const node = (tag, text, className) => {
    const n = document.createElement(tag); if (text !== undefined) n.textContent = text;
    if (className) n.className = className; return n;
  };
  async function api(path, data) {
    const r = await fetch('/runtime/' + path, data === undefined ? {} :
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const result = await r.json();
    if (!r.ok) throw Error(result.error || '服务暂时不可用');
    return result;
  }
  const notice = value => { $('rt-message').textContent = value; };
  async function action(button, work) {
    button.disabled = true;
    try { await work(); } catch (e) { notice(e.message); } finally { button.disabled = false; }
  }
  async function loadCharacter() {
    character = await api('character');
    $('rt-character-version').textContent = `v${character.revision} · 外貌 v${character.appearance_revision} · 声音 v${character.voice_revision}`;
    $('rt-persona').value = character.profile.persona;
    for (const key of ['voice', 'appearance']) {
      $(`rt-${key}`).value = character.profile[key].description;
      $(`rt-${key}-path`).value = character.profile[key].references[0]?.path || '';
      $(`rt-${key}-source`).value = character.profile[key].references[0]?.source || '';
    }
    dirty = false;
    window.dispatchEvent(new CustomEvent('automedia:character', {detail: character}));
  }
  $('rt-character-form').addEventListener('input', () => { dirty = true; });
  window.automediaCharacterDirty = () => dirty;
  $('rt-character-form').addEventListener('submit', e => {
    e.preventDefault();
    action(e.submitter, async () => {
      const profile = structuredClone(character.profile); profile.persona = $('rt-persona').value;
      for (const key of ['voice', 'appearance']) {
        profile[key].description = $(`rt-${key}`).value;
        const path = $(`rt-${key}-path`).value.trim(), source = $(`rt-${key}-source`).value.trim();
        if (Boolean(path) !== Boolean(source)) throw Error('参考资产请同时填写路径和来源。');
        profile[key].references = [...(path ? [{path, source}] : []), ...profile[key].references.slice(1)];
      }
      await api('character', {expected_revision: character.revision, profile});
      await loadCharacter(); notice('角色已保存，新任务使用新版本；运行中的旧版本任务会停止。');
    });
  });
  $('rt-character-reload').onclick = e => action(e.currentTarget, loadCharacter);
  $('rt-job-form').addEventListener('submit', e => {
    e.preventDefault();
    action(e.submitter, async () => {
      if (dirty) throw Error('请先保存共同角色，或读取最新角色后开始。');
      const data = {kind: $('rt-kind').value, objective: $('rt-objective').value,
        account_id: $('ops-account').value || null};
      const sig = JSON.stringify(data);
      if (pending?.sig !== sig) pending = {sig, key: crypto.randomUUID()};
      const job = await api('jobs', {...data, request_key: pending.key});
      pending = null; notice(`任务 ${job.id.slice(-8)} 已加入队列。`); await loadJobs();
    });
  });
  async function loadJobs() {
    if (busy) return; busy = true;
    try {
      const {jobs} = await api('jobs'), next = JSON.stringify(jobs);
      if (next === fingerprint) return;
      fingerprint = next;
      const open = new Set([...$('rt-jobs').querySelectorAll('details[open][data-job]')].map(n => n.dataset.job));
      $('rt-jobs').replaceChildren(...jobs.map((job, index) => {
        const d = node('details', undefined, 'ops-plan'); d.dataset.job = job.id;
        d.open = open.has(job.id);
        d.append(node('summary', `${labels[job.state]} · ${job.objective.slice(0,120)}`));
        const body = node('div', undefined, 'ops-plan-body');
        body.append(node('p', `${new Date(job.created_at).toLocaleString()} · 角色 v${job.context.character.revision} · ${job.context.account?.name || '共同角色'}`, 'ops-scope'));
        if (job.error) body.append(node('p', job.error, 'ops-plan-error'));
        if (job.result?.draft) {
          body.append(node('p', job.result.draft.text ||
            `${job.result.draft.title}\n\n${job.result.draft.desc}`, 'runtime-output'));
          for (const item of job.result.draft.limitations || []) body.append(node('p', item, 'ops-scope'));
          body.append(node('p', '本地表达草稿 · 未向平台发送', 'ops-scope'));
        }
        if (active.includes(job.state)) {
          const stop = node('button', job.cancel_requested ? '正在停止' : '停止任务', 'btn');
          stop.type = 'button'; stop.disabled = Boolean(job.cancel_requested);
          stop.onclick = () => action(stop, async () => { await api('cancel', {job_id: job.id}); await loadJobs(); });
          body.append(stop);
        }
        const evidence = node('details', undefined, 'ops-details');
        evidence.append(node('summary', '查看观察、判断与表达记录'));
        const events = node('div'); evidence.append(events);
        evidence.addEventListener('toggle', async () => {
          if (!evidence.open) return;
          try {
            const result = await api(`events?job_id=${encodeURIComponent(job.id)}`);
            events.replaceChildren(...result.events.map(ev => {
              const row = node('details', undefined, 'ops-history-row');
              row.append(node('summary', `${ev.domain} · ${ev.kind} · ${new Date(ev.created_at).toLocaleTimeString()}`),
                node('pre', JSON.stringify(ev.payload, null, 2), 'runtime-evidence'));
              return row;
            }));
          } catch(e) { events.textContent = e.message; }
        });
        body.append(evidence); d.append(body); return d;
      }));
      if (!jobs.length) $('rt-jobs').append(node('p', '还没有三域任务。可以先运行一次本地草稿或抖音研究。', 'ops-scope'));
    } finally { busy = false; }
  }
  $('rt-refresh').onclick = e => action(e.currentTarget, loadJobs);
  setInterval(() => {
    if (!document.hidden && $('app').dataset.studioView === 'operations')
      loadJobs().catch(e => notice(e.message));
  }, 3000);
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  loadCharacter().then(loadJobs).catch(e => notice(e.message));
})();
