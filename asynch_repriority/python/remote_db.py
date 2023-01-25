from flask import Flask, jsonify, request
import json

from eqsql import eq

app = Flask(__name__)


def get_eqsql():
    host = 'localhost'
    user = 'eqsql_test_user'
    port = 5433
    db_name = 'eqsql_test_db'

    return eq.init_eqsql(host, user, port, db_name)



@app.post("/submit_task")
def submit_task():
    # print("JSON:", request.json)
    eqsql = get_eqsql()
    msg = json.loads(request.json)
    exp_id = msg['exp_id']
    task_type = msg['task_type']
    task_ids = []
    for payload in msg['payloads']:
        _, ft = eqsql.submit_task(exp_id, task_type, payload)
        task_ids.append(ft.eq_task_id)

    eqsql.close()
    # print(task_ids, flush=True)

    return task_ids