// Fixed DOM reader: no cookies, private APIs, framework stores or account writes.
// Called as a function with a bounded result limit by tools/douyin.py.
(limit) => {
  const allowed = location.protocol === 'https:' && location.hostname === 'www.douyin.com'
    && (!location.port || location.port==='443')
    && /^\/(?:$|jingxuan\/?$|search\/[^/]+\/?$|video\/\d+\/?$|user\/[A-Za-z0-9_-]+\/?$)/.test(location.pathname);
  if (!allowed) return {ok:false,status:'wrong_page',url:location.origin + location.pathname};
  const shown = e => {
    if (!e) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>2 && r.height>2 && s.display!=='none' && s.visibility!=='hidden';
  };
  const area = e => {
    const r=e.getBoundingClientRect();
    return Math.max(0,Math.min(r.right,innerWidth)-Math.max(r.left,0))
      * Math.max(0,Math.min(r.bottom,innerHeight)-Math.max(r.top,0));
  };
  const txt = (e,n=1000) => (e?.innerText || e?.textContent || '').trim().slice(0,n);
  const text = (document.body?.innerText || '').slice(0,16000);
  const idlePause = /长时间无操作[，,、\s]*已暂停播放/.test(text) && text.includes('继续播放');
  const challenges = [...document.querySelectorAll(
    '[id*="captcha"],[class*="captcha"],[class*="verify-wrap"],[role="dialog"]'
  )].filter(shown);
  const challenge = challenges.find(e => /验证码|安全验证|拖动滑块|完成验证|验证身份/.test(txt(e)));
  const restricted = /访问过于频繁|操作过于频繁|账号被封禁|账号已被封禁/.test(text);
  const loginModal = [...document.querySelectorAll(
    '[role="dialog"],[data-e2e="login-modal"],[class*="login-modal"],[class*="loginModal"]'
  )].filter(shown).find(e => /扫码登录|手机号登录|验证码登录/.test(txt(e)));
  const loginWall = /(?:^|\n)登录后即可搜索更多精彩视频(?:\n|$)/.test(text)
    || /(?:^|\n)登录后即可(?:观看|查看)更多(?:视频|内容)(?:\n|$)/.test(text);
  const status = challenge ? 'verification_required' : restricted ? 'restricted'
    : loginModal || loginWall ? 'login_required' : 'ok';
  const videoId = value => {
    try {
      const u = new URL(value, location.href);
      if (u.protocol!=='https:' || u.hostname!=='www.douyin.com' || u.username || u.password) return null;
      return u.pathname.match(/^\/video\/(\d{5,25})\/?$/)?.[1]
        || (/^\d{5,25}$/.test(u.searchParams.get('modal_id')||'') ? u.searchParams.get('modal_id') : null);
    } catch (_) { return null; }
  };
  const links = [...document.querySelectorAll('a[href]')].filter(shown).slice(0,1000);
  const anchors = links.filter(a => videoId(a.href));
  const cleanTitle = value => value.split('\n').map(s=>s.trim()).filter(s=>s
    && !/^(?:合集|客户端|下载客户端|分享|抖音|推荐|登录)$/.test(s)
    && !/^@/.test(s) && !/^\d+(?:\.\d+)?[万亿]?$/.test(s)
    && !/^(?:\d{1,2}:){1,2}\d{2}$/.test(s)
    && !/^(?:\d+[秒分钟小时天周月年]+前|昨天|前天)$/.test(s)).join('\n').slice(0,300);
  const rows = new Map();
  for (const a of anchors) {
    if (a.closest('header,nav') || /^(客户端|下载客户端|分享|登录)$/.test(txt(a,100))) continue;
    const id=videoId(a.href);
    if (rows.has(id)) continue;
    let card=a;
    // Never combine adjacent video cards into one author's evidence.
    for (let parent=a.parentElement, depth=0; parent && depth<5; parent=parent.parentElement,depth++) {
      const ids = new Set([...parent.querySelectorAll('a[href]')].map(x=>videoId(x.href)).filter(Boolean));
      if (ids.size>1 || parent===document.body) break;
      if (txt(parent).length>1800) break;
      card=parent;
      if (parent.matches('li,article,[data-e2e*="search-card"],[data-e2e*="video-card"]')) break;
    }
    const author=[...card.querySelectorAll('a[href*="/user/"]')].find(shown);
    const titleNode=card.querySelector('[data-e2e*="video-desc"],[data-e2e*="video-title"]');
    const title=cleanTitle(txt(titleNode,600) || (a.getAttribute('title')||'') || txt(card,1000));
    if (!title) continue;
    const byline=txt(card,1000).split('\n').find(s=>s.trim().startsWith('@'));
    const stat = kind => {
      const e=card.querySelector(`[data-e2e*="${kind}"]`);
      return e && shown(e) ? txt(e,80) || e.getAttribute('aria-label') : null;
    };
    rows.set(id,{video_id:id,url:a.href,title:title||null,
      author:author&&txt(author,100)?{name:txt(author,100),url:author.href,source:'profile_link'}
        :byline?{name:byline.trim().slice(1,101),url:null,source:'card_text'}:null,
      statistics:{likes:stat('like'),comments:stat('comment'),favorites:stat('collect'),shares:stat('share')},
      text:txt(card,1000),source:'visible_dom',in_viewport:area(card)>0});
    if (rows.size>=limit) break;
  }
  const videoCandidates=[...document.querySelectorAll('video')].filter(shown);
  const exposed = v => {
    const r=v.getBoundingClientRect();
    const x=(Math.max(0,r.left)+Math.min(innerWidth,r.right))/2;
    const y=(Math.max(0,r.top)+Math.min(innerHeight,r.bottom))/2;
    const top=document.elementFromPoint(x,y), root=v.closest('[data-e2e="feed-item"]');
    return top && (top===v || v.contains(top) || (root && root.contains(top)));
  };
  // Preloaded/live siblings can have large rectangles but sit behind the active slide.
  const videos=videoCandidates.filter(v=>area(v)>0 && exposed(v)).sort((a,b)=>area(b)-area(a));
  const video=videos[0];
  let activeRoot=video?.closest('[data-e2e="feed-active-video"],[data-e2e="feed-item"],.swiper-slide-active');
  // Player wrappers vary. Walk only ancestors containing this one video, never the whole feed.
  const playerAncestors=[];
  for(let e=video?.parentElement,depth=0;e && e!==document.body && depth<10;e=e.parentElement,depth++){
    if(e.querySelectorAll('video').length!==1)break;
    playerAncestors.push(e);
  }
  if(!activeRoot){
    activeRoot=playerAncestors.find(e=>{
      const ids=new Set([...e.querySelectorAll('a[href]')].map(a=>videoId(a.href)).filter(Boolean));
      return ids.size===1 || /^\d{5,25}$/.test(e.getAttribute('data-aweme-id')||e.getAttribute('data-id')||'');
    }) || video?.parentElement?.parentElement;
  }
  const activeIds=activeRoot?[...new Set([...activeRoot.querySelectorAll('a[href]')].map(a=>videoId(a.href)).filter(Boolean))]:[];
  const attrIds=[...new Set([video,...playerAncestors].filter(Boolean).flatMap(e=>
    ['data-aweme-id','data-video-id'].map(k=>e.getAttribute(k))
      .filter(v=>/^\d{5,25}$/.test(v||''))))];
  const id=videoId(location.href) || (activeIds.length===1?activeIds[0]:null)
    || (attrIds.length===1?attrIds[0]:null);
  const desc=activeRoot?.querySelector('[data-e2e*="video-desc"],[data-e2e*="video-title"]');
  const heading=document.querySelector('h1');
  const detailTitle=txt(desc,500) || (heading&&shown(heading)?txt(heading,500):null);
  let media=null;
  if (video) {
    const src=video.currentSrc||video.src||'';
    let stream=null;
    try {
      const u=new URL(src);
      // Only a real currentSrc on the visible player, never unrelated network cache.
      if (u.protocol==='https:' && !u.username && !u.password &&
          (u.hostname.endsWith('.douyinvod.com') || u.hostname==='douyinvod.com')) stream=src;
    } catch (_) {}
    // This is a player continuity key, NOT a canonical aweme ID or a share URL.
    // Ignore expiring CDN query parameters; require a concrete title as a second signal.
    const playerKey=stream&&detailTitle
      ? JSON.stringify([new URL(stream).pathname,cleanTitle(detailTitle)]) : null;
    media={video_id:id,source_url:id?`https://www.douyin.com/video/${id}`:null,
      player_key:playerKey,identity_scope:id?'canonical_video':playerKey?'visible_player':'unknown',
      content_kind:video.closest('[data-e2e="feed-live"]')?'live_preview':'video',
      stream_url:stream,source_type:src.startsWith('blob:')?'blob':stream?'https':'unavailable',
      duration:Number.isFinite(video.duration)?video.duration:null,current_time:video.currentTime,
      paused:video.paused,ready_state:video.readyState,in_viewport:true,
      title:detailTitle,card_text:txt(activeRoot,800),understood:false};
    media.identity_evidence={
      source:id?(videoId(location.href)?'page_url':activeIds.length===1?'player_link':'player_attribute'):'unavailable',
      ancestors:playerAncestors.map(e=>({tag:e.tagName,e2e:e.getAttribute('data-e2e'),
        aweme_id:e.getAttribute('data-aweme-id'),video_id:e.getAttribute('data-video-id'),
        video_links:[...new Set([...e.querySelectorAll('a[href]')].map(a=>videoId(a.href)).filter(Boolean))].slice(0,4)}))
    };
  }
  const commentRoot=document.querySelector('[data-e2e="comment-list"],[data-e2e="comment-list-container"]');
  const comments=commentRoot&&shown(commentRoot)
    ? [...commentRoot.querySelectorAll('[data-e2e="comment-item"]')].filter(shown).slice(0,limit).map(e=>({
      text:txt(e,700),source:'visible_comment_row',comment_id:null
    })) : [];
  const accountLink=links.find(a=>/^\/user\//.test(new URL(a.href,location.href).pathname)
    && /^(我|我的|个人主页)$/.test(txt(a,30)));
  const navigation=Object.fromEntries(links.filter(a=>/^(推荐|精选)$/.test(txt(a,10)))
    .map(a=>[txt(a,10)==='推荐'?'recommend':'featured',a.href]));
  const empty=/暂无搜索结果|没有搜索结果|未找到相关内容|搜索结果为空/.test(text);
  return {ok:status==='ok',status,url:location.href,page_title:document.title,ready_state:document.readyState,
    blocker:status==='ok'?null:challenge?txt(challenge,160):loginModal?txt(loginModal,160)
      :loginWall?'登录后才能继续搜索或观看':'页面显示访问限制',
    login_state:loginModal||loginWall?'required':accountLink?'observed_profile_link':'unknown',
    account:accountLink?{profile_url:accountLink.href}:null,
    items:[...rows.values()],media,comments,empty_explicit:empty,navigation,
    player_candidates:videoCandidates.slice(0,8).map(v=>({
      area:Math.round(area(v)),exposed:!!exposed(v),paused:v.paused,ready_state:v.readyState,
      ancestors:(()=>{const out=[];for(let e=v.parentElement,n=0;e&&e!==document.body&&n<6;e=e.parentElement,n++)
        out.push({tag:e.tagName,e2e:e.getAttribute('data-e2e'),id:e.id||null,
          video_id:e.getAttribute('data-video-id'),aweme_id:e.getAttribute('data-aweme-id')});return out;})()
    })),
    navigation_controls:[...document.querySelectorAll('[data-e2e],[aria-label],[title]')]
      .filter(e=>shown(e)&&area(e)>0&&/next|switch|下一条|下一个/i.test(
        [e.getAttribute('data-e2e'),e.getAttribute('aria-label'),e.getAttribute('title')].join(' ')))
      .slice(0,16).map(e=>({tag:e.tagName,e2e:e.getAttribute('data-e2e'),
        label:e.getAttribute('aria-label'),title:e.getAttribute('title'),text:txt(e,60)})),
    focus:{document:document.hasFocus(),active_tag:document.activeElement?.tagName},idle_pause:idlePause,
    visible_text_excerpt:status==='ok'?text.slice(0,1600):'页面要求验证、登录或提示访问限制',
    capabilities:{browse:true,search:true,read:true,next:true,account_write:false},
    scope:'当前网页已加载DOM；不代表全量搜索、完整评论或已理解视频',
    collected_at:new Date().toISOString()};
}
