from flask import Flask, request
import json
from collections import namedtuple
from multiprocessing import Process, Queue

from eqsql import eq

app = Flask(__name__)
q = None
db_host = None
db_port = None
db_user = None
db_name = None


# def shutdown_server(environ):
#     shutdown = environ.get("werkzeug.server.shutdown")

#     if shutdown is None:
#         raise RuntimeError("Not running the development server.")

#     shutdown()

@app.post('/init')
def flsk_init():
    msg = json.loads(request.json)
    print(msg)
    init_db_params(msg['db_host'], msg['db_port'], msg['db_user'], msg['db_name'])
    return "initialized"


def init_db_params(host, port, user, name):
    global db_host, db_port, db_user, db_name
    db_host = host
    db_port = port
    db_user = user
    db_name = name
    

@app.post('/completed')
def flsk_completed_task():
    msg = json.loads(request.json)
    return completed_task(msg['task_ids'])


def completed_task(task_ids):
    eqsql = eq.init_eqsql(db_host, db_user, db_port, db_name)
    fts = [eq.Future(eqsql, task_id) for task_id in task_ids]
    result = None
    while result is None:
        for ft in fts:
            status, result_str = ft.result(timeout=0.0)
            # TODO: check for ABORT
            if status == eq.ResultStatus.SUCCESS:
                result = [ft.eq_task_id, [status, result_str]]
                break

    eqsql.close()
    return result


@app.post('/update_priorities')
def flsk_update_priorities():
    msg = json.loads(request.json)
    return update_priorities(msg['task_ids'], msg['new_priorities'])


def update_priorities(task_ids, new_priorities):
    eqsql = eq.init_eqsql(db_host, db_user, db_port, db_name)
    fts = [eq.Future(eqsql, task_id) for task_id in task_ids]
    result = eq.update_priority(fts, new_priorities)
    eqsql.close()
    return list(result)


@app.post('/submit_tasks')
def flsk_submit_tasks():
    # print("JSON:", request.json)
    msg = json.loads(request.json)
    exp_id = msg['exp_id']
    task_type = msg['task_type']
    priority = msg['priority']
    return submit_tasks(exp_id, task_type, msg['payload'], priority)


def submit_tasks(exp_id, task_type, payload, priority):
    task_ids = []
    eqsql = eq.init_eqsql(db_host, db_user, db_port, db_name)
    for task in payload:
        _, ft = eqsql.submit_task(exp_id, task_type, task, priority=priority)
        task_ids.append(ft.eq_task_id)

    eqsql.close()
    # print(len(task_ids), flush=True)

    return task_ids


@app.get("/shutdown")
def shutdown():
    q.put(1)
    return 'Server shutting down ...'


@app.get("/ping")
def ping():
    return 'pong'


def start(host, port):
    global q
    q = Queue()
    p = Process(target=app.run, args=(host, port))
    # app.run(host=host, port=port)
    p.start()
    q.get()
    p.terminate()
    p.join()
    return host, port


if __name__ == '__main__':
    start('127.0.0.1', 11218)
