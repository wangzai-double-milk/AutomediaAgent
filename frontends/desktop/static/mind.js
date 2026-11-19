(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const states = {ready: '准备下一步', reflecting: '整理判断', running: '推进中', waiting: '等待新信息',
    blocked: '需要处理', paused: '已暂停', completed: '已完成', cancelled: '已停止', stale: '资料已变化'};
  const finished = ['completed', 'cancelled', 'stale'];
  let signature = '', pending, loading = false;
  const rows = new Map();
  const node = (tag, content, cls) => {
    const n = document.createElement(tag);
    if (content !== undefined) n.textContent = content;
    if (cls) n.className = cls;
    return n;
  };
  async function api(path, data) {
    const response = await fetch('/runtime/' + path, data === undefined ? {} :
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '暂时无法读取');
    return result;
  }
  async function act(button, work) {
    button.disabled = true;
    try { await work(); } catch (e) { $('rt-message').textContent = e.message; }
    finally { button.disabled = false; }
  }
  function build(goal) {
    const article = node('article', undefined, 'mind-goal');
    article.dataset.goal = goal.id;
    const heading = node('div', undefined, 'mind-heading');
    const title = node('h4'), status = node('span', undefined, 'mind-state');
    heading.append(title, status);
    const summary = node('p', undefined, 'runtime-output');
    const next = node('p', undefined, 'ops-scope');
    const detail = node('details', undefined, 'ops-details');
    detail.append(node('summary', '依据与待确认事项'));
    const criteria = node('p'), unknown = node('div'), history = node('div');
    detail.append(criteria, unknown, history);
    detail.addEventListener('toggle', async () => {
      if (!detail.open) return;
      try {
        const {events} = await api(`goal-events?goal_id=${encodeURIComponent(goal.id)}`);
        history.replaceChildren(...events.filter(e =>
          ['reflection', 'job_result', 'controller_statement', 'source_evidence'].includes(e.kind)).map(e => {
          const item = node('div', undefined, 'ops-history-row');
          const p = e.payload;
          item.append(node('p', e.kind === 'reflection' ? p.decision.summary :
            e.kind === 'controller_statement' ? '补充信息：' + p.content :
              e.kind === 'source_evidence' ? '关注来源：' + (p.content || p.objective) : '工作结果：' + p.objective));
          if (p.evidence_boundary) item.append(node('p', p.evidence_boundary, 'ops-scope'));
          if (p.result?.draft?.text) item.append(node('p', p.result.draft.text, 'runtime-output'));
          if (e.kind === 'reflection') item.append(node('p', p.decision.reason, 'ops-scope'));
          if (p.error) item.append(node('p', p.error, 'ops-plan-error'));
          return item;
        }));
      } catch (e) { history.textContent = e.message; }
    });
    const controls = node('div', undefined, 'mind-controls');
    for (const [action, label] of [['pause', '暂停'], ['resume', '继续'], ['cancel', '停止跟进']]) {
      const button = node('button', label, 'btn'); button.type = 'button'; button.dataset.action = action;
      button.onclick = () => act(button, async () => {
        await api('goal-control', {goal_id: goal.id, expected_revision: rows.get(goal.id).goal.revision, action});
        await refresh();
      });
      controls.append(button);
    }
    const signal = node('form', undefined, 'mind-signal');
    const input = node('textarea'); input.rows = 2; input.required = true; input.maxLength = 12000;
    input.id = 'signal-' + goal.id;
    const label = node('label', '补充新信息'); label.htmlFor = input.id;
    const send = node('button', '补充', 'btn'); send.type = 'submit';
    let signalKey = null;
    signal.onsubmit = e => {
      e.preventDefault();
      act(send, async () => {
        if (signalKey?.value !== input.value) signalKey = {value: input.value, key: crypto.randomUUID()};
        await api('goal-signal', {goal_id: goal.id, content: input.value, request_key: signalKey.key});
        signalKey = null; input.value = ''; await refresh();
      });
    };
    signal.append(label, input, send);
    const watch = node('details', undefined, 'ops-details');
    watch.append(node('summary', '关注哪些新信息'));
    const watchBody = node('div');
    watch.append(watchBody);
    async function loadWatches() {
      const data = await api('goal-watch?goal_id=' + encodeURIComponent(goal.id));
      watchBody.replaceChildren(node('p', data.scope, 'ops-scope'));
      if (!data.options.length) watchBody.append(node('p', '先准备会话、内容母稿或研究任务，再关联到这个目标。'));
      const kinds = {conversation: '会话', content: '母稿', job: '任务'};
      for (const option of data.options) {
        const label = node('label');
        label.style.display = 'block';
        const checkbox = node('input'); checkbox.type = 'checkbox';
        checkbox.checked = data.watches.some(w => w.kind === option.kind &&
          w.source_id === option.source_id && w.enabled);
        checkbox.disabled = finished.includes(rows.get(goal.id).goal.state);
        checkbox.onchange = () => act(checkbox, async () => {
          try {
            await api('goal-watch', {goal_id: goal.id, expected_revision: rows.get(goal.id).goal.revision,
              kind: option.kind, source_id: option.source_id, enabled: checkbox.checked});
          } catch (e) { checkbox.checked = !checkbox.checked; throw e; }
        });
        label.append(checkbox, document.createTextNode(' ' + kinds[option.kind] + ' · ' + option.label));
        watchBody.append(label);
      }
    }
    watch.addEventListener('toggle', () => {
      if (watch.open) loadWatches().catch(e => { watchBody.textContent = e.message; });
    });
    article.append(heading, summary, next, detail, controls, signal, watch);
    return {article, title, status, summary, next, detail, criteria, unknown, controls, signal, goal};
  }
  function update(row, goal) {
    row.goal = goal;
    row.title.textContent = goal.objective;
    row.status.textContent = states[goal.state] || goal.state;
    row.summary.textContent = goal.summary || (goal.state === 'cancelled' ? '已停止跟进，记录保留。' :
      goal.state === 'paused' ? '已暂停，继续后再安排。' : goal.state === 'stale' ? '资料已更新，请按新资料重新安排。' :
        '已记下目标，正在安排第一步。');
    row.next.textContent = goal.state === 'running' ? '下一步：完成当前工作，再核对结果。' :
      goal.state === 'waiting' ? (goal.due_at ? '下次检查：' + new Date(goal.due_at).toLocaleString() : '有新信息时再继续。') :
      goal.state === 'blocked' ? '先处理未完成的工作，再点“继续”。' : '';
    row.criteria.textContent = '完成条件：' + goal.success_criteria;
    row.unknown.replaceChildren(...[
      ...goal.hypotheses.map(t => '待验证判断：' + t), ...goal.unknowns.map(t => '尚不确定：' + t)
    ].map(t => node('p', t, 'ops-scope')));
    for (const b of row.controls.children) {
      b.hidden = finished.includes(goal.state) ||
        (b.dataset.action === 'resume' ? !['paused', 'blocked', 'waiting'].includes(goal.state) :
          b.dataset.action === 'pause' && goal.state === 'paused');
    }
    row.signal.hidden = finished.includes(goal.state);
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const {goals} = await api('goals');
      $('mind-status').textContent = goals.some(g => ['ready', 'reflecting', 'running'].includes(g.state)) ?
        '正在跟进' : '等待下一步';
      const next = JSON.stringify(goals);
      if (signature === next) return;
      signature = next;
      if (!goals.length) {
        $('mind-goals').textContent = '还没有持续目标。可以交代一件需要分步推进的事。';
        return;
      }
      if (!rows.size) $('mind-goals').replaceChildren();
      for (const goal of [...goals].reverse()) {
        if (!rows.has(goal.id)) {
          const row = build(goal); rows.set(goal.id, row); $('mind-goals').prepend(row.article);
        }
        update(rows.get(goal.id), goal);
      }
      for (const [id, row] of rows) {
        if (!goals.some(g => g.id === id)) { row.article.remove(); rows.delete(id); }
      }
    } finally { loading = false; }
  }
  $('mind-form').onsubmit = e => {
    e.preventDefault();
    act(e.submitter, async () => {
      if (window.automediaCharacterDirty?.()) throw Error('请先保存共同角色，或读取最新角色后开始。');
      const data = {objective: $('mind-objective').value, success_criteria: $('mind-criteria').value,
        allowed_kinds: $('mind-douyin').checked ? ['draft', 'douyin_research'] : ['draft'],
        account_id: $('ops-account').value || null};
      const sig = JSON.stringify(data);
      if (pending?.sig !== sig) pending = {sig, key: crypto.randomUUID()};
      await api('goals', {...data, request_key: pending.key});
      pending = null; $('mind-compose').open = false;
      $('rt-message').textContent = '已记下目标，会根据结果继续安排。'; await refresh();
    });
  };
  setInterval(() => {
    if (!document.hidden && $('app').dataset.studioView === 'operations')
      refresh().catch(e => { $('mind-status').textContent = e.message; });
  }, 3000);
  refresh().catch(e => { $('mind-status').textContent = e.message; });
})();
