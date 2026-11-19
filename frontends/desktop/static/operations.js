(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const fields = {
    identity: ['身份与定位', '你的身份、赛道、地区；真人或虚构角色也请注明'],
    audience: ['目标受众', '希望帮助谁，他们遇到了什么问题'],
    pillars: ['内容栏目', '想持续做的系列，以及内容形式'],
    voice: ['表达风格', '口吻、常用词、喜欢和不喜欢的表达'],
    boundaries: ['价值与边界', '不作的承诺、不涉及的内容'],
    goal: ['运营目标', '想吸引什么样的关注，如何判断进步'],
  };
  const statuses = {queued: '等待开始', running: '策划与分析中', reviewing: '独立核对中',
    completed: '计划已核对', blocked: '需要补充或修订', failed: '生成未完成',
    stale: '资料已变更', cancelled: '已停止', interrupted: '重启后中断'};
  const kinds = {experience: '真实经历', observation: '观察记录', hypothesis: '待验证假设'};
  let account = null, dirty = false, loading = false, plansBusy = false, plansFingerprint = '';
  let activeTab = 'profile', accountEpoch = 0, planRequest = null, pendingNotice = null;
  const readLocal = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  const writeLocal = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const message = (value, error = false) => { $('ops-message').textContent = value; $('ops-message').dataset.error = error; };
  const node = (tag, value, className) => {
    const item = document.createElement(tag);
    if (value !== undefined) item.textContent = value;
    if (className) item.className = className;
    return item;
  };
  function markDirty(value) {
    dirty = value;
    $('ops-dirty').textContent = value ? '有未保存的修改 · 保存后影响新计划' : '所有修改已保存';
  }
  async function api(path, data) {
    const response = await fetch('/ops/' + path, data === undefined
      ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    let result;
    try { result = await response.json(); } catch (_) { throw new Error('运营服务暂时不可用，请确认已启动新版服务'); }
    if (!response.ok) throw new Error(result.error || '请求失败，请稍后重试');
    return result;
  }
  function scoped(action, id = account?.id) { return `${encodeURIComponent(id)}/${action}`; }
  async function action(button, work) {
    button.disabled = true;
    try { await work(); } catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
  }
  function renderProfile(data) {
    account = data;
    $('ops-version').textContent = `人设 v${data.revision} · 记忆 v${data.memory_revision}`;
    $('ops-fields').replaceChildren();
    for (const [key, [label, hint]] of Object.entries(fields)) {
      const group = node('div'), head = node('div', undefined, 'ops-field-head');
      const title = node('label', label); title.htmlFor = `ops-field-${key}`;
      const lockLabel = node('label', undefined, 'ops-check');
      const lock = node('input'); lock.type = 'checkbox'; lock.id = `ops-lock-${key}`;
      lock.checked = data.profile[key].locked; lockLabel.append(lock, document.createTextNode('保护 AI 更新'));
      const input = node('textarea'); input.id = `ops-field-${key}`; input.rows = 3; input.maxLength = 4000;
      input.value = data.profile[key].value; input.placeholder = hint;
      head.append(title, lockLabel); group.append(head, input); $('ops-fields').append(group);
    }
    markDirty(false);
  }
  async function history() {
    const epoch = accountEpoch, id = account.id;
    const result = await api(scoped('history', id));
    if (epoch !== accountEpoch) return;
    $('ops-history').replaceChildren(...result.history.map(row => {
      const details = node('details', undefined, 'ops-history-row');
      details.append(node('summary', `v${row.revision} · ${row.actor === 'user' ? '人工编辑' : 'AI更新'} · ${new Date(row.created_at).toLocaleString()}`));
      for (const [key, item] of Object.entries(row.profile)) details.append(node('p', `${fields[key][0]}：${item.value || '尚未填写'}${item.locked ? '（已锁定）' : ''}`));
      return details;
    }));
  }
  async function selectAccount(id) {
    const epoch = ++accountEpoch;
    $('ops-workspace').hidden = true;
    account = null;
    if (!id) { $('ops-empty').hidden = false; return; }
    const data = await api(scoped('profile', id));
    if (epoch !== accountEpoch) return;
    renderProfile(data);
    $('ops-account').value = id;
    $('ops-empty').hidden = true; $('ops-workspace').hidden = false;
    $('ops-memory-form').reset(); $('ops-search').value = ''; $('ops-show-inactive').checked = false;
    $('ops-plan-form').reset(); planRequest = null; pendingNotice = null; plansFingerprint = '';
    writeLocal('automedia:ops-account', id);
    await history(); await loadMemories(); await loadPlans();
  }
  async function loadAccounts(selected) {
    const data = await api('accounts');
    $('ops-account').replaceChildren(...data.accounts.map(a => {
      const option = node('option', a.name); option.value = a.id; return option;
    }));
    const chosen = data.accounts.find(a => a.id === selected)?.id || data.accounts[0]?.id;
    if (!chosen) { const option = node('option', '请先创建档案'); option.value = ''; $('ops-account').append(option); }
    await selectAccount(chosen);
  }
  function openNew() {
    $('ops-new-form').hidden = false; $('ops-new-toggle').setAttribute('aria-expanded', 'true'); $('ops-name').focus();
  }
  $('ops-new-toggle').addEventListener('click', () => {
    if (!$('ops-new-form').hidden) { $('ops-new-form').hidden = true; $('ops-new-toggle').setAttribute('aria-expanded', 'false'); }
    else openNew();
  });
  $('ops-empty-create').addEventListener('click', openNew);
  $('ops-new-form').addEventListener('submit', event => {
    event.preventDefault();
    if (dirty) { message('请先保存当前人设，或读取最新档案放弃表单修改。', true); return; }
    action(event.submitter, async () => {
      const data = await api('accounts', {name: $('ops-name').value});
      $('ops-new-form').reset(); $('ops-new-form').hidden = true;
      $('ops-new-toggle').setAttribute('aria-expanded', 'false');
      await loadAccounts(data.id); message('档案已创建。先写下你希望它记住的身份与风格。');
    });
  });
  $('ops-account').addEventListener('change', async () => {
    if (dirty) { $('ops-account').value = account.id; message('人设尚未保存，请先保存或读取最新档案后切换。', true); return; }
    try { message(''); await selectAccount($('ops-account').value); } catch (error) { message(error.message, true); }
  });
  $('ops-profile-form').addEventListener('input', () => markDirty(true));
  $('ops-profile-form').addEventListener('submit', event => {
    event.preventDefault();
    action($('ops-save'), async () => {
      const id = account.id, epoch = accountEpoch, updates = {};
      for (const key of Object.keys(fields)) updates[key] = {value: $(`ops-field-${key}`).value, locked: $(`ops-lock-${key}`).checked};
      const data = await api(scoped('profile', id), {expected_revision: account.revision, fields: updates});
      if (epoch !== accountEpoch) return;
      renderProfile(data); await history(); await loadPlans();
      message(`人设 v${data.revision} 已保存；旧版本计划会标记过期。`);
    });
  });
  $('ops-reload').addEventListener('click', () => action($('ops-reload'), async () => {
    const epoch = accountEpoch;
    const data = await api(scoped('profile'));
    if (epoch !== accountEpoch) return;
    renderProfile(data); await history(); message('表单已替换为最新保存版本。');
  }));
  function showTab(tab) {
    activeTab = tab;
    for (const name of ['profile', 'memory', 'plan']) {
      $(`ops-pane-${name}`).hidden = name !== tab;
      $(`ops-tab-${name}`).setAttribute('aria-selected', String(name === tab));
      $(`ops-tab-${name}`).tabIndex = name === tab ? 0 : -1;
    }
  }
  document.querySelectorAll('[data-ops-tab]').forEach(button => {
    button.addEventListener('click', () => showTab(button.dataset.opsTab));
    button.addEventListener('keydown', event => {
      const names = ['profile', 'memory', 'plan'];
      let next = names.indexOf(activeTab);
      if (event.key === 'ArrowRight') next = (next + 1) % 3;
      else if (event.key === 'ArrowLeft') next = (next + 2) % 3;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = 2;
      else return;
      event.preventDefault(); showTab(names[next]); $(`ops-tab-${names[next]}`).focus();
    });
  });
  async function loadMemories() {
    const id = account?.id, epoch = accountEpoch; if (!id) return;
    const result = await api(scoped(`memories?q=${encodeURIComponent($('ops-search').value)}&all=${$('ops-show-inactive').checked ? 1 : 0}`, id));
    if (epoch !== accountEpoch) return;
    $('ops-memories').replaceChildren(...result.memories.map(memory => {
      const item = node('article', undefined, 'ops-memory-item');
      const expired = memory.valid_until && Date.parse(memory.valid_until) <= Date.now();
      item.dataset.kind = memory.kind; item.dataset.inactive = memory.state !== 'active' || expired;
      item.append(node('span', `${kinds[memory.kind]}${memory.state !== 'active' ? ' · 已撤回' : expired ? ' · 已过期' : ''}`, 'ops-memory-kind'),
        node('p', memory.content), node('p', `来源：${memory.source}\n发生：${memory.occurred_at}${memory.valid_until ? `\n有效至：${new Date(memory.valid_until).toLocaleString()}` : ''}`, 'ops-memory-meta'));
      if (memory.state === 'active') {
        const button = node('button', '撤回这条记忆', 'btn'); button.type = 'button';
        button.addEventListener('click', () => action(button, async () => {
          await api(scoped('retract', id), {id: memory.id});
          if (epoch !== accountEpoch) return;
          await loadMemories(); await loadPlans(); await refreshVersion();
          message('已撤回，今后的计划不再使用；旧计划保留历史快照并标记过期。');
        }));
        item.append(button);
      }
      return item;
    }));
    if (!result.memories.length) $('ops-memories').append(node('p', '没有匹配的记忆。添加真实素材，或换一个关键词。', 'ops-scope'));
  }
  async function refreshVersion() {
    const epoch = accountEpoch, data = await api(scoped('profile'));
    if (epoch !== accountEpoch) return;
    $('ops-version').textContent = `最新人设 v${data.revision} · 记忆 v${data.memory_revision}`;
    // Do not change the edit base revision: another tab may have saved newer fields.
    if (data.revision === account.revision) account.memory_revision = data.memory_revision;
  }
  $('ops-memory-form').addEventListener('submit', event => {
    event.preventDefault();
    action(event.submitter, async () => {
      const epoch = accountEpoch, expiry = $('ops-expiry').value;
      await api(scoped('memories'), {kind: $('ops-kind').value, content: $('ops-memory-content').value,
        source: $('ops-source').value, occurred_at: $('ops-occurred').value,
        valid_until: expiry ? new Date(expiry).toISOString() : null});
      if (epoch !== accountEpoch) return;
      $('ops-memory-form').reset(); await loadMemories(); await refreshVersion(); await loadPlans();
      message('记忆已保存，下一次计划可以引用它的来源。');
    });
  });
  $('ops-search-form').addEventListener('submit', event => { event.preventDefault(); action(event.submitter, loadMemories); });
  $('ops-show-inactive').addEventListener('change', () => loadMemories().catch(error => message(error.message, true)));
  function list(parent, title, values) {
    if (!values?.length) return;
    parent.append(node('p', title));
    const ul = node('ul'); for (const value of values) ul.append(node('li', value)); parent.append(ul);
  }
  function rawFailure(parent, record) {
    if (!record.raw_output || record.state !== 'failed') return;
    const detail = node('details', undefined, 'ops-details');
    detail.append(node('summary', '查看未通过校验的原始产物（不可采纳）'),
      node('p', JSON.stringify(record.raw_output, null, 2), 'ops-evidence'));
    parent.append(detail);
  }
  function renderPlan(job, index) {
    const card = node('details', undefined, 'ops-plan'); card.dataset.planId = job.id; card.open = index === 0;
    const summary = node('summary');
    summary.append(node('span', job.outdated ? '资料已变化 · 历史计划' : statuses[job.state], 'ops-plan-state'), document.createTextNode(job.objective));
    const body = node('div', undefined, 'ops-plan-body');
    const ctx = job.context, active = ['queued', 'running', 'reviewing'].includes(job.state);
    body.append(node('p', `${new Date(job.created_at).toLocaleString()} · 人设 v${ctx.profile_revision} · 记忆 v${ctx.memory_revision}`, 'ops-scope'));
    if (job.outdated) body.append(node('p', '这份计划引用的资料已变化或过期。请重新生成。', 'ops-plan-error'));
    const flow = node('div', undefined, 'ops-flow');
    for (const id of ['content', 'diagnosis', 'review']) {
      const record = id === 'review' ? job.review : job.branches[id];
      const state = record ? record.state || 'completed' : active ? (id === 'review' && job.state !== 'reviewing' ? 'waiting' : 'running') : 'waiting';
      const step = node('span', `${{content: '内容策划', diagnosis: '运营分析', review: '独立核对'}[id]} · ${record?.elapsed_seconds ? record.elapsed_seconds + '秒' : {waiting: '待处理', running: '进行中', completed: '已返回', failed: '失败'}[state]}`);
      step.dataset.state = state; flow.append(step);
    }
    body.append(flow);
    if (active) {
      body.append(node('p', '可以离开此页，计划会继续运行；停止后保留已返回的内容。', 'ops-scope'));
      const cancel = node('button', '停止本次生成', 'btn'); cancel.type = 'button';
      cancel.addEventListener('click', () => action(cancel, async () => {
        await api(scoped('cancel', job.account_id), {id: job.id}); await loadPlans(); message('生成已停止。');
      })); body.append(cancel);
    }
    if (job.error) body.append(node('p', job.error, 'ops-plan-error'));
    for (const id of ['content', 'diagnosis']) {
      const branch = job.branches[id]; if (!branch) continue;
      const section = node('section', undefined, 'ops-output');
      section.append(node('h4', branch.name));
      if (branch.error) section.append(node('p', branch.error, 'ops-plan-error'));
      rawFailure(section, branch);
      const output = branch.output;
      if (output) {
        section.append(node('p', output.summary));
        for (const idea of output.ideas || []) {
          const piece = node('div', undefined, 'ops-idea');
          piece.append(node('strong', idea.title), node('p', idea.opening));
          list(piece, '准备步骤', idea.steps); list(piece, '还需要', idea.missing);
          list(piece, '引用记忆', idea.evidence_ids); section.append(piece);
        }
        if (output.decision) section.append(node('p', `建议：${{wait: '继续观察', prepare: '先做准备', propose_revision: '准备修改提案'}[output.decision]} · 尚未执行`, 'ops-scope'));
        list(section, '还缺哪些依据', output.missing);
        list(section, '下一步', output.next_actions);
        list(section, '观察安排', output.observation_plan);
      }
      body.append(section);
    }
    if (job.review?.output) {
      const section = node('section', undefined, 'ops-output'), output = job.review.output;
      section.append(node('h4', output.verdict === 'pass' ? '独立核对 · 可供参考' : '独立核对 · 需要修订'), node('p', output.summary));
      list(section, '需修订问题', output.issues); list(section, '下一步', output.next_actions); body.append(section);
    }
    if (job.review?.state === 'failed') rawFailure(body, job.review);
    const evidence = node('details', undefined, 'ops-details'); evidence.dataset.detail = 'evidence';
    evidence.append(node('summary', `查看本次依据与方法版本 · ${ctx.memories.length} 条记忆`));
    evidence.append(node('p', `${ctx.retrieval}\n${ctx.evidence_boundary}\n方法：${Object.entries(ctx.skill_versions).map(([key, value]) => `${key} v${value}`).join(' / ')}`, 'ops-evidence'));
    for (const [key, value] of Object.entries(ctx.profile)) evidence.append(node('p', `${fields[key][0]}：${value.value || '未填写'}`, 'ops-evidence'));
    for (const memory of ctx.memories) evidence.append(node('p', `${memory.id}\n${kinds[memory.kind]}：${memory.content}\n来源：${memory.source}\n发生：${memory.occurred_at}`, 'ops-evidence'));
    body.append(evidence); card.append(summary, body); return card;
  }
  async function loadPlans() {
    if (!account || plansBusy) return;
    plansBusy = true; const epoch = accountEpoch;
    try {
      const result = await api(scoped('plans'));
      if (epoch !== accountEpoch) return;
      const active = result.plans.some(p => ['queued', 'running', 'reviewing'].includes(p.state));
      $('ops-generate').disabled = active;
      const finished = result.plans.find(p => p.id === pendingNotice && !['queued', 'running', 'reviewing'].includes(p.state));
      if (finished) {
        message(`${statuses[finished.state]}。展开记录查看结果和依据。`, ['failed', 'blocked', 'stale'].includes(finished.state));
        pendingNotice = null;
      }
      const fingerprint = JSON.stringify(result.plans);
      if (fingerprint === plansFingerprint) return;
      plansFingerprint = fingerprint;
      const open = new Map([...$('ops-plans').querySelectorAll('.ops-plan')].map(item =>
        [item.dataset.planId, {open: item.open, evidence: item.querySelector('[data-detail=evidence]')?.open}]));
      $('ops-plans').replaceChildren(...result.plans.map(renderPlan));
      for (const item of $('ops-plans').querySelectorAll('.ops-plan')) {
        const saved = open.get(item.dataset.planId);
        if (saved) { item.open = saved.open; item.querySelector('[data-detail=evidence]').open = saved.evidence; }
      }
      if (!result.plans.length) $('ops-plans').append(node('p', '还没有计划。先保存人设和材料，再写下本轮目标。', 'ops-scope'));
    } finally { plansBusy = false; }
  }
  $('ops-plan-form').addEventListener('submit', event => {
    event.preventDefault();
    if (dirty) { message('请先保存人设，计划将使用已保存的版本。', true); return; }
    action($('ops-generate'), async () => {
      const epoch = accountEpoch, objective = $('ops-objective').value, memory_query = $('ops-memory-query').value;
      const signature = JSON.stringify([account.id, objective, memory_query]);
      if (planRequest?.signature !== signature) planRequest = {signature, key: crypto.randomUUID()};
      const job = await api(scoped('plans'), {objective, memory_query, request_key: planRequest.key});
      planRequest = null;
      if (epoch !== accountEpoch) return;
      pendingNotice = job.id;
      plansFingerprint = ''; await loadPlans();
      message(`计划已创建，正在并行策划与分析。编号 ${job.id.slice(-8)}`);
    }).then(() => { loadPlans().catch(error => message(error.message, true)); });
  });
  $('ops-refresh-plans').addEventListener('click', () => action($('ops-refresh-plans'), loadPlans));
  async function init() {
    if (loading) return; loading = true;
    try { await loadAccounts(readLocal('automedia:ops-account')); }
    catch (error) { message(error.message + '；点击“运营者”重试。', true); }
    finally { loading = false; }
  }
  document.querySelector('[data-studio-nav=operations]').addEventListener('click', () => {
    if (!account) init();
    else loadPlans().catch(error => message(error.message, true));
  });
  setInterval(() => {
    if (account && !document.hidden && $('app').dataset.studioView === 'operations' && activeTab === 'plan')
      loadPlans().catch(error => message(error.message, true));
  }, 3000);
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  init();
})();
