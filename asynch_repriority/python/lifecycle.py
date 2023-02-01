import threading
import time
import shlex
import select
import socketserver
import paramiko
import requests
import getpass
import funcx
from collections import namedtuple
import task_queues
from typing import Dict
from datetime import datetime

from proxystore.store import register_store
from proxystore.store.globus import GlobusEndpoints
from proxystore.store.globus import GlobusStore


class ForwardServer(socketserver.ThreadingMixIn, socketserver.TCPServer):

    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            chan = self.ssh_transport.open_channel(
                "direct-tcpip",
                (self.chain_host, self.chain_port),
                self.request.getpeername(),
            )
        except Exception as e:
            print(
                "Incoming request to %s:%d failed: %s"
                % (self.chain_host, self.chain_port, repr(e))
            )
            return
        if chan is None:
            print(
                "Incoming request to %s:%d was rejected by the SSH server."
                % (self.chain_host, self.chain_port)
            )
            return

        # print('Connected!  Tunnel open -> {} -> {} -> {}'.format(self.request.getpeername(),
        #         chan.getpeername(),
        #         (self.chain_host, self.chain_port),
        #     ), flush=True
        # )
        while True:
            r, w, x = select.select([self.request, chan], [], [])
            if self.request in r:
                data = self.request.recv(1024)
                if len(data) == 0:
                    break
                chan.send(data)
            if chan in r:
                data = chan.recv(1024)
                if len(data) == 0:
                    break
                self.request.send(data)

        # peername = self.request.getpeername()
        chan.close()
        self.request.close()
        # print("Tunnel closed from %r" % (peername,))


def _launch_worker_pool(exp_id, launch_script, cfg_params):
    import tempfile
    import os
    import subprocess as sp

    fd, fname = tempfile.mkstemp(text=True)
    print(f'CFG File: {fname}')
    with os.fdopen(fd, 'w') as f:
        for k, v in cfg_params.items():
            if k.startswith('CFG'):
                f.write(f'{k}={v}\n')
    try:
        cwd = os.path.dirname(launch_script)
        result = sp.run([launch_script, str(exp_id), fname], stdout=sp.PIPE, stderr=sp.STDOUT, cwd=cwd,
                        check=True)
        return (True, result.stdout.decode('utf-8'))
    except sp.CalledProcessError as ex:
        return (False, ex.stdout.decode('utf-8'))


class LocalPool:
    def __init__(self, db, type, params):
        self.db = db
        self.type = type
        self.cfg = {}
        for k, v in params.items():
            if k.startswith('CFG_'):
                self.cfg[k] = v
        self.cfg['CFG_DB_HOST'] = db.db_host
        self.cfg['CFG_DB_PORT'] = db.db_port
        self.cfg['CFG_DB_USER'] = db.db_user
        self.cfg['CFG_DB_NAME'] = db.db_name
        self.cfg['CFG_TASK_TYPE'] = self.type
        self.start_pool_script = params['start_pool_script']

    def start(self, exp_id):
        self.t = threading.Thread(target=_launch_worker_pool, args=(exp_id, self.start_pool_script, self.cfg), daemon=True)
        self.t.start()

    def shutdown(self):
        self.t.join()


class RemotePool:

    def __init__(self, fx_executor, db, type, params):
        self.fx = fx_executor
        self.db = db
        self.type = type
        self.cfg = {}
        for k, v in params.items():
            if k.startswith('CFG_'):
                self.cfg[k] = v
        self.cfg['CFG_DB_HOST'] = db.db_host
        self.cfg['CFG_DB_PORT'] = db.db_port
        self.cfg['CFG_DB_USER'] = db.db_user
        self.cfg['CFG_DB_NAME'] = db.db_name
        self.cfg['CFG_TASK_TYPE'] = self.type
        self.start_pool_script = params['start_pool_script']

    def start(self, exp_id):
        # TODO update with psij etc.
        fx_result = self.fx.submit(_launch_worker_pool, exp_id, self.start_pool_script, self.cfg)
        success, output = fx_result.result()
        msg = 'Launch Succeeded' if success else 'Launch Failed'
        print(f'{msg}\n{output}')
        return success, output

    def shutdown(self):
        # TODO update with psij etc.
        pass


def _get_hostname():
    import socket
    return socket.getfqdn()


def _find_task_for_pool(pool_name, params):
    # TODO: Error checking - pool not found etc.
    for task in params['tasks']:
        for task_pool in task['pools']:
            if task_pool == pool_name:
                return (task['type'], task['db'])


