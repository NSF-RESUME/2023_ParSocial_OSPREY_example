import argparse
import yaml
from typing import Dict, List
import numpy as np
import json
import time

from sklearn.gaussian_process import GaussianProcessRegressor, kernels
from sklearn.preprocessing import MinMaxScaler
from sklearn.pipeline import Pipeline
import scipy

from eqsql import eq
import launch


def reprioritize_queue(training_data: List[List],
                       pred_data: List[np.array],
                       gpr: GaussianProcessRegressor,
                       opt_delay: float = 0) -> np.ndarray:
    """Determine an optimal order in which to excecute a task queue
    
    Args:
        database: Inputs and outputs of completed simulations
        gpr: Gaussian-process regression model
        queue: Existing task queue
        opt_delay: Minimum run time of this function
    Returns:
        Re-ordered priorities of queue
    """
    
    time.sleep(opt_delay)

    # Update the GPR with the available training data
    train_X, train_y = zip(*training_data)
    gpr.fit(np.vstack(train_X), train_y)
    
    # Run GPR on the existing task queue
    pred_y, pred_std = gpr.predict(pred_data, return_std=True)
    best_so_far = np.min(train_y)
    # MB: FIXED
    # ei = (best_so_far - pred_y) / pred_std
    ei = (best_so_far - pred_y) * scipy.stats.norm(0,1).cdf((best_so_far - pred_y) / pred_std) + pred_std * scipy.stats.norm(0,1).pdf((best_so_far - pred_y) / pred_std)

    # Argument sort the EI score, ordered with largest tasks first
    return np.argsort(-1 * ei)


def reprioritize(database: Dict[int, List]):
    completed = [x[1:] for x in filter(lambda x: x[2] is not None, database.values())]
    uncompleted = [x[:2] for x in filter(lambda x: x[2] is None, database.values())]
    gpr = Pipeline([
            ('scale', MinMaxScaler(feature_range=(-1, 1))),
            ('gpr', GaussianProcessRegressor(normalize_y=True, kernel=kernels.RBF() * kernels.ConstantKernel()))
    ])
    # x1 is input array
    new_order = reprioritize_queue(completed, [x[1] for x in uncompleted], gpr=gpr)
    # x0 is the future, new_order is list of np.int64 which psycopg2 can't adapt
    fts = []
    priorities = []
    max_priority = len(uncompleted)
    for i, idx in enumerate(new_order):
        ft = uncompleted[idx][0]
        priority = max_priority - i
        fts.append(ft)
        priorities.append(priority)

    eq.update_priority(fts, priorities)
    

def submit_initial_tasks(task_eq: eq.EQSQL, params: Dict):
    search_space_size = params['search_space_size']
    dim = params['sample_dimensions']
    sampled_space = np.random.uniform(size=(search_space_size, dim), low=-32.768, high=32.768)
    
    exp_id = params['exp_id']
    task_type = params['task_types']['sim']
    mean_rt = params['runtime']
    std_rt = params['runtime_var']
    database = {}
    print("SUBMITTING", flush=True)
    for sample in sampled_space:
        payload = json.dumps({'x': list(sample), 'mean_rt': mean_rt, 'std_rt': std_rt})
        _, ft = task_eq.submit_task(exp_id, task_type, payload)
        database[ft.eq_task_id] = [ft, sample, None]

    print("SUBMITTED", flush=False)

    return database


def start_dbs(params: Dict):
    task_eqs = {}
    launch.launch_dbs(params)
    for db in params['dbs']:
        name = db['name']
        if 'tunnel' in db:
            port = db['tunnel']['port'] 
            # port = 11219
            host = 'localhost'
        else:
            port = db['db_port']
            host = db['db_host']

        print(port)
        task_eq = eq.init_eqsql(host, db['db_user'], port, db['db_name'])
        task_eqs[name] = task_eq

        if not task_eq.are_queues_empty():
            print(f"{name} DB queues are not empty. Exiting ...")
            for task_eq in task_eqs.values():
                task_eq.close()
            return
    
    return task_eqs


def run(exp_id, params: Dict):
    task_eqs = start_dbs(params)
    if task_eqs is None:
        launch.stop_dbs(params)
        return
    
    # only one db currently so get the associated task_eq
    task_eq = list(task_eqs.values())[0]
    
    try:
        database = submit_initial_tasks(task_eq, params)
        # launch after submitting so pool has full data
        pool_data = launch.launch_worker_pools(exp_id, params)
        num_guesses = params['num_guesses']
        retrain_after = params['retrain_after']
        next_retrain = retrain_after
        tasks_completed = 0
        fts = [v[0] for _, v in database.items()]
        print(f'NUM GUESSES: {num_guesses}')
        print(f'RETRAIN AFTER: {retrain_after}')
        print(f'FTS: {len(fts)}')
        while tasks_completed < num_guesses:
            ft = eq.pop_completed(fts)
            _, result = ft.result()
            database[ft.eq_task_id][2] = float(result)
            tasks_completed += 1
            print(f"tasks completed: {tasks_completed}")

            if tasks_completed == next_retrain:
                reprioritize(database)
                next_retrain += retrain_after
                print(f'New retrain after: {retrain_after}')

    finally:
        for task_eq in task_eqs.values():
            task_eq.close()
        launch.stop_worker_pools(pool_data)
        launch.stop_dbs(params)


def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('exp_id', help='experiment id')
    parser.add_argument('config_file', help="yaml format configuration file")
    return parser


if __name__ == '__main__':
    parser = create_parser()
    args = parser.parse_args()
    with open(args.config_file) as fin:
        params = yaml.safe_load(fin)

    # launch.launch_dbs(params)
    # launch.launch_worker_pools(args.exp_id, params)
    # launch.stop_dbs(params)
    
    run(args.exp_id, params)

