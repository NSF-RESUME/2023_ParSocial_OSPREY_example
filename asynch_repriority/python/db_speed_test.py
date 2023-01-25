import numpy as np
import json
from datetime import datetime
import requests
import json

from eqsql import eq

def run():
    search_space_size = 250
    dim = 4
    sampled_space = np.random.uniform(size=(search_space_size, dim), low=-32.768, high=32.768)
    start = datetime.now()
    msg = {'exp_id': 'test_1', 'task_type': 0}
    payloads = []
    msg['payloads'] = payloads
    for sample in sampled_space:
        payload = json.dumps({'x': list(sample), 'mean_rt': 2, 'std_rt': 1})
        payloads.append(payload)
    
    api_url = "http://127.0.0.1:5000/submit_task"
    response = requests.post(api_url, json=json.dumps(msg))
    print(response.json())
    end = datetime.now()
    print(end - start)


    # task_eq = eq.init_eqsql('localhost', 'eqsql_test_user', 5433, 'eqsql_test_db')
    # task_eq = eq.init_eqsql('localhost', 'collier', 11219, 'EQ_SQL')
    
    # exp_id = 'speed_test'
    # task_type = 0
    # mean_rt = 2
    # std_rt = 1
    # database = {}
    # print("SUBMITTING", flush=True)
    # start = datetime.now()
    # payloads = []
    # for sample in sampled_space:
    #     payload = json.dumps({'x': list(sample), 'mean_rt': mean_rt, 'std_rt': std_rt})
    #     payloads.append(payload)

    #     _, ft = task_eq.submit_task(exp_id, task_type, payload)
    #     database[ft.eq_task_id] = [ft, sample, None]
    #     end = datetime.now()
    # print("SUBMITTED", flush=False)
    # print(end - start)

    # start = datetime.now()
    # with task_eq.db.conn:
    #         with task_eq.db.conn.cursor() as cur:
    #             for _ in range(search_space_size):
    #                         cur.execute("select count(*) from eq_tasks;") # nextval('emews_id_generator');")
    #                         rs = cur.fetchone()
    #                         eq_task_id = rs[0]
    # end = datetime.now()
    # print(end - start)

    # task_eq.close()
if __name__ == '__main__':
    run()