def _get_pool_exp_id(exp_id: str):
    dt = datetime.now()
    # getting the timestamp
    ts = datetime.timestamp(dt)
    return f'{exp_id}_{ts}'


def initialize_worker_pools(exp_id, pool_names, fx_executors, dbs, params):
    # TODO: Error checking -- pool doesn't start, key errors etc.
    pools = {}
    for name in pool_names:
        task_type, db_name = _find_task_for_pool(name, params)
        db = dbs[db_name]
        pool_params = params['pools'][name]
        if pool_params['type'] == 'funcx':
            fx = fx_executors[pool_params['fx_endpoint']]
            pool = RemotePool(fx, db, task_type, pool_params)
        elif pool_params['type'] == 'local':
            pool = LocalPool(db, task_type, pool_params)

        pool.start(_get_pool_exp_id(exp_id))
        pools[name] = pool

    return pools


def _exec_cmd(cmd, env):
    # for funcx
    import subprocess as sp
    import os

    for k in ['PATH', 'LD_LIBRARY_PATH']:
        if k in env and k in os.environ:
            path = os.environ[k]
            env[k] = f'{env[k]}:{path}'
    try:
        result = sp.run(cmd, stdout=sp.PIPE, stderr=sp.STDOUT, env=env, check=True)
        return (True, result.stdout.decode('utf-8'))
    except sp.CalledProcessError as ex:
        return (False, ex.stdout.decode('utf-8'))


class LocalDB:

    def __init__(self, params: Dict):
        self.db_user = params['db_user']
        self.db_name = params['db_name']
        self.db_port = params['db_port']
        self.db_host = params['db_host']

    def start(self):
        pass

    def shutdown(self):
        pass


class RemoteDB:

    def __init__(self, fx, params: Dict):
        self.fx = fx
        self.db_user = params['db_user']
        self.db_port = params['db_port']
        self.db_name = params['db_name']
        fx_result = fx.submit(_get_hostname)
        self.db_host = fx_result.result()
        self.db_data = params['db_data']

        self.env = params['env']
        self.start_cmd = params['start_cmd']
        self.stop_cmd = params['stop_cmd']

    def _exec_db_cmd(self, str_cmd):
        raw_cmd = shlex.split(str_cmd)
        cmd = []
        substs = {'db_port': self.db_port, 'db_data': self.db_data, 'db_name': self.db_name}
        for word in raw_cmd:
            # TODO use re
            if word.find('${') != -1 and word.find('}') != -1:
                start = word.find('${')
                end = word.find('}')
                key = word[start + 2: end]
                new_word = f'{word[:start]}{str(substs[key])}{word[end+1:]}'
                cmd.append(new_word)
            else:
                cmd.append(word)

        print(f'Runing DB CMD: {cmd}')
        fx_result = self.fx.submit(_exec_cmd, cmd, self.env)
        success, output = fx_result.result()
        msg = 'DB CMD Succeeded' if success else 'DB CMD Failed'
        print(f'{msg}\n{output}')

    def start(self):
        self._exec_db_cmd(self.start_cmd)

    def shutdown(self):
        self._exec_db_cmd(self.stop_cmd)


def initialize_dbs(db_names, fx_executors, params):
    # TODO: error checking -- db doesn't start, db name not found etc.
    dbs = {}
    for name in db_names:
        db_params = params['dbs'][name]
        if db_params['type'] == 'funcx':
            fx = fx_executors[db_params['fx_endpoint']]
            db = RemoteDB(fx, db_params)

        elif db_params['type'] == 'local':
            db = LocalDB(db_params)

        db.start()
        dbs[name] = db

    return dbs

# def _get_port():
#     s = socket.socket()
#     s.bind(("", 0))
#     port = s.getsockname()[1]
#     s.close()
#     return port


class Tunnel:

    def __init__(self, transport, port, forward_server, tunnel_thread):
        self.transport = transport
        self.port = port
        self.forward_server = forward_server
        self.tunnel_thread = tunnel_thread

    def close(self):
        self.transport.close()
        self.forward_server.shutdown()
        self.tunnel_thread.join()


