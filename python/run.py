import argparse
import yaml
from typing import Dict, List, Union
import subprocess
import threading
import numpy as np
import json
import time

from sklearn.gaussian_process import GaussianProcessRegressor, kernels
from sklearn.preprocessing import MinMaxScaler
from sklearn.pipeline import Pipeline
import scipy

from eqsql import eq


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


def launch_worker_pools(params: Dict):
    # TODO: remote pools
    # launch local pool
    try:
        subprocess.run(['bash', params['start_pool_script'], "1", params['pool_cfg']], check=True)
    except subprocess.CalledProcessError as e:
            print(e, flush=True)

def start_db(params: Dict):
    # TODO: remove DB
    # local DB - no need to start
    pass


def submit_initial_tasks(task_eq: eq.EQSQL, params: Dict):
    search_space_size = params['search_space_size']
    dim = params['sample_dimensions']
    sampled_space = np.random.uniform(size=(search_space_size, dim), low=-32.768, high=32.768)
    
    exp_id = params['exp_id']
    task_type = params['pool_task_type']
    mean_rt = params['runtime']
    std_rt = params['runtime_var']
    database = {}
    for sample in sampled_space:
        payload = json.dumps({'x': list(sample), 'mean_rt': mean_rt, 'std_rt': std_rt})
        _, ft = task_eq.submit_task(exp_id, task_type, payload)
        database[ft.eq_task_id] = [ft, sample, None]

    return database


def run(params: Dict):
    start_db(params)
    task_eq = eq.init_eqsql(params['db_host'], params['db_user'], params['db_port'], 
                            params['db_name'])

    if not task_eq.are_queues_empty():
        print("Queues are not empty. Exiting ...")
        task_eq.close()
        return

    # use thread locally so call returns
    t = threading.Thread(target=launch_worker_pools, args=(params,))
    t.start()

    try:
        database = submit_initial_tasks(task_eq, params)
        num_guesses = params['num_guesses']
        retrain_after = params['retrain_after']
        tasks_completed = 0
        fts = [v[0] for _, v in database.items()]
        while tasks_completed < num_guesses:
            ft = eq.pop_completed(fts)
            _, result = ft.result()
            database[ft.eq_task_id][2] = float(result)
            tasks_completed += 1

            if tasks_completed == retrain_after:
                reprioritize(database)
                retrain_after += tasks_completed
                print(f'New retrain after: {retrain_after}')

    finally:
        task_eq.stop_worker_pool(params['pool_task_type'])
        task_eq.close()

    t.join()


def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('config_file', help="yaml format configuration file")
    return parser


if __name__ == '__main__':
    parser = create_parser()
    args = parser.parse_args()
    with open(args.config_file) as fin:
        params = yaml.safe_load(fin)
    
    run(params)

