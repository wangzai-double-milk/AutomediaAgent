(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {preparing: '正在写作与核对', ready: '母稿已核对，尚未发布', queued: '等待执行',
    publishing: '准备发布', submitting: '已开始提交，等待回读', verified: '已独立回读确认',
    unknown: '结果未知，禁止重发', blocked: '受阻，请查看任务记录', stale: '资料或素材已变化', cancelled: '已停止'};
  const node = (tag, text, cls) => {
    const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls; return el;
  };
  let pending, signature = '', busy = false;
  async function api(path, data) {
    const r = await fetch('/content/' + path, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const result = await r.json(); if (!r.ok) throw Error(result.error || '操作未完成'); return result;
  }
  async function act(button, work) {
    button.disabled = true;
    try { await work(); } catch (e) { $('content-notice').textContent = e.message; }
    finally { button.disabled = false; }
  }
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const data = await api('state'), ready = data.publish_ready;
      $('content-status').textContent = !data.binding_available ? '尚无已绑定平台账号。' :
        ready?.ok ? '发帖间隔已满足；执行前仍会检查页面身份。' :
          '发布暂缓：' + (ready?.reason || '请核对账号状态') +
          (ready?.retry_after ? ' 最早 ' + new Date(ready.retry_after).toLocaleString() : '');
      const next = JSON.stringify(data.contents);
      if (signature === next) return; signature = next;
      $('content-list').replaceChildren(...data.contents.map(item => {
        const card = node('article', undefined, 'mind-goal');
        card.dataset.content = item.id;
        card.append(node('h4', item.payload?.title || item.brief.objective),
          node('p', labels[item.state] || item.state, 'ops-scope'));
        if (item.payload) card.append(node('p', item.payload.desc, 'runtime-output'));
        const controls = node('div', undefined, 'mind-controls');
        if (item.state === 'ready') {
          const publish = node('button', '发布 · 仅自己可见', 'btn'); publish.type = 'button';
          publish.onclick = () => act(publish, async () => {
            const result = await api('publish', {id: item.id, fingerprint: item.fingerprint});
            $('content-notice').textContent = result.status === 'deferred' || result.status === 'restricted' ?
              result.reason : '已安排一次发布，结果以独立回读为准。';
            await refresh();
          });
          controls.append(publish);
        }
        if (item.state === 'blocked' && item.receipt?.status === 'error' &&
            item.receipt?.msg === 'chrome_foreground_not_verified') {
          const retry = node('button', '浏览器恢复后重试 · 仅自己可见', 'btn');
          retry.type = 'button';
          const requestKey = crypto.randomUUID();
          retry.onclick = () => act(retry, async () => {
            const result = await api('retry', {id: item.id, fingerprint: item.fingerprint, request_key: requestKey});
            $('content-notice').textContent = result.status === 'deferred' || result.status === 'restricted' ?
              result.reason : '已确认没有提交登记，重新安排同一篇母稿。';
            await refresh();
          });
          controls.append(retry);
        }
        if (['preparing', 'ready', 'queued', 'publishing'].includes(item.state)) {
          const cancel = node('button', '停止', 'btn'); cancel.type = 'button';
          cancel.onclick = () => act(cancel, async () => { await api('cancel', {id: item.id}); await refresh(); });
          controls.append(cancel);
        }
        const details = node('details', undefined, 'ops-details');
        details.append(node('summary', '素材、来源与回读'),
          node('pre', JSON.stringify({assets: item.brief.assets, sources: item.brief.sources,
            fingerprint: item.fingerprint, receipt: item.receipt}, null, 2), 'runtime-evidence'));
        card.append(controls, details); return card;
      }));
      if (!data.contents.length) $('content-list').textContent = '还没有内容母稿。';
    } finally { busy = false; }
  }
  $('content-form').onsubmit = e => {
    e.preventDefault();
    act(e.submitter, async () => {
      if (window.automediaCharacterDirty?.()) throw Error('请先保存共同角色。');
      const data = {objective: $('content-objective').value, account_id: $('ops-account').value,
        assets: $('content-images').value.split('\n').map(s => s.trim()).filter(Boolean)
          .map(path => ({path, source: $('content-source').value})),
        sources: [{content: $('content-facts').value, source: $('content-reference').value}]};
      const sig = JSON.stringify(data);
      if (pending?.sig !== sig) pending = {sig, key: crypto.randomUUID()};
      await api('create', {...data, request_key: pending.key}); pending = null;
      $('content-create').open = false;
      $('content-notice').textContent = '已安排写作与独立核对。'; await refresh();
    });
  };
  setInterval(() => {
    if (!document.hidden && $('content-panel').open) refresh().catch(e => { $('content-notice').textContent = e.message; });
  }, 3000);
  refresh().catch(e => { $('content-notice').textContent = e.message; });
})();