def start_tunnel(host, port, local_port, destination, config_key, ssh_config):
    config = paramiko.SSHConfig.from_path(ssh_config)
    host_config = config.lookup(config_key)

    transport = paramiko.Transport(destination)
    transport.start_client()

    # TODO better error handling when authentication fails
    # Need to stop the remote db etc.
    try:
        key = paramiko.RSAKey.from_private_key_file(host_config['identityfile'][0])
    except paramiko.ssh_exception.PasswordRequiredException:
        for _ in range(4):
            try:
                passwd = getpass.getpass('Key Password: ')
                key = paramiko.RSAKey.from_private_key_file(host_config['identityfile'][0], password=passwd)
                break
            except Exception:
                pass

    transport.auth_publickey(host_config['user'], key)

    class SubHander(Handler):
        chain_host = host
        chain_port = port
        ssh_transport = transport

    fs = ForwardServer(("", local_port), SubHander, bind_and_activate=False)
    fs.allow_reuse_address = True
    fs.server_bind()
    fs.server_activate()
    th = threading.Thread(target=fs.serve_forever)
    th.start()

    return Tunnel(transport, port, fs, th)


def start_flask(host, port):
    import sys
    sys.path.append('/lcrc/project/EMEWS/bebop/repos/eqsql_examples/asynch_repriority/python')
    sys.path.append('/lcrc/project/EMEWS/bebop/repos/EQ-SQL/python')
    import remote_db
    remote_db.start(host, port)


def find_active_elements(params: Dict):
    endpoints = set()
    queues = params['task_queues']
    # queues is a dictionary with name as key
    ref_dbs = set()
    for q in queues.values():
        if q['type'] == 'funcx':
            endpoints.add(q['fx_endpoint'])
        ref_dbs.add(q['db'])

    # check dbs for endpoints
    dbs = params['dbs']
    for rdb in ref_dbs:
        db = dbs[rdb]
        if db['type'] == 'funcx':
            endpoints.add(db['fx_endpoint'])

    ref_pools = set()
    pools = params['pools']
    # check tasks for pools that may need funcx
    for task in params['tasks']:
        for task_pool in task['pools']:
            pool = pools[task_pool]
            ref_pools.add(task_pool)
            if pool['type'] == 'funcx':
                endpoints.add(pool['fx_endpoint'])

    AE = namedtuple('active_elements', 'fx_endpoints, dbs, pools')
    return AE(list(endpoints), list(ref_dbs), list(ref_pools))


def initialize_fx_endpoints(endpoint_names, params: Dict) -> Dict:
    # TODO: error checking - name doesn't exist, endpoint doesn't start, etc.
    fx_endpoints = params['fx_endpoints']
    return {name: funcx.FuncXExecutor(endpoint_id=fx_endpoints[name]) for name in endpoint_names}


def _start_queue_server(fx, db, params):
    host = params['host']
    port = params['port']
    hostname = db.db_host

    tunnel_p = params['tunnel']
    api_host = f"http://{host}:{tunnel_p['port']}"
    tunnel = start_tunnel(host, port, tunnel_p['port'], hostname, tunnel_p['config_key'],
                          tunnel_p['ssh_config'])

    fx.submit(start_flask, host, port)
    started = False
    for _ in range(20):
        try:
            time.sleep(1)
            api_url = f'{api_host}/ping'
            requests.get(api_url)
            started = True
            break
        except Exception:
            pass

    return (started, api_host, tunnel)


def initialize_task_queues(fx_executors, dbs, params):
    taskqs = {}
    for task_queue_name, task_queue_params in params['task_queues'].items():
        db_name = task_queue_params['db']
        db = dbs[db_name]
        task_queue_type = task_queue_params['type']
        if task_queue_type == 'local':
            task_queue = task_queues.LocalTaskQueue(db.db_host, db.db_user, db.db_port, db.db_name)
        elif task_queue_type == 'funcx':
            fx_endpoint = task_queue_params['fx_endpoint']
            fx = fx_executors[fx_endpoint]
            db = dbs[task_queue_params['db']]
            task_queue = task_queues.FXTaskQueue(fx, db)
        elif task_queue_type == 'queue_server':
            # Start Tunnel
            # Start Flask Server - initializing db_host etc.
            fx = fx_executors[task_queue_params['fx_endpoint']]
            db = dbs[task_queue_params['db']]
            started, api_host, tunnel = _start_queue_server(fx, db, task_queue_params)
            if started:
                task_queue = task_queues.RESTfulTaskQueue(api_host, tunnel, db)
            else:
                # TODO: better error checking
                raise ValueError()

        taskqs[task_queue_name] = task_queue

    return taskqs


def initialize_proxystore(params: Dict):
    globus_config = params['proxystore']['globus_config']
    endpoints = GlobusEndpoints.from_dict(globus_config)
    store = GlobusStore('globus', endpoints=endpoints)
    register_store(store)
