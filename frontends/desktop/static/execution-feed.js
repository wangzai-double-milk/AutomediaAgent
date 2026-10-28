// A presentation layer for the actual agent_loop text protocol.
// Parsing never executes tools or infers platform success from a model sentence.
(() => {
  'use strict';
  const roundPattern = /^\s*(?:\*\*)?(?:LLM Running \(Turn (\d+)\)|Turn (\d+))\s*\.{3}(?:\*\*)?\s*$/;
  const toolPattern = /^\s*🛠\uFE0F?\s*(?:Tool:\s*)?`?([\w.-]+)`?\s*(.*)$/;
  const labels = {
    update_working_checkpoint: '保存任务进度', xhs_task_init: '建立研究任务',
    xhs_check_login: '核对登录状态', xhs_search_feeds: '搜索小红书',
    xhs_get_feed_detail: '深读笔记', xhs_read_feed: '深读笔记',
    xhs_get_comments: '读取评论', xhs_task_status: '查看任务进度',
    file_read: '读取操作资料', file_write: '整理任务文稿',
    file_append: '更新任务文稿', web_scan: '查看浏览器页面',
    web_execute_js: '读取或操作网页', web_desktop: '查看桌面页面',
    web_visual_click: '操作页面', web_visual_fill: '填写页面',
    code_run: '执行本地检查', analyze_media: '分析图片与视频',
    xhs_publish: '准备发布', xhs_publish_content: '准备发布',
    xhs_manage: '核对作品', xhs_test_account: '核对测试账号',
    xhs_task_write_report: '更新研究报告', search_anywhere: '搜索资料',
  };
  const memories = new Map();

  function plain(text, limit = 120) {
    const value = String(text || '').replace(/<think>[\s\S]*?<\/think>/gi, '')
      .replace(/<\/?(?:summary|think)>/gi, '').replace(/[*#`]/g, '')
      .replace(/\bSOP\b/g, '操作流程').replace(/\bCDP\b/g, '浏览器连接')
      .replace(/\s+/g, ' ').trim();
    return value.length > limit ? `${value.slice(0, limit)}…` : value;
  }
  function parse(raw) {
    const rounds = [];
    let round = null, tool = null, target = 'narration', fence = null;
    const ensureRound = (number = null) => {
      if (!round) {
        round = { number, key: `round-${number ?? 'start'}-${rounds.length}`, narration: [], tools: [] };
        rounds.push(round);
      }
      return round;
    };
    const push = (line) => {
      if (!round) return;
      if (tool && target !== 'narration') tool[target].push(line);
      else round.narration.push(line);
    };
    for (const line of String(raw || '').split(/\r?\n/)) {
      if (fence) {
        if (new RegExp(`^\\s*\`{${fence.length}}\\s*$`).test(line)) {
          if (fence.target === 'args') target = 'result';
          else if (fence.target === 'result' && tool) tool.resultClosed = true;
          else if (fence.target === 'narration') push(line);
          fence = null;
        } else push(line);
        continue;
      }
      const nextRound = line.match(roundPattern);
      if (nextRound) {
        if (tool) tool.resultClosed = true;
        round = null;
        ensureRound(Number(nextRound[1] || nextRound[2]));
        tool = null;
        target = 'narration';
        continue;
      }
      const nextTool = line.match(toolPattern);
      if (nextTool) {
        if (tool) tool.resultClosed = true;
        ensureRound();
        tool = { key: `${round.key}-tool-${round.tools.length}`, name: nextTool[1], args: [], result: [] };
        round.tools.push(tool);
        const rest = nextTool[2].replace(/^\s*📥\s*args:\s*/i, '').trim();
        if (rest) tool.args.push(rest);
        target = 'args';
        continue;
      }
      const opening = line.match(/^\s*(`{3,})([^`]*)$/);
      if (opening) {
        fence = { length: opening[1].length, target };
        if (target === 'narration') push(line);
        continue;
      }
      if (tool && target === 'args' && /^\s*\[(?:Info|Action|Status|Error|Warning|Stdout|Stderr)\]/i.test(line)) target = 'result';
      push(line);
      if (tool && target === 'args' && tool.args.length) {
        try { JSON.parse(tool.args.join('\n')); target = 'result'; } catch (_) {}
      }
    }
    if (!rounds.length) return null;
    for (const item of rounds) {
      item.narration = item.narration.join('\n').trim();
      for (const action of item.tools) {
        action.args = action.args.join('\n').trim();
        action.result = action.result.join('\n').trim();
        try { action.arguments = JSON.parse(action.args); } catch (_) { action.arguments = {}; }
      }
    }
    return { rounds, tools: rounds.flatMap((item) => item.tools) };
  }
  function describe(tool) {
    const args = tool.arguments || {};
    let title = labels[tool.name] || '执行一项操作';
    let context = args.keyword || args.query || args.action || '';
    if (tool.name === 'file_read') {
      const path = String(args.path || '');
      if (/SKILL\.md$/i.test(path)) title = '读取操作指南';
      context = /xhs_search/.test(path) ? '小红书搜索' : /explore_mode/.test(path) ? '网页探索'
        : /research_mode/.test(path) ? '趋势研究' : path.split('/').pop() || '';
    }
    if (tool.name === 'code_run') {
      const script = String(args.script || '');
      if (/osascript|9222|Google Chrome/.test(script)) title = '检查浏览器连接';
      context = args.type === 'bash' ? '本地检查' : '';
    }
    if (tool.name === 'web_scan') context = args.tabs_only ? '标签页' : args.text_only ? '页面正文' : '当前页面';
    return { title, context: plain(context, 48) };
  }
  function warning(tool) {
    const recorded = tool.name === 'file_read'
      ? (tool.result || '').split('\n').slice(0, 2).join('\n')
      : tool.result || '';
    const result = recorded.split('\n').filter((line) =>
      !/^\[Action\]\s*(?:Running|Reading file|正在运行|读取文件)/i.test(line)
    ).join('\n');
    // Only evidence in returned output qualifies. Words inside args, script
    // bodies or a read guide never become an execution error.
    return /\[(?:Error|Warning)\]/i.test(result)
      || /['"](?:status|ok)['"]\s*:\s*(?:['"](?:error|failed|restricted|deferred)['"]|false)/i.test(result)
      || /^\[Status\].*(?:❌|Exit Code:\s*[1-9])/im.test(result)
      || (['web_scan', 'code_run', 'xhs_search_feeds', 'web_desktop'].includes(tool.name)
        && /^(?:\[Info\]\s*)?(?:没有可用的浏览器标签页|no CDP on \d+|desktop[\w_]*timeout)/im.test(result));
  }
  function toolState(tool, latest, live) {
    if (warning(tool)) return ['attention', '需关注'];
    if (latest && live && tool.result && !tool.resultClosed) return ['running', '回读中'];
    if (tool.result) return ['returned', '已返回'];
    return latest && live ? ['running', '执行中'] : ['unknown', '未回读'];
  }
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text != null) item.textContent = text;
    return item;
  }
  function setText(element, value) {
    if (element.textContent !== value) element.textContent = value;
  }
  function remember(feed, key, open) {
    feed.memory.set(key, open);
  }
  function drawer(feed, key, className, label) {
    const item = node('details', className);
    item.dataset.executionKey = key;
    item.open = feed.memory.get(key) === true;
    const summary = node('summary', '', label);
    item.append(summary);
    item.addEventListener('toggle', () => {
      remember(feed, key, item.open);
      if (item.open && item._refresh) item._refresh();
    });
    item._summary = summary;
    return item;
  }
  function rawPanel(label) {
    const panel = node('section', 'execution-raw-panel');
    const head = node('div', 'execution-raw-head');
    const title = node('span', '', label);
    const copy = node('button', '', '复制');
    copy.type = 'button';
    copy.setAttribute('aria-label', `复制${label}`);
    const pre = node('pre');
    const code = node('code');
    pre.append(code);
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(code.textContent); copy.textContent = '已复制'; }
      catch (_) { copy.textContent = '请手动复制'; }
    });
    head.append(title, copy);
    panel.append(head, pre);
    panel.update = (text) => { setText(code, text); };
    return panel;
  }
  function buildAction(feed, key) {
    const item = drawer(feed, key, 'execution-action', '');
    const mark = node('span', 'execution-action-mark');
    const title = node('strong');
    const context = node('span', 'execution-action-context');
    const status = node('span', 'execution-action-status');
    item._summary.replaceChildren(mark, title, context, status);
    item._refresh = () => {
      if (!item._panels) {
        const body = node('div', 'execution-action-body');
        const args = rawPanel('操作参数');
        const result = rawPanel('原始回读');
        body.append(args, result);
        item.append(body);
        item._panels = { args, result };
      }
      item._panels.args.update(item._tool.args || '未记录参数');
      item._panels.result.update(item._tool.result || '这项操作还没有回读记录。');
    };
    item.update = (tool, latest, live) => {
      item._tool = tool;
      const description = describe(tool);
      const [kind, label] = toolState(tool, latest, live);
      item.dataset.state = kind;
      setText(mark, kind === 'attention' ? '!' : kind === 'running' ? '◌' : '·');
      setText(title, description.title);
      setText(context, description.context);
      setText(status, label);
      if (item.open) item._refresh();
    };
    return item;
  }
  function buildRound(feed, key) {
    const item = drawer(feed, key, 'execution-round', '');
    const title = node('strong');
    const count = node('span', 'execution-round-count');
    const badge = node('span', 'execution-round-badge');
    item._summary.replaceChildren(title, count, badge);
    const actions = new Map();
    item._refresh = () => {
      if (!item._body) {
        item._body = node('div', 'execution-round-body');
        item._note = node('p', 'execution-round-note');
        item._body.append(item._note);
        item.append(item._body);
      }
      setText(item._note, plain(item._round.narration, 300) || '操作记录');
      for (const tool of item._round.tools) {
        let action = actions.get(tool.key);
        if (!action) { action = buildAction(feed, tool.key); actions.set(tool.key, action); }
        action.update(tool, tool === feed.parsed.tools.at(-1), feed.options.live);
        if (action.parentElement !== item._body) item._body.append(action);
      }
    };
    item.update = (round) => {
      item._round = round;
      const actions = [...new Set(round.tools.map((tool) => describe(tool).title))].join(' · ');
      const label = actions || plain(round.narration.split('\n').find((line) => line.trim()), 76) || '整理下一步';
      setText(title, label);
      setText(count, round.number != null ? `第 ${round.number} 轮` : '开始');
      const warnings = round.tools.filter(warning).length;
      setText(badge, warnings ? `${warnings} 项需关注` : `${round.tools.length} 项动作`);
      badge.classList.toggle('attention', warnings > 0);
      if (item.open) item._refresh();
    };
    return item;
  }
  function buildFeed(container, key) {
    if (!memories.has(key)) memories.set(key, new Map());
    if (memories.size > 80) memories.delete(memories.keys().next().value);
    const feed = { memory: memories.get(key), rows: new Map(), options: {}, parsed: null };
    const root = node('section', 'execution-feed');
    root._feed = feed;
    root.setAttribute('aria-label', '任务执行进展');
    const header = node('div', 'execution-feed-header');
    const icon = node('span', 'execution-feed-icon', '↗');
    const heading = node('span', 'execution-feed-kicker', '执行进展');
    const status = node('span', 'execution-feed-status');
    header.append(icon, heading, status);
    const headline = node('strong', 'execution-feed-title');
    const caption = node('p', 'execution-feed-caption');
    const metrics = node('div', 'execution-feed-metrics');
    const stats = node('span');
    const attention = node('button', 'execution-attention-button');
    attention.type = 'button';
    attention.addEventListener('click', () => {
      history.open = true;
      for (const round of feed.parsed.rounds) {
        if (!round.tools.some(warning)) continue;
        const row = feed.rows.get(round.key);
        if (row.parentElement === earlierList) earlier.open = true;
        row.open = true;
        row._refresh();
      }
      const first = list.querySelector('.execution-action[data-state="attention"] > summary');
      first?.focus();
      first?.scrollIntoView({ block: 'nearest' });
    });
    metrics.append(stats, attention);
    const drawers = node('div', 'execution-drawers');
    const history = drawer(feed, 'history', 'execution-history', '查看执行过程');
    const list = node('div', 'execution-round-list');
    const earlier = drawer(feed, 'earlier', 'execution-earlier', '');
    const earlierList = node('div', 'execution-earlier-list');
    earlier.append(earlierList);
    list.append(earlier);
    history.append(list);
    const raw = drawer(feed, 'raw', 'execution-raw', '原始日志');
    raw._refresh = () => {
      if (!raw._panel) { raw._panel = rawPanel('完整日志'); raw.append(raw._panel); }
      raw._panel.update(feed.raw);
    };
    drawers.append(history, raw);
    root.append(header, headline, caption, metrics, drawers);
    const answer = node('div', 'execution-final-answer md');
    container.replaceChildren(root, answer);
    container.closest('.msg-assistant')?.classList.add('execution-message');
    Object.assign(feed, { key, root, header, headline, caption, metrics, stats, attention, history, list, earlier, earlierList, rawDrawer: raw, answer, status });
    container._executionFeed = feed;
    return feed;
  }
  function render(container, raw, options = {}) {
    const parsed = parse(raw);
    if (!parsed) {
      delete container._executionFeed;
      container.closest('.msg-assistant')?.classList.remove('execution-message');
      return false;
    }
    const key = options.key || 'execution';
    let feed = container._executionFeed;
    if (!feed || feed.key !== key || !container.contains(feed.root)) feed = buildFeed(container, key);
    if (feed.raw === raw && feed.options.live === options.live && feed.options.status === options.status) return true;
    Object.assign(feed, { raw, parsed, options });
    const last = parsed.rounds.at(-1);
    const currentTool = last.tools.at(-1);
    const alerts = parsed.tools.filter(warning).length;
    const state = options.status === 'cancelled' ? '已停止' : options.status === 'error' ? '需要处理' : options.live ? '进行中' : '运行结束';
    feed.root.dataset.state = options.live ? 'running' : options.status || 'ended';
    setText(feed.status, state);
    setText(feed.headline, options.live ? currentTool ? describe(currentTool).title : '正在整理下一步' : '这次运行的过程');
    setText(feed.caption, plain(last.narration, 160) || '参数和原始回读保存在执行详情中。');
    setText(feed.stats, `${parsed.rounds.length} 轮记录 · ${parsed.tools.length} 项动作`);
    setText(feed.attention, `${alerts} 项需关注 ↗`);
    feed.attention.hidden = !alerts;
    feed.metrics.classList.toggle('has-attention', alerts > 0);
    const cut = Math.max(0, parsed.rounds.length - 4);
    feed.earlier.hidden = !cut;
    setText(feed.earlier._summary, `更早的 ${cut} 轮记录`);
    for (let index = 0; index < parsed.rounds.length; index++) {
      const round = parsed.rounds[index];
      let row = feed.rows.get(round.key);
      if (!row) { row = buildRound(feed, round.key); feed.rows.set(round.key, row); }
      row.update(round);
      const parent = index < cut ? feed.earlierList : feed.list;
      if (row.parentElement !== parent) parent.append(row);
    }
    if (feed.rawDrawer.open) feed.rawDrawer._refresh();
    // A final response without tools remains fully visible, beyond the folded
    // execution history. Ending the loop does not claim the task succeeded.
    const finalText = !options.live && !last.tools.length ? last.narration : '';
    if (feed.answer._text !== finalText) {
      feed.answer._text = finalText;
      feed.answer.innerHTML = finalText && options.markdown ? options.markdown(finalText) : '';
      if (finalText && !options.markdown) feed.answer.textContent = finalText;
    }
    feed.answer.hidden = !finalText;
    return true;
  }
  function prepareSearch(container, query) {
    const needle = String(query || '').toLocaleLowerCase();
    if (!needle) return;
    for (const root of container.querySelectorAll('.execution-feed')) {
      const feed = root._feed;
      if (!feed || !feed.raw.toLocaleLowerCase().includes(needle)
          || root.textContent.toLocaleLowerCase().includes(needle)) continue;
      feed.rawDrawer.open = true;
      feed.rawDrawer._refresh();
    }
  }
  window.AutoMediaExecution = { parse, render, prepareSearch };
})();
