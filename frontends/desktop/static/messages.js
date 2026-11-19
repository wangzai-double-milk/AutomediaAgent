(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const status = {draft: '本地草稿', approved: '已授权，尚未发送', sending: '发送中',
    unknown: '结果未知，需核对', sent: '已回读确认', cancelled: '已取消', stale: '会话或资料已变化'};
  let contacts = [], signature = '', pendingImport = null, refreshing = null;
  const rows = new Map();
  const selectedKey = 'automedia.message.contact';
  function rememberContact() {
    try { localStorage.setItem(selectedKey, $('msg-contact').value); } catch (_) {}
  }
  const node = (tag, content, cls) => {
    const n = document.createElement(tag);
    if (content !== undefined) n.textContent = content;
    if (cls) n.className = cls;
    return n;
  };
  async function api(path, data) {
    const response = await fetch('/messages/' + path, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '操作未完成');
    return result;
  }
  async function act(button, work) {
    button.disabled = true;
    try { await work(); } catch (e) { $('msg-notice').textContent = e.message; }
    finally { button.disabled = false; }
  }
  async function conversation() {
    const id = $('msg-contact').value;
    if (!id) { $('msg-history').textContent = '还没有导入会话。'; return; }
    const result = await api('conversation?contact_id=' + encodeURIComponent(id));
    if ($('msg-contact').value !== id) return;
    $('msg-history').replaceChildren(...result.messages.map(m =>
      node('p', `${m.direction === 'inbound' ? '对方' : '己方'} · ${m.source === 'adapter' ? '通道观察' : '手动导入'}\n${m.content}`,
        'runtime-output')));
    if (!result.messages.length) $('msg-history').textContent = '还没有导入消息。';
  }
  function build(item) {
    const article = node('article', undefined, 'mind-goal');
    article.dataset.outbox = item.id;
    const title = node('h4'), state = node('p', undefined, 'ops-scope');
    const label = node('label', '回复正文'), input = node('textarea');
    input.rows = 4; input.maxLength = 12000; input.id = 'outbox-' + item.id; label.htmlFor = input.id;
    const controls = node('div', undefined, 'mind-controls');
    const save = node('button', '保存修改', 'btn'), cancel = node('button', '取消草稿', 'btn');
    save.type = cancel.type = 'button';
    const row = {article, title, state, input, save, cancel, item, dirty: false};
    input.addEventListener('input', () => { row.dirty = true; });
    save.onclick = () => act(save, async () => {
      await api('edit', {id: row.item.id, expected_revision: row.item.revision,
        content: input.value, commitments: row.item.commitments});
      row.dirty = false; $('msg-notice').textContent = '已保存本地修改。'; await refresh();
    });
    cancel.onclick = () => act(cancel, async () => {
      await api('cancel', {id: row.item.id, expected_revision: row.item.revision});
      row.dirty = false; await refresh();
    });
    const evidence = node('details', undefined, 'ops-details');
    const detail = node('div');
    evidence.append(node('summary', '发送与授权记录'), detail);
    evidence.addEventListener('toggle', async () => {
      if (!evidence.open) return;
      try {
        const result = await api('events?id=' + encodeURIComponent(item.id));
        detail.replaceChildren(...result.events.map(e => node('p',
          `${e.kind} · ${new Date(e.created_at).toLocaleString()}`, 'ops-scope')));
        if (!result.events.length) detail.textContent = '尚无发送或授权记录。';
      } catch (e) { detail.textContent = e.message; }
    });
    controls.append(save, cancel);
    article.append(title, state, label, input, controls, evidence);
    return row;
  }
  async function refresh() {
    if (refreshing) { await refreshing; return refresh(); }
    refreshing = (async () => {
      const data = await api('state');
      $('msg-status').textContent = '微信、QQ尚未连接 · 可手动导入并准备回复';
      if (Object.values(data.environment?.platforms || {}).some(p =>
        p.status === 'not_found_in_checked_locations')) {
        $('msg-status').textContent = '微信、QQ尚未连接 · 应用目录中有客户端未找到';
      }
      const next = JSON.stringify(data);
      if (signature === next) return;
      signature = next; contacts = data.contacts;
      let selected = $('msg-contact').value;
      if (!selected) { try { selected = localStorage.getItem(selectedKey); } catch (_) {} }
      $('msg-contact').replaceChildren(...contacts.map(c => {
        const o = node('option', `${c.label} · ${{wechat: '微信', qq: 'QQ', sandbox: '隔离验证'}[c.platform] || c.platform}`);
        o.value = c.id; o.title = `${c.owner_id} → ${c.peer_id}`; return o;
      }));
      if (contacts.some(c => c.id === selected)) $('msg-contact').value = selected;
      $('msg-import').hidden = !contacts.length;
      if (!rows.size) $('msg-outbox').replaceChildren();
      for (const item of [...data.outbox].reverse()) {
        if (!rows.has(item.id)) {
          const row = build(item); rows.set(item.id, row); $('msg-outbox').prepend(row.article);
        }
        const row = rows.get(item.id);
        row.title.textContent = contacts.find(c => c.id === item.contact_id)?.label || '回复草稿';
        // Preserve both unsaved text and its original revision; a changed server version conflicts.
        if (!row.dirty) { row.item = item; row.input.value = item.content; }
        row.state.textContent = (status[item.state] || item.state) +
          (row.dirty && row.item.revision !== item.revision ? ' · 本地修改尚未保存，请先核对新状态' : '');
        row.input.readOnly = !['draft', 'approved'].includes(item.state);
        row.save.hidden = row.input.readOnly;
        row.cancel.hidden = !['draft', 'approved', 'stale'].includes(item.state);
      }
      for (const [id, row] of rows) if (!data.outbox.some(o => o.id === id) && !row.dirty) {
        row.article.remove(); rows.delete(id);
      }
      $('msg-empty').hidden = !!data.outbox.length;
      $('msg-promises').replaceChildren(...data.commitments.map(c => node('p',
        `${c.content} · ${new Date(c.due_at).toLocaleString()} · ${
          {open: '待跟进', following_up: '已安排本地跟进', stale: '资料变化，需重新核对',
            controller_reported_done: '控制者报告已履约'}[c.state] || c.state}`, 'runtime-output')));
      await conversation();
    })();
    try { await refreshing; } finally { refreshing = null; }
  }
  $('msg-contact').onchange = () => {
    rememberContact();
    conversation().catch(e => { $('msg-notice').textContent = e.message; });
  };
  $('msg-contact-form').onsubmit = e => {
    e.preventDefault();
    act(e.submitter, async () => {
      $('msg-import').inert = true;
      try {
        const contact = await api('contacts', {platform: $('msg-platform').value,
          owner_id: $('msg-owner').value, peer_id: $('msg-peer').value, label: $('msg-label').value});
        await refresh(); $('msg-contact').value = contact.id; rememberContact(); await conversation();
        $('msg-contact-create').open = false;
        $('msg-notice').textContent = '已保存本地联系人备注，尚未核验平台身份。';
      } finally { $('msg-import').inert = false; }
    });
  };
  $('msg-import-form').onsubmit = e => {
    e.preventDefault();
    act(e.submitter, async () => {
      const data = {contact_id: $('msg-contact').value, content: $('msg-content').value};
      const sig = JSON.stringify(data);
      if (pendingImport?.sig !== sig) pendingImport = {sig, key: crypto.randomUUID()};
      await api('import', {...data, request_key: pendingImport.key});
      pendingImport = null; $('msg-content').value = '';
      $('msg-notice').textContent = '已导入原文，保留为外部消息。'; await refresh();
    });
  };
  $('msg-reply-form').onsubmit = e => {
    e.preventDefault();
    act(e.submitter, async () => {
      if (window.automediaCharacterDirty?.()) throw Error('请先保存共同角色。');
      const job = await api('reply', {contact_id: $('msg-contact').value,
        instruction: $('msg-instruction').value, account_id: $('ops-account').value || null});
      $('msg-notice').textContent = ['cancelled', 'stale', 'failed', 'blocked'].includes(job.state) ?
        '之前的草拟未完成。请按新信息调整回复要求后重新安排。' : '已安排回复草稿，完成后显示在下方。';
    });
  };
  setInterval(() => {
    if (!document.hidden && $('messages-panel').open) refresh().catch(e => { $('msg-notice').textContent = e.message; });
  }, 3000);
  refresh().catch(e => { $('msg-notice').textContent = e.message; });
})();
