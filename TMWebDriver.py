import json, threading, time, uuid, queue, socket, requests, traceback, logging
from typing import Any
from simple_websocket_server import WebSocketServer, WebSocket
import bottle
from bottle import request

log = logging.getLogger('TMWebDriver')


class WebDriverExecutionError(RuntimeError):
    """The browser could not complete a requested script."""


class WebDriverTimeoutError(WebDriverExecutionError):
    """The browser did not return a script result before the deadline."""


class WebDriverSessionError(WebDriverExecutionError):
    """The requested browser session is unavailable or was reloaded."""


class Session:
    def __init__(self, session_id, info, client=None):
        self.id = session_id
        self.info = info
        self.connect_at = time.time()
        self.disconnect_at = None
        self.type = info.get('type', 'ws')
        self.ws_client = client if self.type in ('ws', 'ext_ws') else None
        self.http_queue = client if self.type == 'http' else None
    @property
    def url(self): return self.info.get('url', '')
    def is_active(self):
        if self.type == 'http' and self.disconnect_at is None and time.time() - self.connect_at > 60:
            self.mark_disconnected()
        return self.disconnect_at is None
    def reconnect(self, client, info):
        self.info = info
        self.type = info.get('type', 'ws')
        if self.type in ('ws', 'ext_ws'):
            self.ws_client = client
            self.http_queue = None
        elif self.type == 'http':
            self.http_queue = client
        self.connect_at = time.time()
        self.disconnect_at = None
    def mark_disconnected(self):
        if self.disconnect_at is None:
            log.debug(f"Tab disconnected: {self.url} (Session: {self.id})")
            self.disconnect_at = time.time()


