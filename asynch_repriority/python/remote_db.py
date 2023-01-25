from flask import Flask, request
import json

from eqsql import eq

app = Flask(__name__)


def get_eqsql():
    host = 'beboplogin3.lcrc.anl.gov'
    port = 3149
    db_name = 'EQ_SQL'
    user ='collier'
    # user = 'eqsql_test_user'
    # port = 5433
    # db_name = 'eqsql_test_db'

    return eq.init_eqsql(host, user, port, db_name)

@app.post('/completed')
def completed_task():
    eqsql = get_eqsql()
    msg = json.loads(request.json)
    fts = [eq.Future(eqsql, task_id) for task_id in msg['task_ids']]
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
def update_priorities():
    eqsql = get_eqsql()
    msg = json.loads(request.json)
    fts = [eq.Future(eqsql, task_id) for task_id in msg['task_ids']]
    new_priorities = msg['new_priorities']
    result = eq.update_priority(fts, new_priorities)
    eqsql.close()
    return list(result)

@app.post('/submit_tasks')
def submit_task():
    # print("JSON:", request.json)
    eqsql = get_eqsql()
    msg = json.loads(request.json)
    exp_id = msg['exp_id']
    task_type = msg['task_type']
    priority = msg['priority']
    task_ids = []
    for payload in msg['payload']:
        _, ft = eqsql.submit_task(exp_id, task_type, payload, priority=priority)
        task_ids.append(ft.eq_task_id)

    eqsql.close()
    print(len(task_ids), flush=True)

    return task_ids