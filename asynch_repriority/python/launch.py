import threading
import funcx
import shlex
import socket
import select
import socketserver
import paramiko
import threading
import getpass


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

        print(
            "Connected!  Tunnel open %r -> %r -> %r"
            % (
                self.request.getpeername(),
                chan.getpeername(),
                (self.chain_host, self.chain_port),
            )
        )
        while True:
            r, w, x = select.select([self.request, chan], [], [])
            if self.request in r:
                data = self.request.recv(4096)
                if len(data) == 0:
                    break
                chan.send(data)
            if chan in r:
                data = chan.recv(4096)
                if len(data) == 0:
                    break
                self.request.send(data)

        peername = self.request.getpeername()
        chan.close()
        self.request.close()
        print("Tunnel closed from %r" % (peername,))

def _launch_worker_pool(exp_id, pool_params):
    import tempfile
    import os
    import subprocess as sp

    fd, fname = tempfile.mkstemp(text=True)
    with os.fdopen(fd, 'w') as f:
        for k, v in pool_params.items():
            if k.startswith('CFG'):
                f.write(f'{k}={v}\n')
    
    launch_script = pool_params['start_pool_script']

    try:
        cwd = os.path.dirname(launch_script)
        result = sp.run([launch_script, str(exp_id), fname], stdout=sp.PIPE, stderr=sp.STDOUT, cwd=cwd, 
                         check=True)
        return (True, result.stdout.decode('utf-8'))
    except sp.CalledProcessError as ex:
        return (False, ex.stdout.decode('utf-8'))
   
def _find_pool_by_name(pool_name, params):
    pools = params['pools']
    for p in pools:
        if p['name'] == pool_name:
            return p
    
    raise ValueError(f"Unknown Pool: {pool_name}")


def _get_hostname():
    import socket
    return socket.getfqdn()

def launch_worker_pool(exp_id, task_type, name, pool_params):
    print(f'Launching {name} Worker Pool ...')
    cpy = pool_params.copy()
    cpy['CFG_TASK_TYPE'] = task_type
    pool_type = cpy['type']
    if pool_type == 'local':
        # use thread locally so call returns
        t = threading.Thread(target=_launch_worker_pool, args=(exp_id, cpy))
        t.start()
        return (cpy, t)
    elif pool_type == 'funcx':
        ep = cpy['fx_endpoint']
        with funcx.FuncXExecutor(endpoint_id=ep) as fx:
            fx_result = fx.submit(_get_hostname)
            hostname = fx_result.result()
            cpy['CFG_DB_HOST'] = hostname
            fx_result = fx.submit(_launch_worker_pool, exp_id, cpy)
            success, output = fx_result.result()
            msg = 'Launch Succeeded' if success else 'Launch Failed'
            print(f'{msg}\n{output}')
        return (cpy, )


def stop_worker_pool(pool_info):
    if pool_info[0]['type'] == 'local':
        pool_info[1].join()

def stop_worker_pools(pools_data):
    for pool_data in pools_data:
        stop_worker_pool(pool_data)

def launch_worker_pools(exp_id: str, params):
    task_types = params['task_types']
    tasks = params['tasks']
    pool_data = []
    for task in tasks:
        task_type_id = task['type']
        task_type = task_types[task_type_id]
        for pool_name in task['pools']:
            pool = _find_pool_by_name(pool_name, params)
            pool_data.append(launch_worker_pool(exp_id, task_type, pool_name, pool))

    return pool_data

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

def exec_db_cmd(db_cfg, cmd_key):
    str_cmd = db_cfg[cmd_key]
    raw_cmd = shlex.split(str_cmd)
    cmd = []
    for word in raw_cmd:
        # TODO use re
        if word.find('${') != -1 and word.find('}') != -1:
            start = word.find('${')
            end = word.find('}')
            key = word[start + 2: end]
            new_word = f'{word[:start]}{str(db_cfg[key])}{word[end+1:]}'
            cmd.append(new_word)
        else:
            cmd.append(word)

    print(cmd)
    if db_cfg['type'] == 'funcx':
        ep = db_cfg['fx_endpoint']
        with funcx.FuncXExecutor(endpoint_id=ep) as fx:
            fx_result = fx.submit(_exec_cmd, cmd, db_cfg['env'])
            success, output = fx_result.result()
            msg = 'DB Launch Succeeded' if success else 'DB Launch Failed'
            print(f'{msg}\n{output}')

            # TODO better error checking
            if success and 'tunnel' in db_cfg:
                fx_result = fx.submit(_get_hostname)
                hostname = fx_result.result()

                return hostname


def stop_dbs(params):
    for db in params['dbs']:
        if db['type'] != 'local':
            exec_db_cmd(db, 'stop_cmd')

            if 'tunnel' in db:
                db['tunnel']['transport'].close()
                db['tunnel']['forward_server'].shutdown()


def launch_dbs(params):
    for db in params['dbs']:
        if db['type'] != 'local':
            hostname = exec_db_cmd(db, 'start_cmd')
            if 'tunnel' in db:
                start_db_tunnel(db, hostname)


def _get_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def start_db_tunnel(db_params, db_host):
    agent = paramiko.Agent()
    keys = agent.get_keys()

    tunnel_params = db_params['tunnel']
    host = tunnel_params['host']
    config = paramiko.SSHConfig.from_path(tunnel_params['ssh_config'])
    host_config = config.lookup(host)

    transport = paramiko.Transport(host_config['hostname'])
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
            except:
                pass
        
    transport.auth_publickey(host_config['user'], key)

    class SubHander(Handler):
        chain_host = db_host
        chain_port = db_params['db_port']
        ssh_transport = transport

    tunnel_port = _get_port()
    fs = ForwardServer(("", tunnel_port), SubHander, bind_and_activate=False)
    fs.allow_reuse_address = True
    fs.server_bind()
    fs.server_activate()
    th = threading.Thread(target=fs.serve_forever)
    th.start()
    
    tunnel_params['transport'] = transport
    tunnel_params['port'] = tunnel_port
    tunnel_params['forward_server'] = fs