class TMWebDriver:  
    def __init__(self, host: str = '127.0.0.1', port: int = 18765):  
        self.host, self.port = host, port
        self.sessions, self.results, self.acks = {}, {}, {}
        self._request_lock = threading.RLock()
        self._pending_requests = set()
        self.default_session_id = None  
        self.latest_session_id = None  
        self.is_remote = socket.socket().connect_ex((host, port+1)) == 0
        if not self.is_remote:  
            self.start_ws_server()  
            self.start_http_server()
        else:
            self.remote = f'http://{self.host}:{self.port+1}/link'

    def start_http_server(self):
        self.app = app = bottle.Bottle()

        @app.route('/api/longpoll', method=['GET', 'POST'])
        def long_poll():
            data = request.json
            session_id = data.get('sessionId')  
            session_info = {'url': data.get('url'), 'title': data.get('title', ''), 'type': 'http'}  
            if session_id not in self.sessions: 
                session = Session(session_id, session_info, queue.Queue())
                log.debug(f"Browser http connected: {session.url} (Session: {session_id})")
                self.sessions[session_id] = session
            session = self.sessions[session_id]
            if session.disconnect_at is not None and session.type != 'http': session.reconnect(queue.Queue(), session_info)
            session.disconnect_at = None
            if session.type == 'http': msgQ = session.http_queue
            else: return json.dumps({"id": "", "ret": "use ws"})
            session.connect_at = start_time = time.time()
            while time.time() - start_time < 5:
                try:
                    msg = msgQ.get(timeout=0.2)
                    try: self._record_ack(json.loads(msg).get('id', ''))
                    except Exception: traceback.print_exc()
                    return msg
                except queue.Empty: continue
            return json.dumps({"id": "", "ret": "next long-poll"})

        @app.route('/api/result', method=['GET','POST'])
        def result():
            data = request.json
            if data.get('type') == 'result':  
                self._record_result(data.get('id'), True, data.get('result'), data.get('newTabs', []))
            elif data.get('type') == 'error':  
                self._record_result(data.get('id'), False, data.get('error'), data.get('newTabs', []))
            return 'ok'

        @app.route('/link', method=['GET','POST'])
        def link():
            data = request.json
            if data.get('cmd') == 'get_all_sessions': return json.dumps({'r': self.get_all_sessions()}, ensure_ascii=False)  
            if data.get('cmd') == 'find_session': 
                url_pattern = data.get('url_pattern', '')
                return json.dumps({'r': self.find_session(url_pattern)}, ensure_ascii=False)
            if data.get('cmd') == 'execute_js':
                session_id = data.get('sessionId')
                code = data.get('code')
                timeout = float(data.get('timeout', 10.0))
                try:
                    result = self.execute_js(code, timeout=timeout, session_id=session_id)
                    log.debug('[remote result] %s', (str(code)[:50] + ' RESULT:' + str(result)[:50]).replace('\n', ' '))
                    return json.dumps({'r': result}, ensure_ascii=False)
                except Exception as e:
                    return json.dumps({'r': {'error': str(e)}}, ensure_ascii=False)
            return 'ok'
        def run():
            from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler
            from socketserver import ThreadingMixIn
            class _T(ThreadingMixIn, WSGIServer): pass
            class _H(WSGIRequestHandler):
                def log_request(self, *a): pass
            make_server(self.host, self.port+1, app, server_class=_T, handler_class=_H).serve_forever()
        http_thread = threading.Thread(target=run, daemon=True)
        http_thread.start()  

    def clean_sessions(self):
        sids = list(self.sessions.keys())
        for sid in sids:
            session = self.sessions[sid]
            if not session.is_active() and time.time() - session.disconnect_at > 600:
                del self.sessions[sid]
    
    def start_ws_server(self) -> None:  
        driver = self  
        class JSExecutor(WebSocket):  
            def handle(self) -> None:  
                try:  
                    data = json.loads(self.data)  
                    if data.get('type') == 'ready':  
                        session_id = data.get('sessionId')  
                        session_info = {'url': data.get('url'), 'title': data.get('title', ''),
                            'connected_at': time.time(), 'type': 'ws'}  
                        driver._register_client(session_id, self, session_info)  
                    elif data.get('type') in ['ext_ready', 'tabs_update']:
                        tabs = data.get('tabs', [])
                        current_tab_ids = {str(tab['id']) for tab in tabs}
                        for sid in list(driver.sessions.keys()):
                            sess = driver.sessions[sid]
                            if sess.type == 'ext_ws' and sid not in current_tab_ids:
                                sess.mark_disconnected()
                        for tab in tabs:
                            session_id = str(tab['id'])
                            session_info = {'url': tab.get('url'), 'title': tab.get('title', ''), 'connected_at': time.time(), 'type': 'ext_ws'}
                            sess = driver.sessions.get(session_id)
                            if sess and sess.is_active(): sess.info = session_info
                            else: driver._register_client(session_id, self, session_info)
                    elif data.get('type') == 'ack':
                        driver._record_ack(data.get('id', ''))
                    elif data.get('type') == 'result':  
                        driver._record_result(data.get('id'), True, data.get('result'), data.get('newTabs', []))
                    elif data.get('type') == 'error':  
                        driver._record_result(data.get('id'), False, data.get('error'), data.get('newTabs', []))
                except Exception as e:  
                    print(f"Error handling message: {e}")  
                    if hasattr(self, 'data'): print(self.data)  
            def connected(self): (f"New connection from {self.address}")  
            def handle_close(self): 
                log.debug(f"WS Connection closed: {self.address}")
                driver._unregister_client(self)  
        
        self.server = WebSocketServer(self.host, self.port, JSExecutor)  
        server_thread = threading.Thread(target=self.server.serve_forever)  
        server_thread.daemon = True  
        server_thread.start()  
        print(f"WebSocket server running on ws://{self.host}:{self.port}")  
    
    def _register_client(self, session_id: str, client: WebSocket, session_info) -> None:  
        is_new_session = session_id not in self.sessions

        if is_new_session:
            session = Session(session_id, session_info, client)
            self.sessions[session_id] = session            
            log.debug(f"New tab connected: {session.url} (Session: {session_id})")
        else:
            session = self.sessions[session_id]
            session.reconnect(client, session_info)
            log.debug(f"Tab reconnected: {session.url} (Session: {session_id})")

        self.latest_session_id = session_id
        if self.default_session_id is None: self.default_session_id = session_id 
    
    def _unregister_client(self, client: WebSocket) -> None:  
        for session in self.sessions.values():
            if session.ws_client == client: session.mark_disconnected()

    def _record_ack(self, exec_id):
        if not exec_id:
            return
        with self._request_lock:
            if exec_id in self._pending_requests:
                self.acks[exec_id] = True

    def _record_result(self, exec_id, success, data, new_tabs=None):
        if not exec_id:
            return
        with self._request_lock:
            if exec_id in self._pending_requests:
                self.results[exec_id] = {
                    'success': success,
                    'data': data,
                    'newTabs': new_tabs or [],
                }

    def _finish_request(self, exec_id):
        with self._request_lock:
            self._pending_requests.discard(exec_id)
            self.acks.pop(exec_id, None)
            return self.results.pop(exec_id, None)
    
    def execute_js(self, code, timeout=15, session_id=None) -> Any:  
        explicit_session = session_id is not None
        if session_id is None:
            session_id = self.default_session_id
        if self.is_remote:
            log.debug('remote_execute_js')
            response = self._remote_cmd({"cmd": "execute_js", "sessionId": session_id, 
                                         "code": code, "timeout": str(timeout)}).get('r', {})
            if response.get('error'):
                raise WebDriverExecutionError(response['error'])
            return response
 
        session = self.sessions.get(session_id)
        if not session or not session.is_active():
            if not explicit_session:
                time.sleep(3)
                session = self.sessions.get(session_id)
            if not session or not session.is_active():
                if explicit_session:
                    raise WebDriverSessionError(f"会话ID {session_id} 未连接")
                alive_sessions = [s for s in self.sessions.values() if s.is_active()]
                if alive_sessions:
                    session = alive_sessions[-1]
                    log.info(f"默认会话 {session_id} 未连接，切换到活动会话: {session.id}")
                    session_id = self.default_session_id = session.id
                if not session or not session.is_active():
                    raise WebDriverSessionError(f"会话ID {session_id} 未连接")

        tp = session.type
        if tp not in ('ws', 'http', 'ext_ws'):
            raise WebDriverSessionError(f"Unsupported session type: {tp}")
        exec_id = str(uuid.uuid4())  
        payload_dict = {'id': exec_id, 'code': code}
        if tp == 'ext_ws': payload_dict['tabId'] = int(session.id)
        payload = json.dumps(payload_dict)

        with self._request_lock:
            self._pending_requests.add(exec_id)
        try:
            if tp in ['ws', 'ext_ws']:
                session.ws_client.send_message(payload)
            elif tp == 'http':
                session.http_queue.put(payload)

            start_time = time.time()
            self.clean_sessions()
            disconnected = acked = False

            while True:
                time.sleep(0.2)
                with self._request_lock:
                    result_ready = exec_id in self.results
                    ack_received = exec_id in self.acks
                if result_ready:
                    break
                if not acked and ack_received:
                    acked = True
                    start_time = time.time()
                if tp in ['ws', 'ext_ws']:
                    if not session.is_active():
                        disconnected = True
                    if disconnected and session.is_active():
                        raise WebDriverSessionError(f"Session {session_id} reloaded before returning a result")
                if time.time() - start_time > timeout:
                    delivery = 'delivered but no result' if acked else 'not acknowledged'
                    if disconnected:
                        delivery = 'session disconnected or reloaded'
                    raise WebDriverTimeoutError(
                        f"Session {session_id} timed out after {timeout}s ({delivery})"
                    )
            result = self._finish_request(exec_id)
        finally:
            self._finish_request(exec_id)

        if not result['success']:
            raise WebDriverExecutionError(str(result['data']))
        rr = {'data': result['data']}
        newtabs = result.get('newTabs', []); [x.pop('ts', None) for x in newtabs]
        if newtabs: rr['newTabs'] = newtabs
        return rr
    
    def _remote_cmd(self, cmd):
        try: return requests.post(self.remote, headers={"Content-Type": "application/json"}, json=cmd, timeout=30).json()
        except (ConnectionError, requests.exceptions.ConnectionError):
            raise ConnectionError("TMWebDriver master未运行，看tmwebdriver_sop启动master")

    def get_all_sessions(self):  
        if self.is_remote:
            return self._remote_cmd({"cmd": "get_all_sessions"}).get('r', [])
        return [{'id': session.id, **session.info} for session in self.sessions.values()
                if session.is_active()]  

    def get_session_dict(self):
        return {session['id']: session['url'] for session in self.get_all_sessions()}
        
    def find_session(self, url_pattern: str):
        if url_pattern == '': 
            session = self.sessions.get(self.latest_session_id)
            return [(session.id, session.info)] if session else []
        matching_sessions = []  
        for session in self.sessions.values():
            if not session.is_active(): continue
            if 'url' in session.info and url_pattern in session.info['url']:  
                matching_sessions.append((session.id, session.info))  
        return matching_sessions

    def set_session(self, url_pattern: str) -> bool:  
        if self.is_remote:
            matched = self._remote_cmd({"cmd": "find_session", "url_pattern": url_pattern}).get('r', [])
        else:
            matched = self.find_session(url_pattern)
        if not matched: return print(f"警告: 未找到URL包含 '{url_pattern}' 的会话")  
        if len(matched) > 1: print(f"警告: 找到多个URL包含 '{url_pattern}' 的会话，选择第一个")  
        self.default_session_id, info = matched[0]
        print(f"成功设置默认会话: {self.default_session_id}: {info['url']}")  
        return self.default_session_id  
    
    def jump(self, url, timeout=10): self.execute_js(f"window.location.href='{url}'", timeout=timeout)
    
if __name__ == "__main__":
    driver = TMWebDriver(host='127.0.0.1', port=18765)
