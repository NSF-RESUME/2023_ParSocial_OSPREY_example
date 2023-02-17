import requests
from typing import List
import json

from eqsql import eq


class RemoteTask:

    def __init__(self, api_host, task_id, task_type):
        self.api_host = api_host
        self.eq_task_id = task_id
        self.task_type = task_type
        self._result = None
        self.priority = 0

    def result(self):
        return self._result


class LocalTaskQueue:

    def __init__(self, host, user, port, name):
        self.task_eq = eq.init_eqsql(host, user, port, name)

    def submit_tasks(self, exp_id, eq_type, payload, priority: int = 0,
                     tag: str = None):
        fts = []
        for task in payload:
            _, ft = self.task_eq.submit_task(exp_id, eq_type, task, priority, tag)
            fts.append(ft)
        return fts

    def update_priorities(self, tasks: List, new_priorities: List[int]):
        eq.update_priority(tasks, new_priorities)

    def pop_completed(self, tasks: List, n=1):
        if n == 1:
            return [eq.pop_completed(tasks)]
        else:
            return [ft for ft in eq.as_completed(tasks, pop=True, n=n)]

    def shutdown(self):
        self.task_eq.close()


class RESTfulTaskQueue:

    def __init__(self, api_host, tunnel, db):
        self.api_host = api_host
        self.tunnel = tunnel

        msg = {}
        msg['db_host'] = db.db_host
        msg['db_port'] = db.db_port
        msg['db_user'] = db.db_user
        msg['db_name'] = db.db_name
        api_url = f'{self.api_host}/init'
        # print('posting: ', json.dumps(msg))
        requests.post(api_url, json=json.dumps(msg))

    def submit_tasks(self, exp_id, eq_type, payload, priority: int = 0,
                     tag: str = None) -> List[RemoteTask]:

        msg = {'exp_id': exp_id, 'task_type': eq_type, 'payload': payload,
               'priority': priority}

        api_url = f'{self.api_host}/submit_tasks'
        response = requests.post(api_url, json=json.dumps(msg))
        task_ids = response.json()
        return [RemoteTask(self.api_host, task_id, eq_type) for task_id in task_ids]

    def pop_completed(self, tasks: List[RemoteTask], n=1) -> List[RemoteTask]:
        api_url = f'{self.api_host}/completed'
        msg = {'task_ids': [ft.eq_task_id for ft in tasks], 'n': n}
        response = requests.post(api_url, json=json.dumps(msg))
        # task_id: result - we do the map here because json keys are strings
        completed_task_results = completed_task_results = {x[0]: x[1] for x in response.json()}
        # print(completed_task_results)
        completed_tasks = []
        uncompleted_tasks = []
        for ft in tasks:
            if ft.eq_task_id in completed_task_results:
                ft._result = completed_task_results[ft.eq_task_id]
                completed_tasks.append(ft)
            else:
                uncompleted_tasks.append(ft)
        tasks.clear()
        tasks.extend(uncompleted_tasks)
        return completed_tasks

    def update_priorities(self, tasks: List[RemoteTask], new_priorities: List[int]):
        for i, task in enumerate(tasks):
            task.priority = new_priorities[i]
        api_url = f'{self.api_host}/update_priorities'
        msg = {'task_ids': [ft.eq_task_id for ft in tasks], 'new_priorities': new_priorities}
        requests.post(api_url, json=json.dumps(msg))

    def ping(self):
        api_url = f'{self.api_host}/ping'
        requests.get(api_url)

    def shutdown(self):
        api_url = f'{self.api_host}/shutdown'
        requests.get(api_url)
        self.tunnel.close()


class FXTaskQueue:

    def __init__(self, fx, db):
        self.fx = fx

        def __init_db(host, port, user, name):
            import sys
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/eqsql_examples/asynch_repriority/python')
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/EQ-SQL/python')
            import remote_db
            return remote_db.init_db_params(host, port, user, name)

        self.fx.submit(__init_db, db.db_host, db.db_port, db.db_user, db.db_name)

    def submit_tasks(self, exp_id, eq_type, payload, priority: int = 0,
                     tag: str = None) -> List[RemoteTask]:

        def _submit_tasks(exp_id, task_type, payload, priority):
            import sys
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/eqsql_examples/asynch_repriority/python')
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/EQ-SQL/python')
            import remote_db
            return remote_db.submit_tasks(exp_id, task_type, payload, priority)

        ft = self.fx.submit(_submit_tasks, exp_id, eq_type, payload, priority)
        return [RemoteTask('', task_id, eq_type) for task_id in ft.result()]

    def pop_completed(self, tasks: List[RemoteTask], n=1) -> List[RemoteTask]:
        def _pop_completed(task_ids, n):
            import sys
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/eqsql_examples/asynch_repriority/python')
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/EQ-SQL/python')
            import remote_db
            return remote_db.completed_task(task_ids, n)

        task_ids = [ft.eq_task_id for ft in tasks]
        ft = self.fx.submit(_pop_completed, task_ids, n)
        # {task_id: result}
        completed_task_results = {x[0]: x[1] for x in ft.result()}
        # print(completed_task_results)
        completed_tasks = []
        uncompleted_tasks = []
        for ft in tasks:
            if ft.eq_task_id in completed_task_results:
                # result is [status, actual result]
                ft._result = completed_task_results[ft.eq_task_id]
                completed_tasks.append(ft)
            else:
                uncompleted_tasks.append(ft)
        tasks.clear()
        tasks.extend(uncompleted_tasks)
        return completed_tasks

    def update_priorities(self, tasks: List[RemoteTask], new_priorities: List[int]):
        def _update_priorities(task_ids, new_priorities):
            import sys
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/eqsql_examples/asynch_repriority/python')
            sys.path.append('/lcrc/project/EMEWS/bebop/repos/EQ-SQL/python')
            import remote_db
            return remote_db.update_priorities(task_ids, new_priorities)

        for i, task in enumerate(tasks):
            task.priority = new_priorities[i]
        task_ids = [rt.eq_task_id for rt in tasks]
        ft = self.fx.submit(_update_priorities, task_ids, new_priorities)
        ft.result()

    def shutdown(self):
        pass
