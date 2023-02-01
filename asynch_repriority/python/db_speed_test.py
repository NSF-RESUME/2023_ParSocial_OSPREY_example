import numpy as np
import json
from datetime import datetime
import funcx
import json
import asynch_repriority.python.queues as queues
import sys
import time

from eqsql import eq
import asynch_repriority.python.lifecycle as lifecycle

def run():
    search_space_size = 1000
    dim = 4
    sampled_space = np.random.uniform(size=(search_space_size, dim), low=-32.768, high=32.768)
    payloads = []
    for sample in sampled_space:
        payload = json.dumps({'x': list(sample), 'mean_rt': 2, 'std_rt': 1})
        payloads.append(payload)

    def start_flask():
        import sys
        sys.path.append('/lcrc/project/EMEWS/bebop/repos/eqsql_examples/asynch_repriority/python')
        sys.path.append('/lcrc/project/EMEWS/bebop/repos/EQ-SQL/python')
        import remote_db
        return remote_db.start('127.0.0.1', 11218)
       
    tunnel = lifecycle.start_tunnel('127.0.0.1', 11218, 11219, 'Bebop', '/home/nick/.ssh/config')

    bebop_ep = 'd526418b-8920-4bc9-a9a0-3c97e1a10d3b'
    # with funcx.FuncXExecutor(endpoint_id=bebop_ep) as fx:
    fx = funcx.FuncXExecutor(endpoint_id=bebop_ep)
    ft = fx.submit(start_flask)

    api_host = "http://127.0.0.1:11219"
    for i in range(20):
        try:
            queues.ping(api_host)
            print("PING!")
        except:
            print("NONTHING")
            time.sleep(1)
    
    start = datetime.now()
    # fts = remote.submit_remote_tasks(api_host, 'test_1', 0, payloads, 0)
    # # print(fts)
    # print(fts[0].eq_task_id)
    # end = datetime.now()
    # print(end - start)

    queues.remote_shutdown(api_host)
    print(ft.result())
    fx.shutdown()
    tunnel.close()


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